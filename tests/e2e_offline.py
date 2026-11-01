"""End-to-end test through the HTTP API in offline (no-LLM) mode. Run: python tests/e2e_offline.py"""
import os, sys, tempfile, time, json, re
from pathlib import Path
tmp = tempfile.mkdtemp(prefix="converge-test-")
os.environ.update({"DATA_DIR": tmp, "LLM_PROVIDER": "none", "EMBEDDING_BACKEND": "tfidf", "ALLOW_PRIVATE_URLS": "true"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from fastapi.testclient import TestClient
from app.main import app

def wait(fn, timeout=120):
    t = time.time()
    while time.time() - t < timeout:
        v = fn()
        if v: return v
        time.sleep(0.4)
    raise TimeoutError

with TestClient(app) as c:
    ws = c.post("/api/demo").json(); wid = ws["id"]
    print("workspace", ws["name"])
    wait(lambda: all(r["status"] in ("ready", "error") for r in c.get(f"/api/workspaces/{wid}").json()["resources"]))
    for r in c.get(f"/api/workspaces/{wid}").json()["resources"]:
        print(f"  {r['source_id']} {r['kind']:5} {r['status']:6} units={r['stats'].get('units')} {r['error']} {r['warnings'][:1]}")
    c.post(f"/api/workspaces/{wid}/process")
    job = wait(lambda: (lambda j: j if j["status"] in ("done", "error") else None)(c.get(f"/api/workspaces/{wid}/job").json()))
    print("job", job["status"], job["error"])
    for s in job["stages"]: print("  ", s["status"], s["label"], "-", s["detail"])
    rep = c.get(f"/api/workspaces/{wid}/report").json()
    print(json.dumps(rep["stats"], indent=1))
    Path(tmp, "report.json").write_text(json.dumps(rep, indent=1))
    for s in rep["sections"]:
        print("=" * 100); print(s["number"], s["title"]); print(s["markdown"][:1800])
    print("WARNINGS", rep["warnings"])
    print("TMP", tmp)
