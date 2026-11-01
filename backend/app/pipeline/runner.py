"""Background processing job: extraction -> embedding -> comparison -> dedup -> synthesis -> report."""
from __future__ import annotations

import logging
import threading
import time
import traceback

from .. import db
from ..config import settings
from ..ingest import process_resource
from ..llm import llm
from . import chat
from .analysis import analyse
from .embeddings import VectorIndex, get_embedder
from .synthesis import Builder

log = logging.getLogger("converge.runner")

STAGES = [("extraction", "Extraction & normalisation"), ("embedding", "Semantic embedding & indexing"),
          ("comparison", "Cross-source similarity search"), ("dedup", "Deduplication & relationship analysis"),
          ("synthesis", "Knowledge synthesis"), ("report", "Report generation & validation")]

_jobs: dict[str, dict] = {}
_threads: dict[str, threading.Thread] = {}
_lock = threading.Lock()


def new_state() -> dict:
    return {"status": "running", "started": time.time(), "finished": None, "error": None, "warnings": [], "log": [],
            "stages": [{"key": k, "label": l, "status": "pending", "progress": 0.0, "detail": ""} for k, l in STAGES],
            "mode": {"llm": llm.status(), "embedder": None, "note": None}, "summary": None}


def get_state(wid: str) -> dict | None:
    return _jobs.get(wid) or db.get_job(wid)


def is_running(wid: str) -> bool:
    t = _threads.get(wid)
    return bool(t and t.is_alive())


def start(wid: str) -> dict:
    with _lock:
        if is_running(wid):
            return _jobs[wid]
        st = new_state()
        _jobs[wid] = st
        db.set_job(wid, st)
        t = threading.Thread(target=_run, args=(wid,), daemon=True, name=f"job-{wid}")
        _threads[wid] = t
        t.start()
        return st


def _progress_fn(wid: str, st: dict):
    keys = [k for k, _ in STAGES]

    def progress(stage: str, frac: float, detail: str = "") -> None:
        i = keys.index(stage)
        for j, s in enumerate(st["stages"]):
            if j < i and s["status"] != "done":
                s["status"], s["progress"] = "done", 1.0
            if j == i:
                s["status"] = "done" if frac >= 1.0 else "running"
                s["progress"] = round(min(frac, 1.0), 3)
                if detail:
                    s["detail"] = detail
        if detail and (not st["log"] or st["log"][-1]["text"] != detail):
            st["log"].append({"t": time.time(), "stage": stage, "text": detail})
            st["log"] = st["log"][-60:]
        db.set_job(wid, st)
    return progress


def _fail(wid: str, st: dict, msg: str) -> None:
    for s in st["stages"]:
        if s["status"] == "running":
            s["status"], s["detail"] = "error", msg
    st["status"], st["error"], st["finished"] = "error", msg, time.time()
    db.set_job(wid, st)


def _run(wid: str) -> None:
    st = _jobs[wid]
    progress = _progress_fn(wid, st)
    warnings: list[str] = st["warnings"]
    try:
        ws = db.get_workspace(wid)
        # 1. extraction ------------------------------------------------------------
        progress("extraction", 0.02, "Checking resources…")
        resources = db.list_resources(wid)
        if not resources:
            return _fail(wid, st, "This workspace has no resources. Add at least one document, URL or text first.")
        pending = [r for r in resources if r["status"] in ("queued", "processing")]
        for i, r in enumerate(pending):
            progress("extraction", 0.05 + 0.9 * i / max(len(pending), 1), f"Extracting {r['name']}…")
            process_resource(r["id"])
        deadline = time.time() + 600
        while any(r["status"] == "processing" for r in db.list_resources(wid)) and time.time() < deadline:
            time.sleep(0.5)  # another thread is still extracting
        resources = db.list_resources(wid)
        ready = [r for r in resources if r["status"] == "ready"]
        failed = [r for r in resources if r["status"] == "error"]
        for r in failed:
            warnings.append(f"{r['source_id']} '{r['name']}' failed and is excluded: {r['error']}")
        if not ready:
            return _fail(wid, st, "None of the resources could be read. " + (failed[0]["error"] if failed else ""))
        units = db.list_units(wid)
        if len(units) > settings.max_units:
            return _fail(wid, st, f"The workspace has {len(units)} knowledge units, above MAX_UNITS={settings.max_units}. "
                                  "Remove some resources or raise MAX_UNITS in .env.")
        if len(ready) < 2:
            warnings.append("Only one readable source: cross-source comparison and contradiction detection need at least two.")
        progress("extraction", 1.0, f"{len(ready)} source(s) ready - {len(units)} knowledge units"
                                    + (f"; {len(failed)} failed" if failed else ""))

        # 2. embedding -------------------------------------------------------------
        progress("embedding", 0.1, "Loading embedding model…")
        try:
            emb, note = get_embedder([u["text"] for u in units])
            progress("embedding", 0.4, f"Embedding {len(units)} units with {emb.name}…")
            E = emb.encode([u["text"] for u in units])
        except Exception as e:
            return _fail(wid, st, f"Embedding failed: {e}")
        index = VectorIndex(E)
        st["mode"]["embedder"], st["mode"]["note"], st["mode"]["index"] = emb.name, note, index.backend
        if note:
            warnings.append(note)
        progress("embedding", 1.0, f"{len(units)} vectors indexed ({index.backend}, dim {E.shape[1]}).")

        # 3-4. comparison + dedup ----------------------------------------------------
        analysis = analyse(units, E, emb, progress, warnings)
        progress("dedup", 1.0, f"{analysis['stats']['units']} units → {analysis['stats']['items']} distinct statements; "
                               f"{analysis['stats']['conflicts']} conflict(s) flagged.")

        # 5-6. synthesis + report -----------------------------------------------------
        b = Builder(ws, resources, units, analysis, analysis["vecs"], emb.name, warnings, progress)
        sections = b.build()
        progress("report", 0.9, "Validating citations and coverage…")
        report = assemble(ws, resources, units, analysis, b, sections, emb, index, note, warnings)
        db.save_report(wid, report)
        chat.warm(wid, units, emb, E)
        st["summary"] = {"sources": len(ready), **report["stats"]}
        progress("report", 1.0, "Report ready.")
        st["status"], st["finished"] = "done", time.time()
        db.set_job(wid, st)
    except Exception as e:
        log.error("Job failed\n%s", traceback.format_exc())
        _fail(wid, st, f"Unexpected error: {type(e).__name__}: {e}")


