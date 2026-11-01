"""PDF, DOCX, TXT and HTML extraction. Each segment keeps page / section so citations can point back."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ..config import settings
from .base import Extraction, ExtractionError, Segment, clean_text
from .ocr import ocr_available, ocr_image, ocr_status

_SENT_END = re.compile(r"[.!?:;\"”')\]]$")


def _looks_like_heading(p: str) -> bool:
    w = p.split()
    return 0 < len(w) <= 10 and len(p) <= 90 and not _SENT_END.search(p) and not p[:1].islower()


def _pdf_lines(page) -> list[dict]:
    """Text lines with font size / boldness / block id (so headings can be told apart from body text)."""
    out = []
    for bi, blk in enumerate(page.get_text("dict", sort=True)["blocks"]):
        if blk.get("type") != 0:
            continue
        for ln in blk["lines"]:
            spans = [sp for sp in ln["spans"] if sp["text"].strip()]
            if not spans:
                continue
            out.append({"text": "".join(sp["text"] for sp in spans), "size": max(sp["size"] for sp in spans),
                        "bold": all((sp["flags"] & 16) or "bold" in sp["font"].lower() for sp in spans), "block": bi})
    return out


def extract_pdf(path: Path) -> Extraction:
    try:
        import pymupdf
    except ImportError:  # older PyMuPDF
        import fitz as pymupdf  # type: ignore
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        raise ExtractionError(f"Could not open the PDF (corrupt file?): {e}")
    if doc.needs_pass:
        raise ExtractionError("This PDF is password-protected. Remove the password and upload it again.")

    toc = sorted([(pg, title) for _lvl, title, pg in doc.get_toc() if pg > 0])
    warnings: list[str] = []
    page_lines = [_pdf_lines(doc[i]) for i in range(len(doc))]
    sizes: Counter = Counter()
    for pl in page_lines:
        for l in pl:
            sizes[round(l["size"], 1)] += len(l["text"])
    body = sizes.most_common(1)[0][0] if sizes else 10.0

    pages: list[tuple[int, list[tuple[str, str]], str]] = []   # (page, [(h|p, text)], kind)
    ocr_pages = 0
    for pno, pl in enumerate(page_lines):
        items: list[tuple[str, str]] = []
        kind = "text"
        buf: list[str] = []
        cur_block = None

        def flush():
            if buf:
                items.append(("p", clean_text(" ".join(buf))))
                buf.clear()

        for l in pl:
            t = l["text"].strip()
            words_n = len(t.split())
            is_head = (len(t) <= 100 and not re.search(r"[.!?;,]$", t) and words_n <= 14 and
                       (l["size"] >= body * 1.12 or (l["bold"] and l["size"] >= body * 0.97 and words_n <= 10)))
            if is_head:
                flush()
                if items and items[-1][0] == "h":
                    items[-1] = ("h", items[-1][1] + " " + t)
                else:
                    items.append(("h", t))
                cur_block = None
                continue
            if cur_block is not None and l["block"] != cur_block:
                flush()
            cur_block = l["block"]
            buf.append(l["text"])
        flush()
        if sum(len(t) for k, t in items if k == "p") < 40 and settings.ocr_enabled and ocr_available():
            try:
                import io
                from PIL import Image
                pix = doc[pno].get_pixmap(dpi=200)
                txt, conf = ocr_image(Image.open(io.BytesIO(pix.tobytes("png"))))
                if txt:
                    items = []
                    for para in [clean_text(x) for x in re.split(r"\n\s*\n", txt) if x.strip()]:
                        items.append(("h" if _looks_like_heading(para) else "p", para))
                    kind = "ocr"
                    ocr_pages += 1
                    if conf < 60:
                        warnings.append(f"Page {pno + 1}: low OCR confidence ({conf:.0f}%) - text may contain errors.")
            except Exception as e:
                warnings.append(f"Page {pno + 1}: OCR failed ({e}).")
        pages.append((pno + 1, items, kind))

    # drop running headers/footers and bare page numbers
    n = len(pages)
    norm = lambda s: re.sub(r"\d+", "#", s.lower())
    counts = Counter(norm(t) for _pg, its, _k in pages for t in {t for _k2, t in its} if len(t) < 160)
    repeated = {k for k, c in counts.items() if n >= 3 and c >= max(3, 0.5 * n)}

    segs: list[Segment] = []
    for pg, its, kind in pages:
        section = ""
        for pgno, title in toc:
            if pgno <= pg:
                section = title
        for k, t in its:
            if not t or norm(t) in repeated or re.fullmatch(r"(page\s*)?\d{1,4}(\s*/\s*\d+)?", t.strip().lower()):
                continue
            if k == "h":
                section = t
            else:
                segs.append(Segment(t, page=pg, section=section, kind=kind))
    if ocr_pages:
        warnings.append(f"{ocr_pages} page(s) had no text layer and were read with OCR.")
    if not segs:
        hint = "" if ocr_available() else " " + ocr_status()["message"]
        raise ExtractionError("No readable text found in this PDF (it may be scanned images)." + hint)
    return Extraction(segs, warnings, {"pages": n, "title": (doc.metadata or {}).get("title") or ""})


def extract_docx(path: Path) -> Extraction:
    try:
        import docx
    except ImportError:
        raise ExtractionError("python-docx is not installed (pip install python-docx).")
    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise ExtractionError(f"Could not open the Word document (corrupt or not a .docx file?): {e}")
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        content = list(d.iter_inner_content())
    except AttributeError:  # python-docx < 1.0
        content = list(d.paragraphs)
    segs: list[Segment] = []
    section = ""
    for el in content:
        if isinstance(el, Paragraph):
            txt = clean_text(el.text)
            if not txt:
                continue
            style = (el.style.name if el.style is not None else "") or ""
            if style.lower().startswith(("heading", "title")):
                section = txt
                continue
            segs.append(Segment(txt, section=section))
        elif isinstance(el, Table):
            rows = [[clean_text(c.text) for c in r.cells] for r in el.rows]
            rows = [r for r in rows if any(r)]
            if len(rows) >= 2:
                head = rows[0]
                for r in rows[1:]:
                    cells = [f"{h}: {v}" if h and v else (v or h) for h, v in zip(head, r)]
                    segs.append(Segment("; ".join(c for c in cells if c) + ".", section=section, kind="table"))
            else:
                for r in rows:
                    segs.append(Segment(" | ".join(c for c in r if c), section=section, kind="table"))
    if not segs:
        raise ExtractionError("The Word document contains no text.")
    return Extraction(segs, [], {"title": (d.core_properties.title or "")})


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_txt(path: Path) -> Extraction:
    text = _decode(path.read_bytes()).replace("\r\n", "\n")
    segs: list[Segment] = []
    section = ""
    buf: list[str] = []

    def flush():
        if buf:
            segs.append(Segment(clean_text(" ".join(buf)), section=section))
            buf.clear()

    for line in text.split("\n"):
        if not line.strip():
            flush()
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", line)
        if m:
            flush()
            section = m.group(1).strip()
            continue
        mb = re.match(r"^\s*([-*\u2022]|\d+[.)])\s+(.*)$", line)
        if mb:
            flush()
            segs.append(Segment(clean_text(mb.group(2)), section=section))
            continue
        buf.append(line.strip())
    flush()
    if not segs:
        raise ExtractionError("The text file is empty.")
    return Extraction(segs, [], {})


_NOISE = ["script", "style", "noscript", "nav", "footer", "aside", "form", "header", "iframe", "svg", "button"]


def extract_html(html: str, fallback_title: str = "") -> Extraction:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    title = ""
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        title = og["content"].strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    meta: dict = {"title": title or fallback_title}
    for key, names in (("author", ["author", "article:author"]), ("published", ["article:published_time", "date"])):
        for nm in names:
            tag = soup.find("meta", attrs={"name": nm}) or soup.find("meta", property=nm)
            if tag and tag.get("content"):
                meta[key] = tag["content"].strip()
                break
    for t in soup(_NOISE):
        t.decompose()
    root = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    segs: list[Segment] = []
    section = ""
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "tr", "pre", "figcaption"]):
        name = el.name
        if name in ("h1", "h2", "h3", "h4"):
            section = clean_text(el.get_text(" ", strip=True))
            continue
        if name == "li" and el.find("li"):
            continue
        if name == "p" and el.find_parent(["li", "blockquote", "tr"]):
            continue
        if name == "tr":
            cells = [clean_text(c.get_text(" ", strip=True)) for c in el.find_all(["td", "th"])]
            txt = " | ".join(c for c in cells if c)
        else:
            txt = clean_text(el.get_text(" ", strip=True))
        if len(txt) >= 30 and len(txt.split()) >= 5:
            segs.append(Segment(txt, section=section, kind="table" if name == "tr" else "text"))
    if not segs:
        raise ExtractionError("Could not find article text on this page (it may be rendered by JavaScript, "
                              "paywalled, or empty). Try pasting the text instead.")
    return Extraction(segs, [], meta)


def extract_html_file(path: Path) -> Extraction:
    return extract_html(_decode(path.read_bytes()), path.stem)
