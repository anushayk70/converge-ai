"""CONVERGE AI - FastAPI application."""
from __future__ import annotations

import importlib.util
import json
import logging
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db
from .config import ROOT, have_binary, settings
from .extractors import EXT_KIND, detect_kind
from .extractors.ocr import ocr_status
from .extractors.transcribe import transcription_status
from .ingest import process_resource
from .llm import llm
from .pipeline import chat, runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("converge")

app = FastAPI(title="CONVERGE AI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="extract")
SAMPLE_DIR = ROOT / "sample_data"
DIST = ROOT / "frontend" / "dist"


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    # a restart interrupts running jobs / extractions - mark them so the UI doesn't wait forever
    for r in db.q("SELECT id FROM resources WHERE status IN ('processing','queued')"):
        pool.submit(process_resource, r["id"])
    for j in db.q("SELECT workspace_id, state FROM jobs"):
        st = json.loads(j["state"])
        if st.get("status") == "running":
            st["status"], st["error"] = "error", "The server restarted while this job was running. Start processing again."
            db.set_job(j["workspace_id"], st)


# ------------------------------------------------------------------ models
class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1500)


class UrlIn(BaseModel):
    url: str = Field(min_length=4, max_length=2000)


class TextIn(BaseModel):
    name: str = Field(default="", max_length=140)
    text: str = Field(min_length=1)


