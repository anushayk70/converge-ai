"""Shared types for all extractors."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


class ExtractionError(Exception):
    """A problem the user can understand and act on (shown verbatim in the UI)."""


@dataclass
class Segment:
    text: str
    page: int | None = None
    t_start: float | None = None      # seconds, for audio/video
    section: str = ""                 # nearest heading
    kind: str = "text"                # text | ocr | transcript | vision | table


@dataclass
class Extraction:
    segments: list[Segment]
    warnings: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


_LIG = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl",
        "­": "", " ": " ", "​": "", "﻿": ""}


def clean_text(t: str) -> str:
    """Normalise whitespace, ligatures and line-wrap hyphenation inside one paragraph."""
    for a, b in _LIG.items():
        t = t.replace(a, b)
    t = re.sub(r"(\w)-\s*\n\s*([a-z])", r"\1\2", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t.strip()
