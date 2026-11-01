"""Exercises the LLM code path (judging, section writing, chat, guards) against a scripted fake OpenAI-compatible server.

    python tests/e2e_llm_mock.py

This validates plumbing and safety nets, NOT the quality of any real model's prose. The mock deliberately invents a figure
("777 GW") and cites a non-existent passage ([U9999]); the app must flag the first and remove the second.
"""
import os, subprocess, sys, tempfile, time, warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = "9911"
os.environ.update({"DATA_DIR": tempfile.mkdtemp(prefix="converge-llm-"), "LLM_PROVIDER": "openai_compatible",
                   "LLM_BASE_URL": f"http://127.0.0.1:{PORT}", "LLM_API_KEY": "mock", "LLM_MODEL": "mock-llm",
                   "EMBEDDING_BACKEND": "tfidf", "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
warnings.filterwarnings("ignore")
srv = subprocess.Popen([sys.executable, str(ROOT / "tests" / "mock_llm_server.py"), PORT])
time.sleep(1.2)
sys.path.insert(0, str(ROOT / "backend"))
import logging; logging.disable(logging.CRITICAL)
fails = []
def check(n, ok, d=""):
    print(("PASS " if ok else "FAIL ") + n + (f" [{d}]" if d else "")); (not ok) and fails.append(n)
try:
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        wid = c.post("/api/demo").json()["id"]
        while not all(r["status"] in ("ready", "error") for r in c.get(f"/api/workspaces/{wid}").json()["resources"]): time.sleep(.3)
        c.post(f"/api/workspaces/{wid}/process")
        while (j := c.get(f"/api/workspaces/{wid}/job").json())["status"] == "running": time.sleep(.5)
        check("job finished", j["status"] == "done", j["error"])
        r = c.get(f"/api/workspaces/{wid}/report").json(); s = r["stats"]
        check("LLM wrote sections", r["mode"]["synthesis"] == "llm" and s["llm_sections"] >= 10, s["llm_sections"])
        check("LLM judged overlap clusters", s["llm_judged_clusters"] >= 1, s["llm_judged_clusters"])
        check("invented citation [U9999] removed", s["invalid_citations_removed"] >= 1)
        check("invented figure 777 flagged unverified", "777" in s["unverified_figures"] and "Verification note" in r["sections"][11]["markdown"])
        a = c.post(f"/api/workspaces/{wid}/chat", json={"question": "What is the 2023 pack price?", "history": []}).json()
        check("chat answered via LLM with citations", a["mode"] == "llm" and "cite:" in a["answer"])
        check("PDF + DOCX export in LLM mode", c.get(f"/api/workspaces/{wid}/export/pdf").status_code == 200 and c.get(f"/api/workspaces/{wid}/export/docx").status_code == 200)
finally:
    srv.terminate()
print("ALL CHECKS PASSED" if not fails else f"FAILED: {fails}"); sys.exit(1 if fails else 0)
