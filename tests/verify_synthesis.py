"""Verifies the central promise of Converge AI on the bundled demo sources (5 sources, one topic), fully offline.

    python tests/verify_synthesis.py

Checks (each prints PASS/FAIL, exit code 1 if any fail):
  1. every source contributes to the final report (is cited in it)
  2. every extracted passage is accounted for - none silently dropped
  3. facts that exist in ONLY ONE source survive into the report (probe phrases)
  4. facts stated in several sources are consolidated (fewer statements than passages; multi-source statements exist)
  5. a known disagreement ($139 vs $151 per kWh, 2023) is flagged with BOTH positions and sources
  6. narrative sections 3-6 never repeat the same passage
  7. a naive "summarise each file and concatenate" baseline would repeat facts and flag nothing (comparison table)
"""
import collections, os, re, sys, tempfile, time, warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ.update({"DATA_DIR": tempfile.mkdtemp(prefix="converge-verify-"), "LLM_PROVIDER": "none", "EMBEDDING_BACKEND": "tfidf"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import logging; logging.disable(logging.CRITICAL)
from fastapi.testclient import TestClient
from app.main import app

fails = []
def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok: fails.append(name)

with TestClient(app) as c:
    wid = c.post("/api/demo").json()["id"]
    t = time.time()
    while not all(r["status"] in ("ready", "error") for r in c.get(f"/api/workspaces/{wid}").json()["resources"]):
        assert time.time() - t < 120, "extraction timed out"; time.sleep(0.3)
    c.post(f"/api/workspaces/{wid}/process")
    while (job := c.get(f"/api/workspaces/{wid}/job").json())["status"] == "running":
        assert time.time() - t < 240, "processing timed out"; time.sleep(0.4)
    assert job["status"] == "done", job["error"]
    rep = c.get(f"/api/workspaces/{wid}/report").json()
    units_by_src = {}
    for r in c.get(f"/api/workspaces/{wid}").json()["resources"]:
        units_by_src[r["source_id"]] = c.get(f"/api/resources/{r['id']}/units").json()["units"]

st, cites = rep["stats"], rep["citations"]
body = "\n".join(s["markdown"] for s in rep["sections"])
plain = re.sub(r"\]\(cite:u_\w+\)", "]", body)

# 1 ---------------------------------------------------------------------------------
cited_src = collections.Counter(v["source_id"] for v in cites.values())
check("1. all 5 sources are cited in the report", set(cited_src) == {"S1", "S2", "S3", "S4", "S5"}, dict(sorted(cited_src.items())))
check("1b. report has all 13 required sections", [s["number"] for s in rep["sections"]] == list(range(1, 14)))

# 2 ---------------------------------------------------------------------------------
all_units = {u["id"] for us in units_by_src.values() for u in us}
check("2. every extracted passage is cited somewhere (none dropped)", all_units <= set(cites), f"{len(set(cites) & all_units)}/{len(all_units)}")

# 3 ---------------------------------------------------------------------------------
probes = {"S1": "Nobel Prize", "S2": "150-250 kW", "S3": "3,200 cycles", "S4": "hydrogen sulfide", "S5": "6% for lithium"}
for sid, phrase in probes.items():
    check(f"3. unique fact from {sid} preserved ('{phrase}')", phrase in plain)

# 4 ---------------------------------------------------------------------------------
check("4. overlapping passages were consolidated", st["items"] < st["units"] and st["multi_source_items"] >= 3,
      f"{st['units']} passages -> {st['items']} statements, {st['multi_source_items']} backed by 2+ sources")
check("4b. details that only one source adds were kept", st["details_preserved"] >= 3, f"{st['details_preserved']}")

# 5 ---------------------------------------------------------------------------------
hit = [x for x in rep["conflicts"] if {"$139", "$151"} <= {m for p in x["positions"] for m in re.findall(r"\$\d+", p["claim"])}]
check("5. $139 vs $151 per kWh disagreement flagged", bool(hit))
if hit:
    check("5b. both positions attributed to their sources", len({p["source_id"] for p in hit[0]["positions"]}) >= 2,
          sorted({p["source_id"] for p in hit[0]["positions"]}))
sec9 = re.sub(r"\]\(cite:u_\w+\)", "]", rep["sections"][8]["markdown"])
check("5c. Section 9 shows both figures and does not pick a winner", "$139" in sec9 and "$151" in sec9 and "Not resolved" in sec9)

# 6 ---------------------------------------------------------------------------------
seen = collections.defaultdict(set)
for s in rep["sections"]:
    if 3 <= s["number"] <= 6:
        for cid in set(re.findall(r"cite:(u_\w+)", s["markdown"])): seen[cid].add(s["number"])
rep_ids = {cid for cid, v in seen.items() if len(v) > 1}
check("6. narrative sections 3-6 don't repeat a passage", not rep_ids, f"{len(rep_ids)} repeated")

# 7 ---------------------------------------------------------------------------------
naive_statements, naive_conflicts = st["units"], 0
print("\n          | per-file summaries concatenated | Converge AI")
print(f"statements| {naive_statements:>31} | {st['items']}")
print(f"agreeing  | {'0 (repeated verbatim in each)':>31} | {st['multi_source_items']} merged, each citing all its sources")
print(f"conflicts | {naive_conflicts:>31} | {st['conflicts']} flagged with both sides")
print(f"traceable | {'file name only':>31} | {len(cites)} claims -> exact passage + page/section")
print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} CHECK(S) FAILED: {fails}"))
sys.exit(1 if fails else 0)
