"""Lightweight, dependency-free text analysis used by the offline fallback and as signals for the LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass

_ABBR = ["e.g.", "i.e.", "vs.", "etc.", "Fig.", "Figs.", "Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "approx.",
         "et al.", "No.", "Inc.", "Ltd.", "St.", "cf.", "U.S.", "U.K.", "Eq.", "Ref."]
_DOT = "․"


def split_sentences(text: str) -> list[str]:
    t = text
    for a in _ABBR:
        t = t.replace(a, a.replace(".", _DOT))
    t = re.sub(r"(?<=\b[A-Z])\.(?=[A-Z]\b)", _DOT, t)
    parts = re.split(r"(?<=[.!?])[\"”')\]]*\s+(?=[\"“(\[]?[A-Z0-9])", t)
    return [p.replace(_DOT, ".").strip() for p in parts if p.strip()]


STOP = set("""a an the and or of to in on for with by as at from is are was were be been being it its this that these those
which who whom whose can could may might will would should has have had do does did not no than then so such also into over
under about between among per via both each more most other some any all their there they them we our you your i he she his
her but if while when where what how however thus therefore only very often typically generally many much one two three""".split())

_SUFFIXES = ("ations", "ation", "ingly", "edly", "ings", "ing", "ies", "ed", "es", "ly", "s")


def stem(w: str) -> str:
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)] + ("y" if suf == "ies" else "")
    return w


def words(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z\-]+|\d[\d,\.]*", text.lower())


def content_set(text: str, include_numbers: bool = True) -> set[str]:
    out = set()
    for w in words(text):
        if w[0].isdigit():
            if include_numbers:
                out.add(w.rstrip(".,"))
            continue
        if w in STOP or len(w) < 3:
            continue
        out.add(stem(w))
    return out


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def novelty(text: str, known: set[str]) -> float:
    """Fraction of this text's content stems not already present in `known`."""
    s = content_set(text)
    return len(s - known) / len(s) if s else 0.0


# ---- numeric facts -------------------------------------------------------
_NUM = r"\d[\d,]*(?:\.\d+)?"
_UNIT = (r"(?:%|percent|per ?cent|wh/kg|wh/l|wh/litre|kwh|gwh|twh|mah|cycles?|years?|months?|days?|hours?|hrs?|"
         r"minutes?|mins?|°\s?c|volts?|km|kg|tonnes?|tons?|gw|mw|kw|c-rate)")
_FACT = re.compile(rf"(?P<cur>[$€£])?\s?(?P<lo>{_NUM})(?:\s?(?:-|–|—|to)\s?(?P<hi>{_NUM}))?\s?"
                   rf"(?P<scale>billion|million|thousand|bn)?\s?(?P<unit>{_UNIT})?(?![\w])", re.I)
_SCALE = {"billion": 1e9, "bn": 1e9, "million": 1e6, "thousand": 1e3}


@dataclass
class Fact:
    lo: float
    hi: float
    unit: str
    raw: str

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2


def _canon_unit(u: str) -> str:
    u = u.lower().replace(" ", "")
    if u in ("percent", "percent", "%") or u.startswith("per") and u.endswith("cent"):
        return "%"
    if u.startswith("percent"):
        return "%"
    u = {"wh/litre": "wh/l", "hrs": "hours", "hr": "hours", "mins": "minutes", "min": "minutes"}.get(u, u)
    if u.endswith("s") and u[:-1] in ("cycle", "year", "month", "day", "hour", "minute", "tonne", "ton", "volt"):
        u = u[:-1]
    return "ton" if u == "tonne" else u


def numeric_facts(text: str) -> list[Fact]:
    facts: list[Fact] = []
    for m in _FACT.finditer(text):
        try:
            lo = float(m.group("lo").replace(",", ""))
            hi = float(m.group("hi").replace(",", "")) if m.group("hi") else lo
        except ValueError:
            continue
        scale = _SCALE.get((m.group("scale") or "").lower(), 1.0)
        lo, hi = lo * scale, hi * scale
        unit = _canon_unit(m.group("unit")) if m.group("unit") else ""
        cur = m.group("cur") or ""
        tail = text[m.end(): m.end() + 12].lower()
        if cur:
            mm = re.match(r"\s*(?:/|per\s)\s*(kwh|kg|ton|tonne)", tail)
            unit = f"{cur}/{mm.group(1)}" if mm else cur + (unit if unit else "")
        if not unit and not m.group("scale") and 1900 <= lo <= 2100 and lo == hi and "." not in m.group("lo"):
            unit = "year"
        if unit == "year" and re.search(r"\d,\d{3}", m.group("lo")):
            continue
        if not unit and not m.group("scale"):
            continue  # plain integers (list counts, ids) are not comparable facts
        facts.append(Fact(lo, hi, unit, m.group(0).strip()))
    return facts