class ChatIn(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    history: list[dict] = []


# ------------------------------------------------------------------ helpers
def _ws_or_404(wid: str) -> dict:
    ws = db.get_workspace(wid)
    if not ws:
        raise HTTPException(404, "Workspace not found.")
    return ws


def _summary(ws: dict) -> dict:
    res = db.list_resources(ws["id"])
    job = runner.get_state(ws["id"])
    stale = False
    if ws.get("report_at"):
        rep = db.load_report(ws["id"]) or {}
        have = {(s["source_id"], s["name"]) for s in rep.get("sources", []) if s["status"] == "ready"}
        now = {(r["source_id"], r["name"]) for r in res if r["status"] == "ready"}
        stale = have != now
    return {**ws, "resource_count": len(res), "ready_count": sum(r["status"] == "ready" for r in res),
            "error_count": sum(r["status"] == "error" for r in res), "kinds": sorted({r["kind"] for r in res}),
            "has_report": bool(ws.get("report_at")), "report_stale": stale,
            "job_status": (job or {}).get("status")}


def _safe_name(n: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", Path(n).name)[:120] or "file"


def _store_dir(wid: str) -> Path:
    d = settings.upload_dir / wid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _add_file_resource(wid: str, kind: str, name: str, src: Path | None = None, data: bytes | None = None) -> dict:
    res = db.add_resource(wid, kind, name)
    dest = _store_dir(wid) / f"{res['id']}_{_safe_name(name)}"
    if src is not None:
        shutil.copyfile(src, dest)
    elif data is not None:
        dest.write_bytes(data)
    db.x("UPDATE resources SET stored_path=? WHERE id=?", (str(dest), res["id"]))
    pool.submit(process_resource, res["id"])
    return db.get_resource(res["id"])  # type: ignore[return-value]


# ------------------------------------------------------------------ system
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "time": time.time()}


@app.get("/api/capabilities")
def capabilities() -> dict:
    st_ok = importlib.util.find_spec("sentence_transformers") is not None
    return {
        "llm": llm.status(),
        "embeddings": {"backend": settings.embedding_backend,
                       "semantic_available": st_ok or settings.embedding_backend == "api",
                       "message": ("Semantic embeddings (sentence-transformers) installed." if st_ok else
                                   "sentence-transformers not installed - lexical TF-IDF fallback will be used for similarity.")},
        "ocr": ocr_status(), "transcription": transcription_status(),
        "ffmpeg": have_binary("ffmpeg"), "faiss": importlib.util.find_spec("faiss") is not None,
        "max_upload_mb": settings.max_upload_mb,
        "formats": sorted(EXT_KIND.keys()),
    }


# ------------------------------------------------------------------ workspaces
@app.get("/api/workspaces")
def list_workspaces() -> list[dict]:
    return [_summary(w) for w in db.q("SELECT id,name,description,created_at,updated_at,report_at FROM workspaces ORDER BY updated_at DESC")]


@app.post("/api/workspaces")
def create_workspace(body: WorkspaceIn) -> dict:
    return _summary(db.create_workspace(body.name.strip(), body.description.strip()))


@app.get("/api/workspaces/{wid}")
def get_workspace(wid: str) -> dict:
    ws = _ws_or_404(wid)
    return {**_summary(ws), "resources": db.list_resources(wid)}


@app.patch("/api/workspaces/{wid}")
def update_workspace(wid: str, body: WorkspaceIn) -> dict:
    _ws_or_404(wid)
    db.x("UPDATE workspaces SET name=?, description=?, updated_at=? WHERE id=?", (body.name.strip(), body.description.strip(), time.time(), wid))
    return _summary(db.get_workspace(wid))  # type: ignore[arg-type]


@app.delete("/api/workspaces/{wid}")
def delete_workspace(wid: str) -> dict:
    _ws_or_404(wid)
    if runner.is_running(wid):
        raise HTTPException(409, "Processing is running for this workspace. Wait for it to finish.")
    db.x("DELETE FROM units WHERE workspace_id=?", (wid,))
    db.x("DELETE FROM resources WHERE workspace_id=?", (wid,))
    db.x("DELETE FROM jobs WHERE workspace_id=?", (wid,))
    db.x("DELETE FROM workspaces WHERE id=?", (wid,))
    shutil.rmtree(settings.upload_dir / wid, ignore_errors=True)
    return {"ok": True}


# ------------------------------------------------------------------ resources
@app.post("/api/workspaces/{wid}/resources/upload")
async def upload(wid: str, files: list[UploadFile] = File(...)) -> dict:
    _ws_or_404(wid)
    limit = settings.max_upload_mb * 1024 * 1024
    results = []
    for f in files:
        name = f.filename or "file"
        kind = detect_kind(name)
        if not kind:
            results.append({"name": name, "ok": False, "error": f"Unsupported file type '{Path(name).suffix or 'none'}'. "
                            "Supported: PDF, DOCX, TXT/MD, HTML, images, audio, video."})
            continue
        if kind == "image" and Path(name).suffix.lower() == ".gif":
            pass
        buf = bytearray()
        too_big = False
        while chunk := await f.read(1024 * 1024):
            buf.extend(chunk)
            if len(buf) > limit:
                too_big = True
                break
        if too_big:
            results.append({"name": name, "ok": False, "error": f"File is larger than {settings.max_upload_mb} MB (MAX_UPLOAD_MB)."})
            continue
        if not buf:
            results.append({"name": name, "ok": False, "error": "The file is empty."})
            continue
        res = _add_file_resource(wid, kind, name, data=bytes(buf))
        results.append({"name": name, "ok": True, "resource": res})
    return {"results": results}


@app.post("/api/workspaces/{wid}/resources/url")
def add_url(wid: str, body: UrlIn) -> dict:
    _ws_or_404(wid)
    url = body.url.strip()
    if not re.match(r"^https?://[^\s/]+\.[^\s/]+|^https?://localhost", url, re.I):
        raise HTTPException(400, "Please enter a full URL starting with http:// or https://")
    res = db.add_resource(wid, "url", url, origin=url)
    pool.submit(process_resource, res["id"])
    return res


@app.post("/api/workspaces/{wid}/resources/text")
def add_text(wid: str, body: TextIn) -> dict:
    _ws_or_404(wid)
    text = body.text.strip()
    if len(text) < 40:
        raise HTTPException(400, "Pasted text is too short to analyse (minimum 40 characters).")
    if len(text) > 2_000_000:
        raise HTTPException(400, "Pasted text is too long (limit 2 MB).")
    n = sum(1 for r in db.list_resources(wid) if r["kind"] == "text") + 1
    name = body.name.strip() or f"Pasted text {n}"
    return _paste(wid, name, text)


def _paste(wid: str, name: str, text: str) -> dict:
    res = db.add_resource(wid, "text", name)
    dest = _store_dir(wid) / f"{res['id']}_paste.txt"
    dest.write_text(text, encoding="utf-8")
    db.x("UPDATE resources SET stored_path=? WHERE id=?", (str(dest), res["id"]))
    pool.submit(process_resource, res["id"])
    return db.get_resource(res["id"])  # type: ignore[return-value]


@app.delete("/api/resources/{rid}")
def remove_resource(rid: str) -> dict:
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404, "Resource not found.")
    if runner.is_running(r["workspace_id"]):
        raise HTTPException(409, "Processing is running; wait for it to finish before removing resources.")
    if r["stored_path"]:
        Path(r["stored_path"]).unlink(missing_ok=True)
    db.delete_resource(rid)
    db.touch_workspace(r["workspace_id"])
    return {"ok": True}