def assemble(ws, resources, units, analysis, b: Builder, sections, emb, index, note, warnings) -> dict:
    import re
    body = "\n".join(s["markdown"] for s in sections)
    cited_ids = set(re.findall(r"cite:(u_\w+)", body))
    by_id = b.c.by_id
    citations = {uid: {"source_id": by_id[uid]["source_id"], "source_name": by_id[uid]["source_name"], "label": by_id[uid]["label"],
                       "page": by_id[uid]["page"], "t_start": by_id[uid]["t_start"], "kind": by_id[uid]["kind"],
                       "text": by_id[uid]["text"], "context": by_id[uid]["ctx"]} for uid in cited_ids if uid in by_id}
    items = analysis["items"]
    accounted = {u for it in items for u in it["unit_ids"]}
    if len(accounted) != len(units):
        warnings.append(f"Internal check: {len(units) - len(accounted)} unit(s) were not assigned to any statement.")
    uncited_items = [it["id"] for it in items if not (cited_ids & set(b.item_units(it)))]
    consolidations = []
    for it in items:
        if len(it["unit_ids"]) > 1 or it["details"]:
            consolidations.append({
                "item": it["id"], "relation": it["relation"], "text": it["text"], "method": it["method"], "sources": it["sources"],
                "units": [{"unit_id": u, "source_id": by_id[u]["source_id"], "label": by_id[u]["label"], "text": by_id[u]["text"]}
                          for u in it["unit_ids"]],
                "details": [{"text": d["text"], "unit_id": d["unit_id"], "source_id": by_id[d["unit_id"]]["source_id"]} for d in it["details"]],
                "conflicts": it["conflicts"]})
    conflicts = []
    for c in analysis["conflicts"]:
        conflicts.append({**c, "positions": [{**p, "source_id": by_id[p["unit_id"]]["source_id"], "label": by_id[p["unit_id"]]["label"]}
                                             for p in c["positions"]]})
    stats = dict(analysis["stats"])
    stats.update({"units_cited": len(cited_ids), "items_uncited": len(uncited_items), "invalid_citations_removed": b.c.removed,
                  "unverified_figures": sorted(set(b.unverified)), "llm_sections": b.llm_sections,
                  "fallback_sections": b.fallback_sections, "themes": len(b.themes)})
    if b.c.removed:
        warnings.append(f"{b.c.removed} citation(s) referencing non-existent statements were removed from LLM output.")
    return {
        "title": ws["name"], "topic": ws["name"], "description": ws.get("description", ""), "generated_at": time.time(),
        "mode": {"synthesis": "llm" if b.llm_sections else "extractive", "llm": llm.status(), "embedder": emb.name,
                 "index": index.backend, "embedder_note": note},
        "sections": sections, "citations": citations, "conflicts": conflicts, "consolidations": consolidations,
        "themes": [{"title": t["title"], "sources": t["sources"], "items": len(t["items"])} for t in b.themes],
        "sources": [{"source_id": r["source_id"], "name": r["name"], "kind": r["kind"], "status": r["status"],
                     "origin": r["origin"], "units": r["stats"].get("units", 0), "error": r["error"]} for r in resources],
        "source_stats": b.source_stats(), "stats": stats, "warnings": list(dict.fromkeys(warnings)),
    }
