"""DOCX export (python-docx)."""
from __future__ import annotations

import io
import time

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from .mdparse import parse_blocks, runs

LAV = RGBColor(0x6B, 0x57, 0xD6)
INK = RGBColor(0x24, 0x1F, 0x3F)


def _add_runs(par, text: str, size: float | None = None) -> None:
    for r in runs(text):
        run = par.add_run(r["text"])
        run.bold = r["b"] or None
        run.italic = r["i"] or None
        if size:
            run.font.size = Pt(size)
        if r.get("cite"):
            run.font.size = Pt(7.5)
            run.font.color.rgb = LAV
        if r.get("link"):
            run.font.color.rgb = RGBColor(0x4C, 0x6F, 0xD1)
            run.underline = True


def _shade(cell, hex_fill: str) -> None:
    tcPr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_fill)
    tcPr.append(shd)


def _blocks(doc, md: str, skip_h1: bool = False) -> None:
    for b in parse_blocks(md):
        t = b["t"]
        if t == "h":
            if b["level"] == 1 and skip_h1:
                continue
            h = doc.add_heading(level=min(max(b["level"], 1) + 1, 4))
            _add_runs(h, b["text"])
        elif t == "p":
            p = doc.add_paragraph()
            _add_runs(p, b["text"])
        elif t in ("ul", "ol"):
            for it in b["items"]:
                style = ("List Bullet" if t == "ul" else "List Number") + (" 2" if it["level"] else "")
                p = doc.add_paragraph(style=style)
                _add_runs(p, it["text"])
        elif t == "quote":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(14)
            _add_runs(p, b["text"], 10)
            for r in p.runs:
                r.italic = True
        elif t == "table":
            tb = doc.add_table(rows=1, cols=len(b["head"]))
            tb.style = "Table Grid"
            for i, h in enumerate(b["head"]):
                c = tb.rows[0].cells[i]
                c.paragraphs[0].text = ""
                _add_runs(c.paragraphs[0], f"**{h}**" if h else h, 9)
                _shade(c, "EFEBFF")
            for row in b["rows"]:
                cells = tb.add_row().cells
                for i, v in enumerate(row):
                    _add_runs(cells[i].paragraphs[0], v, 9)
            doc.add_paragraph()


def build_docx(report: dict) -> bytes:
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    for lvl, size in ((1, 20), (2, 16), (3, 13), (4, 11.5)):
        hs = doc.styles[f"Heading {lvl}"]
        hs.font.name = "Calibri"
        hs.font.size = Pt(size)
        hs.font.color.rgb = LAV if lvl <= 2 else INK
    doc.core_properties.title = report["title"]
    doc.core_properties.author = "CONVERGE AI"

    k = doc.add_paragraph()
    r = k.add_run("CONVERGE AI · RESEARCH SYNTHESIS")
    r.font.size, r.bold, r.font.color.rgb = Pt(9), True, LAV
    t = doc.add_paragraph()
    tr = t.add_run(report["title"])
    tr.font.size, tr.bold = Pt(28), True
    if report.get("description"):
        d = doc.add_paragraph(report["description"])
        d.runs[0].italic = True
    gen = time.strftime("%d %B %Y", time.localtime(report["generated_at"]))
    m = report["mode"]
    doc.add_paragraph(f"{len(report['sources'])} sources · generated {gen} · "
                      f"{'LLM-assisted synthesis' if m['synthesis'] == 'llm' else 'offline extractive synthesis'}")
    doc.add_heading("Contents", level=2)
    for s in report["sections"]:
        doc.add_paragraph(f"{s['number']}. {s['title']}", style="List Number" if False else None)
    doc.add_page_break()
    for sec in report["sections"]:
        doc.add_heading(f"{sec['number']}. {sec['title']}", level=1)
        _blocks(doc, sec["markdown"], skip_h1=(sec["number"] == 1))
    if report.get("warnings"):
        doc.add_heading("Processing notes", level=2)
        for w in report["warnings"][:15]:
            _add_runs(doc.add_paragraph(style="List Bullet"), w)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