@app.post("/api/resources/{rid}/retry")
def retry_resource(rid: str) -> dict:
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404, "Resource not found.")
    db.set_resource_status(rid, "queued")
    pool.submit(process_resource, rid)
    return db.get_resource(rid)  # type: ignore[return-value]


@app.get("/api/resources/{rid}/units")
def resource_units(rid: str) -> dict:
    r = db.get_resource(rid)
    if not r:
        raise HTTPException(404, "Resource not found.")
    return {"resource": r, "units": db.units_for_resource(rid, 300)}


# ------------------------------------------------------------------ processing / report
@app.post("/api/workspaces/{wid}/process")
def process(wid: str) -> dict:
    _ws_or_404(wid)
    if not db.list_resources(wid):
        raise HTTPException(400, "Add at least one resource before processing.")
    return runner.start(wid)


@app.get("/api/workspaces/{wid}/job")
def job(wid: str) -> dict:
    _ws_or_404(wid)
    return runner.get_state(wid) or {"status": "idle", "stages": [{"key": k, "label": l, "status": "pending", "progress": 0, "detail": ""}
                                                                  for k, l in runner.STAGES], "log": [], "warnings": [],
                                      "mode": {"llm": llm.status()}}


@app.get("/api/workspaces/{wid}/report")
def report(wid: str) -> dict:
    ws = _ws_or_404(wid)
    rep = db.load_report(wid)
    if not rep:
        raise HTTPException(404, "No report yet. Run processing first.")
    rep["stale"] = _summary(ws)["report_stale"]
    return rep


@app.get("/api/workspaces/{wid}/export/{fmt}")
def export(wid: str, fmt: str) -> Response:
    ws = _ws_or_404(wid)
    rep = db.load_report(wid)
    if not rep:
        raise HTTPException(404, "No report to export yet.")
    slug = re.sub(r"[^a-z0-9]+", "-", ws["name"].lower()).strip("-")[:50] or "report"
    try:
        if fmt == "pdf":
            from .export.pdf import build_pdf
            data, mime = build_pdf(rep), "application/pdf"
        elif fmt == "docx":
            from .export.docx_export import build_docx
            data, mime = build_docx(rep), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif fmt in ("md", "markdown"):
            from .export.mdparse import to_plain_markdown
            md = "\n\n".join(f"## {s['number']}. {s['title']}\n\n{to_plain_markdown(s['markdown'])}" if s["number"] > 1
                             else to_plain_markdown(s["markdown"]) for s in rep["sections"])
            data, mime, fmt = md.encode("utf-8"), "text/markdown", "md"
        else:
            raise HTTPException(400, "Format must be pdf, docx or md.")
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Export failed")
        raise HTTPException(500, f"Export failed: {type(e).__name__}: {e}")
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="converge-{slug}.{fmt}"'})


@app.post("/api/workspaces/{wid}/chat")
def chat_ask(wid: str, body: ChatIn) -> dict:
    _ws_or_404(wid)
    if not any(r["status"] == "ready" for r in db.list_resources(wid)):
        raise HTTPException(400, "No processed resources yet. Wait for extraction to finish (or fix failed resources).")
    try:
        return chat.ask(wid, body.question.strip(), body.history[-6:])
    except Exception as e:
        log.exception("Chat failed")
        raise HTTPException(500, f"Could not answer: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ demo
@app.post("/api/demo")
def create_demo() -> dict:
    mf = SAMPLE_DIR / "manifest.json"
    if not mf.exists():
        raise HTTPException(404, "Sample data is missing (sample_data/manifest.json).")
    m = json.loads(mf.read_text())
    ws = db.create_workspace(m["name"], m["description"])
    for r in m["resources"]:
        if "file" in r:
            p = SAMPLE_DIR / r["file"]
            _add_file_resource(ws["id"], r.get("kind") or detect_kind(p.name) or "txt", r.get("name") or p.name, src=p)
        elif "url" in r:
            res = db.add_resource(ws["id"], "url", r["url"], origin=r["url"])
            pool.submit(process_resource, res["id"])
        else:
            _paste(ws["id"], r["name"], r["text"])
    return _summary(db.get_workspace(ws["id"]))  # type: ignore[arg-type]


# ------------------------------------------------------------------ frontend
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    f = (DIST / path)
    if path and f.is_file() and DIST in f.resolve().parents:
        return FileResponse(f)
    if (DIST / "index.html").exists():
        return FileResponse(DIST / "index.html")
    return JSONResponse({"message": "Frontend not built. Run `cd frontend && npm install && npm run build`, "
                                    "or use the Vite dev server (npm run dev)."}, status_code=200)
