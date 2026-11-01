"""Normalise extracted segments and split them into small, citable knowledge units (1-3 sentences)."""
from __future__ import annotations

import re

from ..db import new_id
from ..extractors.base import Segment
from .textutil import split_sentences, words

TARGET, MAXC, MINC = 260, 430, 50


def fmt_time(t: float) -> str:
    t = int(t)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def locator(page, t_start, section: str, idx: int) -> str:
    if page:
        return f"p. {page}"
    if t_start is not None:
        return fmt_time(t_start)
    if section:
        s = re.sub(r"\s+", " ", section).strip()
        return "§ " + (s if len(s) <= 36 else s[:35].rstrip() + "…")
    return f"¶{idx + 1}"


def _merge_short(segs: list[Segment]) -> list[Segment]:
    """Join consecutive short fragments (bullets, table cells) from the same place."""
    out: list[Segment] = []
    for s in segs:
        p = out[-1] if out else None
        if (p and len(p.text) < 130 and len(s.text) < 130 and p.page == s.page and p.section == s.section
                and p.kind == s.kind and p.t_start == s.t_start and len(p.text) + len(s.text) < TARGET):
            sep = " " if p.text.endswith((".", "!", "?")) else "; "
            out[-1] = Segment(p.text.rstrip(";") + sep + s.text, p.page, p.t_start, p.section, p.kind)
        else:
            out.append(s)
    return out


def _good(t: str) -> bool:
    letters = sum(c.isalpha() for c in t)
    return len(t) >= 25 and len(words(t)) >= 4 and letters / max(len(t), 1) >= 0.5


def make_units(segments: list[Segment]) -> list[dict]:
    raw: list[dict] = []
    for seg in _merge_short(segments):
        sents = split_sentences(seg.text)
        chunks: list[str] = []
        buf: list[str] = []
        for s in sents:
            cur = " ".join(buf)
            if buf and len(cur) + len(s) + 1 > TARGET:
                chunks.append(cur)
                buf = []
            buf.append(s)
            if len(" ".join(buf)) >= MAXC:
                chunks.append(" ".join(buf))
                buf = []
        if buf:
            tail = " ".join(buf)
            if chunks and len(tail) < MINC and len(chunks[-1]) + len(tail) < MAXC + 200:
                chunks[-1] += " " + tail
            else:
                chunks.append(tail)
        for c in chunks:
            raw.append({"text": c, "page": seg.page, "t_start": seg.t_start, "ctx": seg.section, "kind": seg.kind})

    units, seen = [], set()
    for r in raw:
        t = r["text"].strip()
        key = re.sub(r"\W+", " ", t.lower()).strip()
        if not _good(t) or key in seen:
            continue
        seen.add(key)
        idx = len(units)
        units.append({"id": new_id("u_"), "idx": idx, "text": t, "ctx": r["ctx"],
                      "label": locator(r["page"], r["t_start"], r["ctx"], idx),
                      "page": r["page"], "t_start": r["t_start"], "kind": r["kind"]})
    return units