def is_stat(text: str) -> bool:
    return any(f.unit and f.unit != "year" for f in numeric_facts(text))


_NEG = re.compile(r"\b(not|no longer|never|cannot|can't|isn't|aren't|won't|unlikely|without|fails? to|none|neither|nor|no)\b", re.I)
_ANTONYMS = [("increase", "decrease"), ("increases", "decreases"), ("higher", "lower"), ("faster", "slower"),
             ("improve", "degrade"), ("improves", "worsens"), ("safer", "riskier"), ("more", "less"), ("cheaper", "costlier"),
             ("rise", "fall"), ("rises", "falls"), ("expand", "shrink"), ("longer", "shorter"), ("greater", "smaller"),
             ("beneficial", "harmful"), ("effective", "ineffective"), ("safe", "unsafe")]


def _overlap(a: Fact, b: Fact, tol: float = 0.03) -> bool:
    pad = 0.5 if a.unit == "year" else tol * max(abs(a.mid), abs(b.mid), 1e-9)
    return a.lo - pad <= b.hi and b.lo - pad <= a.hi


def detect_conflict(a: str, b: str) -> dict | None:
    """Heuristic only: flags *potential* disagreements for human/LLM review."""
    ca, cb = content_set(a, False), content_set(b, False)
    j = jaccard(ca, cb)
    fa, fb = numeric_facts(a), numeric_facts(b)
    for unit in {f.unit for f in fa} & {f.unit for f in fb}:
        xa = [f for f in fa if f.unit == unit]
        xb = [f for f in fb if f.unit == unit]
        if any(_overlap(x, y) for x in xa for y in xb):
            continue
        need = 0.30 if unit == "year" else 0.18
        if j >= need:
            x = min(xa, key=lambda f: min(abs(f.mid - y.mid) for y in xb))
            y = min(xb, key=lambda f: abs(f.mid - x.mid))
            return {"kind": "numeric", "a": x.raw, "b": y.raw, "unit": unit}
    if j >= 0.45:
        if bool(_NEG.search(a)) != bool(_NEG.search(b)):
            return {"kind": "polarity"}
        la, lb = set(words(a)), set(words(b))
        for p, q in _ANTONYMS:
            if (p in la and q in lb and p not in lb) or (q in la and p in lb and q not in lb):
                return {"kind": "antonym", "a": p, "b": q}
    return None


# ---- cheap content-type cues ---------------------------------------------
_DEF = re.compile(r"\b(is|are) (a|an|the)\b.{0,80}\b(that|which|used|of|for)\b|\brefers? to\b|\bis defined as\b|\bdefined as\b|"
                  r"\bknown as\b|\bis called\b|\bmeans\b|\bstands for\b|\bconsists? of\b|\bis the (process|ability|measure|amount)\b", re.I)
_EXAMPLE = re.compile(r"\b(for example|for instance|e\.g\.|such as|case study|an example|in practice|consider|including)\b", re.I)
_APPLIC = re.compile(r"\b(used (in|for|to)|application|applications|powers?|deployed|adopted|enables?|vehicles?|grid|"
                     r"industr(y|ies)|commercial|manufactur\w+|implementation|use case)\b", re.I)
_FUTURE = re.compile(r"\b(future|will|expected to|projected|forecast|by 20\d\d|before 20\d\d|roadmap|next generation|"
                     r"upcoming|promising|potential|aim|target|plans? to|could)\b", re.I)
_TECH = re.compile(r"\b(mechanism|reaction|electrolyte|anode|cathode|voltage|algorithm|protocol|architecture|process|"
                   r"temperature|density|capacity|efficiency|parameter|ratio|material|chemistry|layer|interface|equation)\b", re.I)


def is_definition(t: str) -> bool:
    return bool(_DEF.search(t))


def is_example(t: str) -> bool:
    return bool(_EXAMPLE.search(t))


def is_application(t: str) -> bool:
    return bool(_APPLIC.search(t))


def is_future(t: str) -> bool:
    return bool(_FUTURE.search(t))


def is_technical(t: str) -> bool:
    return bool(_TECH.search(t)) or is_stat(t)


def shorten(t: str, n: int = 160) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= n else t[: n - 1].rsplit(" ", 1)[0] + "…"
