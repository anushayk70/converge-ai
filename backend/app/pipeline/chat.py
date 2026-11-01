"""Cross-source question answering: retrieve across ALL resources, answer with citations, flag disagreements."""
from __future__ import annotations

import hashlib
import re
import threading

import numpy as np

from .. import db
from ..llm import LLMError, llm
from .embeddings import VectorIndex, get_embedder
from .synthesis import Cites, strip_handles
from .analysis import numbers_supported
from .textutil import content_set, split_sentences

_cache: dict[str, dict] = {}
_lock = threading.Lock()

CHAT_SYSTEM = """You are the question-answering engine of CONVERGE AI. Answer the user's question using ONLY the numbered source statements.
- Cite every factual claim with the handles of all supporting statements, e.g. [U3][U8]. Only use given handles.
- Combine information across sources; say which source (S1, S2...) says what when it matters.
- If sources disagree, say so explicitly and attribute each position. Never silently pick one.
- If the statements do not contain the answer, say "The uploaded sources do not contain this information." You may add what is related.
- Do not use outside knowledge. Be concise (under 200 words unless detail is needed). Markdown allowed."""


def _store(wid: str):
    units = db.list_units(wid)
    if not units:
        return None
    sig = hashlib.md5("".join(u["id"] for u in units).encode()).hexdigest()
    with _lock:
        st = _cache.get(wid)
        if st and st["sig"] == sig:
            return st
    emb, note = get_embedder([u["text"] for u in units])
    E = emb.encode([u["text"] for u in units])
    for i, u in enumerate(units):
        u["h"] = f"U{i + 1}"
    st = {"sig": sig, "units": units, "emb": emb, "E": E, "index": VectorIndex(E), "note": note}
    with _lock:
        _cache[wid] = st
    return st


def warm(wid: str, units, emb, E) -> None:
    sig = hashlib.md5("".join(u["id"] for u in units).encode()).hexdigest()
    with _lock:
        _cache[wid] = {"sig": sig, "units": units, "emb": emb, "E": E, "index": VectorIndex(E), "note": None}


def _hit(u: dict, score: float) -> dict:
    return {"unit_id": u["id"], "source_id": u["source_id"], "source_name": u["source_name"], "label": u["label"],
            "text": u["text"], "score": round(float(score), 3)}


def ask(wid: str, question: str, history: list[dict] | None = None, k: int = 8) -> dict:
    st = _store(wid)
    if st is None:
        return {"answer": "This workspace has no processed resources yet. Add and process some sources first.",
                "mode": "none", "citations": [], "notes": []}
    units, emb = st["units"], st["emb"]
    q_text = question
    if history:  # light follow-up handling: add the previous user question as context for retrieval
        prev = [m["content"] for m in history if m.get("role") == "user"][-1:]
        if prev and len(question.split()) < 6:
            q_text = prev[0] + " " + question
    qv = emb.encode([q_text])
    D, I = st["index"].search(qv, k)
    floor = 0.12 if not emb.semantic else 0.30
    hits = [(units[int(i)], float(s)) for s, i in zip(D[0], I[0]) if int(i) >= 0 and s >= floor]
    # make sure several sources are represented: take the best hit from sources missing in the top list
    present = {u["source_id"] for u, _ in hits}
    for sid in {u["source_id"] for u in units} - present:
        best = [(units[i], float(qv[0] @ st["E"][i])) for i in range(len(units)) if units[i]["source_id"] == sid]
        if best:
            u, s = max(best, key=lambda x: x[1])
            if s >= floor * 1.2:
                hits.append((u, s))
    hits.sort(key=lambda x: -x[1])
    hits = hits[: k + 2]
    notes: list[str] = []
    if not hits:
        return {"answer": "The uploaded sources do not contain information relevant to that question.",
                "mode": "none", "citations": [], "notes": []}

    report = db.load_report(wid)
    ids = {u["id"] for u, _ in hits}
    if report:
        for c in report.get("conflicts", []):
            if len({p["unit_id"] for p in c["positions"]} & ids) >= 1:
                srcs = sorted({p["source_id"] for p in c["positions"]})
                kind = "Sources disagree" if c["method"] == "llm" else "Possible disagreement between sources"
                notes.append(f"⚠ {kind} on a related point ({', '.join(srcs)}): {c['topic'][:110]} — see report conflict {c['id']}.")

    cites = Cites(units)
    if llm.enabled:
        mat = "\n".join(f"[{u['h']}] ({u['source_id']} · {u['label']}) {u['text']}" for u, _ in hits)
        conv = ""
        if history:
            conv = "Earlier conversation:\n" + "\n".join(f"{m['role']}: {m['content'][:400]}" for m in history[-4:]) + "\n\n"
        try:
            ans = llm.complete(CHAT_SYSTEM, f"{conv}QUESTION: {question}\n\nSOURCE STATEMENTS:\n{mat}", 1000)
            bad = numbers_supported(strip_handles(ans), [mat, question])
            ans = cites.resolve(ans.strip())
            if any(float(n) >= 10 or "." in n for n in bad if re.fullmatch(r"\d+(\.\d+)?", n)):
                notes.append("⚠ Some figures in this answer could not be matched to the retrieved passages; verify against the sources.")
            return {"answer": ans, "mode": "llm", "citations": [_hit(u, s) for u, s in hits], "notes": notes}
        except LLMError as e:
            notes.append(f"The LLM call failed ({e}); showing extractive results instead.")

    # ---- offline extractive answer: best sentences per hit
    qset = content_set(question, False)
    lines = []
    for u, s in hits[:6]:
        sents = split_sentences(u["text"])
        scored = sorted(sents, key=lambda x: -len(qset & content_set(x, False)))
        best = scored[0] if scored and qset & content_set(scored[0], False) else u["text"]
        lines.append(f"- {best} {cites.chip(u)}")
    ans = ("*Offline extractive mode (no LLM configured): these are the closest passages across your sources, ranked by similarity — "
           "not a written answer.*\n\n" + "\n".join(lines))
    return {"answer": ans, "mode": "extractive", "citations": [_hit(u, s) for u, s in hits], "notes": notes}
