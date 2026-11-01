"""PDF export (ReportLab) with cover, table of contents, styled tables and page numbers."""
from __future__ import annotations

import io
import os
import time
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, PageBreak, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from .mdparse import parse_blocks, runs

LAV, LAV_L, INK, MUTED, ACC = "#6b57d6", "#f1eeff", "#241f3f", "#6b6788", "#4c6fd1"


def _fonts() -> tuple[str, str, str]:
    cands = [("/usr/share/fonts/truetype/dejavu", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
             ("/usr/share/fonts/dejavu", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf"),
             ("/Library/Fonts", "Arial Unicode.ttf", "Arial Unicode.ttf", "Arial Unicode.ttf"),
             ("C:/Windows/Fonts", "arial.ttf", "arialbd.ttf", "ariali.ttf")]
    for d, r, b, i in cands:
        if all(os.path.exists(os.path.join(d, f)) for f in (r, b, i)):
            pdfmetrics.registerFont(TTFont("CV", os.path.join(d, r)))
            pdfmetrics.registerFont(TTFont("CV-B", os.path.join(d, b)))
            pdfmetrics.registerFont(TTFont("CV-I", os.path.join(d, i)))
            pdfmetrics.registerFontFamily("CV", normal="CV", bold="CV-B", italic="CV-I", boldItalic="CV-B")
            return "CV", "CV-B", "CV-I"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


def _markup(text: str) -> str:
    out = []
    for r in runs(text):
        t = escape(r["text"])
        if r.get("cite"):
            out.append(f'<font color="{LAV}" size="7">{t}</font>')
            continue
        if r.get("link"):
            t = f'<link href="{escape(r["link"])}" color="{ACC}">{t}</link>'
        if r["b"]:
            t = f"<b>{t}</b>"
        if r["i"]:
            t = f"<i>{t}</i>"
        out.append(t)
    return "".join(out)


class _Doc(BaseDocTemplate):
    def __init__(self, buf, title):
        super().__init__(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=20 * mm,
                         title=title, author="CONVERGE AI")
        fr = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="f")
        self.addPageTemplates([PageTemplate(id="p", frames=[fr], onPage=self._footer)])
        self.fb = "CV"

    def _footer(self, canv, doc):
        canv.saveState()
        canv.setFont(self.fb, 7.5)
        canv.setFillColor(colors.HexColor(MUTED))
        canv.drawString(20 * mm, 11 * mm, "CONVERGE AI · multi-source knowledge synthesis")
        canv.drawRightString(A4[0] - 20 * mm, 11 * mm, f"Page {doc.page}")
        canv.setStrokeColor(colors.HexColor("#e4dffa"))
        canv.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        canv.restoreState()

    def afterFlowable(self, fl):
        if isinstance(fl, Paragraph) and fl.style.name == "H1x":
            key = f"h{id(fl)}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (0, fl.getPlainText(), self.page, key))


def build_pdf(report: dict) -> bytes:
    f, fb, fi = _fonts()
    S = {
        "title": ParagraphStyle("title", fontName=fb, fontSize=26, leading=31, textColor=colors.HexColor(INK), spaceAfter=8),
        "h1": ParagraphStyle("H1x", fontName=fb, fontSize=17, leading=21, textColor=colors.HexColor(LAV), spaceBefore=14, spaceAfter=7),
        "h2": ParagraphStyle("h2", fontName=fb, fontSize=13, leading=17, textColor=colors.HexColor(INK), spaceBefore=11, spaceAfter=4),
        "h3": ParagraphStyle("h3", fontName=fb, fontSize=11, leading=15, textColor=colors.HexColor(INK), spaceBefore=8, spaceAfter=3),
        "p": ParagraphStyle("p", fontName=f, fontSize=9.6, leading=14.2, textColor=colors.HexColor(INK), spaceAfter=6, alignment=TA_LEFT),
        "li": ParagraphStyle("li", fontName=f, fontSize=9.6, leading=14, textColor=colors.HexColor(INK), spaceAfter=2.5),
        "q": ParagraphStyle("q", fontName=fi, fontSize=9.4, leading=13.5, textColor=colors.HexColor("#4a3fa3"), leftIndent=8, spaceAfter=6,
                            backColor=colors.HexColor(LAV_L), borderPadding=(5, 6, 5, 6)),
        "cell": ParagraphStyle("cell", fontName=f, fontSize=8.2, leading=11, textColor=colors.HexColor(INK)),
        "cellh": ParagraphStyle("cellh", fontName=fb, fontSize=8.2, leading=11, textColor=colors.HexColor(INK)),
        "muted": ParagraphStyle("muted", fontName=f, fontSize=9, leading=13, textColor=colors.HexColor(MUTED)),
    }
    buf = io.BytesIO()
    doc = _Doc(buf, report["title"])
    doc.fb = f
    width = doc.width

    def blocks_to_flow(md: str, skip_h1: bool = False) -> list:
        fl: list = []
        for b in parse_blocks(md):
            t = b["t"]
            if t == "h":
                if b["level"] == 1:
                    if skip_h1:
                        continue
                    fl.append(Paragraph(_markup(b["text"]), S["h2"]))
                else:
                    fl.append(Paragraph(_markup(b["text"]), S["h2"] if b["level"] == 2 else S["h3"]))
            elif t == "p":
                fl.append(Paragraph(_markup(b["text"]), S["p"]))
            elif t in ("ul", "ol"):
                n = 0
                for it in b["items"]:
                    n += 1 if it["level"] == 0 else 0
                    bullet = "•" if t == "ul" else f"{n}."
                    if it["level"]:
                        bullet = "–"
                    fl.append(Paragraph(_markup(it["text"]), ParagraphStyle("li2", parent=S["li"], leftIndent=14 + 14 * it["level"] + 4,
                                                                           bulletIndent=4 + 14 * it["level"], bulletFontName=f),
                                        bulletText=bullet))
                fl.append(Spacer(1, 4))
            elif t == "quote":
                fl.append(Paragraph(_markup(b["text"]), S["q"]))
            elif t == "table":
                ncol = len(b["head"])
                lens = [max([len(b["head"][c])] + [len(r[c]) for r in b["rows"]]) for c in range(ncol)]
                w = [min(max(x, 8), 70) ** 0.8 for x in lens]
                tot = sum(w)
                data = [[Paragraph(_markup(h), S["cellh"]) for h in b["head"]]] + \
                       [[Paragraph(_markup(c), S["cell"]) for c in r] for r in b["rows"]]
                tb = Table(data, colWidths=[width * x / tot for x in w], repeatRows=1)
                tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(LAV_L)),
                                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#ddd8f4")),
                                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfaff")]),
                                        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
                fl += [tb, Spacer(1, 7)]
        return fl

    story: list = []
    gen = time.strftime("%d %B %Y", time.localtime(report["generated_at"]))
    mode = report["mode"]
    story += [Spacer(1, 30 * mm), Paragraph("CONVERGE AI · RESEARCH SYNTHESIS", ParagraphStyle("k", fontName=fb, fontSize=9, textColor=colors.HexColor(LAV), spaceAfter=10)),
              Paragraph(escape(report["title"]), S["title"])]
    if report.get("description"):
        story.append(Paragraph(escape(report["description"]), S["muted"]))
    story += [Spacer(1, 6),
              Paragraph(f"{len(report['sources'])} sources · generated {gen} · "
                        f"{'LLM-assisted synthesis' if mode['synthesis'] == 'llm' else 'offline extractive synthesis'}", S["muted"]),
              Spacer(1, 14)]
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle("t0", fontName=f, fontSize=10, leading=17, textColor=colors.HexColor(INK))]
    story += [Paragraph("Contents", S["h2"]), toc, PageBreak()]
    for sec in report["sections"]:
        if sec["number"] == 1:
            story.append(Paragraph(f"{sec['number']}. {sec['title']}", S["h1"]))
            story += blocks_to_flow(sec["markdown"], skip_h1=True)
        else:
            story.append(Paragraph(f"{sec['number']}. {sec['title']}", S["h1"]))
            story += blocks_to_flow(sec["markdown"])
    if report.get("warnings"):
        story.append(Paragraph("Processing notes", S["h2"]))
        for w in report["warnings"][:15]:
            story.append(Paragraph(_markup(w), S["li"], bulletText="•"))
    doc.multiBuild(story)
    return buf.getvalue()
