"""Tiny Markdown subset parser shared by the PDF and DOCX exporters (headings, paragraphs, lists, tables, inline)."""
from __future__ import annotations

import re


def parse_blocks(md: str) -> list[dict]:
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[dict] = []
    i = 0
    para: list[str] = []

    def flush():
        if para:
            out.append({"t": "p", "text": " ".join(s.strip() for s in para)})
            para.clear()

    while i < len(lines):
        ln = lines[i]
        if not ln.strip():
            flush()
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            flush()
            out.append({"t": "h", "level": len(m.group(1)), "text": m.group(2).strip()})
            i += 1
            continue
        if ln.lstrip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush()
            def cells(s): return [c.strip() for c in s.strip().strip("|").split("|")]
            head = cells(ln)
            i += 2
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                r = cells(lines[i])
                rows.append((r + [""] * len(head))[: len(head)])
                i += 1
            out.append({"t": "table", "head": head, "rows": rows})
            continue
        m = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.*)$", ln)
        if m:
            flush()
            items = []
            ordered = m.group(2)[0].isdigit()
            while i < len(lines):
                m2 = re.match(r"^(\s*)([-*•]|\d+[.)])\s+(.*)$", lines[i])
                if not m2:
                    # continuation line of the previous item
                    if lines[i].strip() and lines[i].startswith("  ") and items:
                        items[-1]["text"] += " " + lines[i].strip()
                        i += 1
                        continue
                    break
                items.append({"level": len(m2.group(1)) // 2, "text": m2.group(3).strip()})
                i += 1
            out.append({"t": "ol" if ordered else "ul", "items": items})
            continue
        if ln.startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].startswith(">"):
                q.append(lines[i].lstrip("> ").strip())
                i += 1
            out.append({"t": "quote", "text": " ".join(q)})
            continue
        para.append(ln)
        i += 1
    flush()
    return out


_TOK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)|\*\*(.+?)\*\*|(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])")


def runs(text: str, b: bool = False, i: bool = False) -> list[dict]:
    """Inline markup -> list of {text, b, i, cite, link}."""
    out: list[dict] = []
    pos = 0
    for m in _TOK.finditer(text):
        if m.start() > pos:
            out.append({"text": text[pos:m.start()], "b": b, "i": i})
        if m.group(1) is not None:
            href = m.group(2)
            if href.startswith("cite:"):
                out.append({"text": f"[{m.group(1)}]", "b": False, "i": False, "cite": True})
            else:
                out.append({"text": m.group(1), "b": b, "i": i, "link": href})
        elif m.group(3) is not None:
            out += runs(m.group(3), True, i)
        else:
            out += runs(m.group(4), b, True)
        pos = m.end()
    if pos < len(text):
        out.append({"text": text[pos:], "b": b, "i": i})
    return out


def plain(text: str) -> str:
    return "".join(r["text"] for r in runs(text))


def to_plain_markdown(md: str) -> str:
    """Markdown with citation links collapsed to [S1 · p. 3]."""
    return re.sub(r"\[([^\]]+)\]\(cite:[^)]*\)", r"[\1]", md)
