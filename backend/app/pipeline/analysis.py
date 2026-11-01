"""Semantic comparison + relationship judgement.

1. Cluster units that talk about the same concept (cross-source: related; same-source: near-duplicates only).
2. Decide per cluster whether the units are duplicate / complementary / conflicting / unrelated.
   - LLM mode: the model judges and writes a merged statement, but must list which units it covers, which unique
     details it keeps and which claims conflict. Anything it fails to account for is re-emitted as its own item,
     and merged text containing numbers not found in the sources is rejected -> information cannot silently vanish.
   - Offline mode: transparent heuristics (sentence-level novelty, numeric/polarity checks).
"""
from __future__ import annotations

import logging
import re

import numpy as np

from ..config import settings
from ..llm import LLMError, llm
from .embeddings import Embedder, VectorIndex
from .textutil import content_set, detect_conflict, novelty, shorten, split_sentences

log = logging.getLogger("converge.analysis")

JUDGE_SYSTEM = """You are the relationship-judging engine of CONVERGE AI, a multi-source knowledge synthesis system.
You receive clusters of short statements (each tagged [U#] with its source). For every cluster decide how the statements relate:
- duplicate: the same information, merely reworded.
- complementary: overlapping topic but each adds different details (numbers, examples, causes, caveats, scope).
- conflicting: at least two statements make incompatible claims about the same thing (different figures, opposite conclusions,
  different dates). Different scopes or conditions are NOT conflicts - say complementary and note the condition.
- unrelated: the statements are about different things; list them under "standalone".
Never discard a distinct fact, number, name, example or caveat just because sentences look similar.
Write "merged" as ONE self-contained, factual statement (1-3 sentences) that keeps every detail that is shared or compatible, using only
information present in the statements. Put details that appear in only one statement and do not fit the merged statement into
"unique_details". Put incompatible claims into "conflicts" (do not pick a winner). "covers" lists every unit whose information is fully
represented by "merged". Every unit must appear in covers, unique_details, conflicts or standalone."""


LEXICAL_CROSS_THRESHOLD = 0.27   # best-sentence IDF-weighted cosine between units of different sources


def lexical_edges(units: list[dict], D: np.ndarray, I: np.ndarray, E: np.ndarray, emb: Embedder) -> dict[tuple[int, int], float]:
    """Fallback similarity when no neural embeddings are available.

    Whole-unit TF-IDF cosine is diluted by long, multi-sentence units, so two units that state the same fact in one
    sentence each score low. Here we compare the best-matching *sentence pair* with IDF weights instead.
    """
    import math
    from collections import Counter
    n = len(units)
    sents = [[cs for cs in (content_set(s) for s in split_sentences(u["text"])) if len(cs) >= 3] or [content_set(u["text"])]
             for u in units]
    df = Counter(w for u in sents for w in set().union(*u))
    idf = {w: math.log((n + 1) / (c + 0.5)) for w, c in df.items()}

    def cos(a: set, b: set) -> float:
        inter = sum(idf[w] for w in a & b)
        if inter <= 0:
            return 0.0
        return inter / math.sqrt(sum(idf[w] for w in a) * sum(idf[w] for w in b))

    edges: dict[tuple[int, int], float] = {}
    for i in range(n):
        for j in range(i + 1, n):
            if units[i]["source_id"] == units[j]["source_id"]:
                if float(E[i] @ E[j]) >= emb.near_dup:      # same source: only near-duplicates
                    edges[(i, j)] = float(E[i] @ E[j])
                continue
            best = max((cos(a, b) for a in sents[i] for b in sents[j]), default=0.0)
            if best >= LEXICAL_CROSS_THRESHOLD:
                edges[(i, j)] = best
    return edges


def build_clusters(units: list[dict], E: np.ndarray, emb: Embedder, max_size: int = 8) -> list[list[int]]:
    n = len(units)
    if n < 2:
        return []
    src = [u["source_id"] for u in units]
    D, I = VectorIndex(E).search(E, min(12, n))
    edges: dict[tuple[int, int], float] = {}
    if not emb.semantic:
        edges = lexical_edges(units, D, I, E, emb)
        max_size = min(max_size, 5)
    else:
        for i in range(n):
            for s, j in zip(D[i], I[i]):
                j = int(j)
                if j == i or j < 0:
                    continue
                thr = emb.related if src[i] != src[j] else emb.near_dup
                if s >= thr:
                    key = (min(i, j), max(i, j))
                    edges[key] = max(edges.get(key, 0.0), float(s))
    parent = list(range(n))
    members = {i: [i] for i in range(n)}
    cent = {i: E[i].copy() for i in range(n)}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), _s in sorted(edges.items(), key=lambda kv: -kv[1]):
        ra, rb = find(a), find(b)
        if ra == rb or len(members[ra]) + len(members[rb]) > max_size:
            continue
        if emb.semantic:
            ca, cb = cent[ra] / (np.linalg.norm(cent[ra]) or 1), cent[rb] / (np.linalg.norm(cent[rb]) or 1)
            if float(ca @ cb) < emb.related * 0.92:
                continue
        parent[rb] = ra
        members[ra] += members.pop(rb)
        cent[ra] = cent[ra] + cent[rb]
    return [sorted(m) for m in members.values() if len(m) > 1]


def _cite(u: dict) -> str:
    return f"{u['source_id']} · {u['label']}"


def _mk_conflict(a: dict, b: dict, info: dict, method: str = "heuristic", ca: str | None = None, cb: str | None = None) -> dict:
    ca, cb = ca or a["text"], cb or b["text"]
    if info["kind"] == "numeric":
        expl = (f"Potential conflict (automatic check): the figures differ - {info['a']} in {a['source_id']} vs "
                f"{info['b']} in {b['source_id']}. Verify scope, date or measurement conditions.")
    elif info["kind"] == "polarity":
        expl = "Potential conflict (automatic check): one statement is negated or qualified where the other is not. Verify."
    else:
        expl = f"Potential conflict (automatic check): opposite direction ('{info.get('a')}' vs '{info.get('b')}'). Verify."
    return {"topic": shorten(ca, 90), "positions": [{"claim": ca, "unit_id": a["id"]}, {"claim": cb, "unit_id": b["id"]}],
            "explanation": expl, "method": method, "confidence": "potential"}


def heuristic_conflicts(cu: list[dict]) -> list[dict]:
    out, seen = [], set()
    for i, a in enumerate(cu):
        for b in cu[i + 1:]:
            if a["source_id"] == b["source_id"]:
                continue
            for sa in split_sentences(a["text"]):          # compare claim against claim, not paragraph against paragraph
                for sb in split_sentences(b["text"]):
                    info = detect_conflict(sa, sb)
                    if info and (sa, sb) not in seen:
                        seen.add((sa, sb))
                        out.append(_mk_conflict(a, b, info, ca=sa, cb=sb))
    return out[:4]


def heuristic_judge(cu: list[dict], sims: np.ndarray | None = None) -> dict:
    """Sentence-level novelty: keep the most informative unit, add only sentences that bring new content."""
    rep = max(cu, key=lambda u: (len(content_set(u["text"])), len(u["text"])))
    known = set(content_set(rep["text"]))
    details = []
    for u in sorted((x for x in cu if x is not rep), key=lambda x: -len(x["text"])):
        novel = []
        for s in split_sentences(u["text"]):
            cs = content_set(s)
            if len(cs) >= 3 and novelty(s, known) >= 0.40:
                novel.append(s)
                known |= cs
        if novel:
            details.append({"text": " ".join(novel), "unit_id": u["id"]})
    conflicts = heuristic_conflicts(cu)
    relation = "conflicting" if conflicts else ("complementary" if details else "duplicate")
    return {"relation": relation, "merged": rep["text"], "covers": [u["id"] for u in cu],
            "unique_details": details, "conflicts": conflicts, "standalone": [], "method": "heuristic"}


def _numbers(t: str) -> set[str]:
    return {m.rstrip(".,").replace(",", "") for m in re.findall(r"\d[\d,]*(?:\.\d+)?", t)}


def numbers_supported(text: str, sources: list[str]) -> list[str]:
    have = set()
    for s in sources:
        have |= _numbers(s)
    return sorted(n for n in _numbers(text) if n not in have)


def _prompt_for(batch: list[tuple[str, list[dict]]], hints: dict[str, list[dict]]) -> str:
    parts = ["Judge each cluster. Return JSON: {\"clusters\":[{\"cluster\":\"C1\",\"relation\":\"duplicate|complementary|conflicting|unrelated\","
             "\"merged\":\"...\",\"covers\":[\"U1\"],\"unique_details\":[{\"detail\":\"...\",\"unit\":\"U2\"}],"
             "\"conflicts\":[{\"topic\":\"...\",\"positions\":[{\"claim\":\"...\",\"unit\":\"U1\"},{\"claim\":\"...\",\"unit\":\"U2\"}],"
             "\"explanation\":\"...\"}],\"standalone\":[\"U3\"]}]}\n"]
    for cid, cu in batch:
        parts.append(f"\nCLUSTER {cid}")
        for u in cu:
            parts.append(f"[{u['h']}] ({_cite(u)}) {u['text']}")
        for h in hints.get(cid, []):
            parts.append(f"(automatic hint: {h['explanation']})")
    return "\n".join(parts)


def _norm_llm_result(res: dict, cu: list[dict], hmap: dict[str, dict]) -> dict | None:
    """Validate/normalise one LLM cluster verdict. Returns None if unusable (caller falls back to heuristics)."""
    ids = {u["h"] for u in cu}
    def valid(h): return isinstance(h, str) and h.strip("[] ") in ids
    clean = lambda h: hmap[h.strip("[] ")]["id"]
    rel = str(res.get("relation", "")).lower()
    if rel not in ("duplicate", "complementary", "conflicting", "unrelated", "mixed"):
        return None
    standalone = [clean(h) for h in res.get("standalone", []) if valid(h)]
    if rel == "unrelated":
        return {"relation": "unrelated", "merged": "", "covers": [], "unique_details": [], "conflicts": [],
                "standalone": [u["id"] for u in cu], "method": "llm"}
    merged = str(res.get("merged", "")).strip()
    covers = [clean(h) for h in res.get("covers", []) if valid(h)]
    if not merged or not covers:
        return None
    bad = numbers_supported(merged, [u["text"] for u in cu])
    if bad:
        log.warning("LLM merged text contained unsupported numbers %s - using heuristic merge", bad)
        return None
    details = []
    for d in res.get("unique_details", []) or []:
        if isinstance(d, dict) and valid(d.get("unit")) and str(d.get("detail", "")).strip():
            if not numbers_supported(str(d["detail"]), [u["text"] for u in cu]):
                details.append({"text": str(d["detail"]).strip(), "unit_id": clean(d["unit"])})
    conflicts = []
    for c in res.get("conflicts", []) or []:
        pos = [p for p in (c.get("positions") or []) if isinstance(p, dict) and valid(p.get("unit"))]
        if len(pos) >= 2 and len({p["unit"].strip("[] ") for p in pos}) >= 2:
            conflicts.append({"topic": str(c.get("topic", ""))[:140] or shorten(hmap[pos[0]["unit"].strip("[] ")]["text"], 90),
                              "positions": [{"claim": str(p.get("claim", "")).strip() or hmap[p["unit"].strip("[] ")]["text"],
                                             "unit_id": clean(p["unit"])} for p in pos],
                              "explanation": str(c.get("explanation", "")).strip(), "method": "llm", "confidence": "judged"})
    return {"relation": rel if rel != "mixed" else "complementary", "merged": merged, "covers": covers,
            "unique_details": details, "conflicts": conflicts, "standalone": standalone, "method": "llm"}


def analyse(units: list[dict], E: np.ndarray, emb: Embedder, progress, warnings: list[str]) -> dict:
    """Returns {items, conflicts, vecs, stats}."""
    for i, u in enumerate(units):
        u["h"] = f"U{i + 1}"
    hmap = {u["h"]: u for u in units}
    n = len(units)
    progress("comparison", 0.1, f"Searching {n} units for overlapping concepts…")
    clusters = build_clusters(units, E, emb)
    clusters.sort(key=lambda m: (-len({units[i]["source_id"] for i in m}), -len(m)))
    progress("comparison", 1.0, f"{len(clusters)} overlap cluster(s) found covering "
                                 f"{sum(len(c) for c in clusters)} of {n} units.")

    verdicts: dict[int, dict] = {}
    used_llm = llm.enabled
    budget = settings.llm_max_pairs
    batches: list[list[tuple[str, list[int]]]] = []
    heur_idx: list[int] = []
    cur, cur_chars = [], 0
    for ci, m in enumerate(clusters):
        if used_llm and budget > 0:
            sz = sum(len(units[i]["text"]) for i in m)
            if cur and (cur_chars + sz > 7000 or len(cur) >= 12):
                batches.append(cur)
                cur, cur_chars = [], 0
            cur.append((f"C{ci + 1}", m))
            cur_chars += sz
            budget -= len(m)
        else:
            heur_idx.append(ci)
    if cur:
        batches.append(cur)
    if used_llm and heur_idx:
        warnings.append(f"LLM_MAX_PAIRS limit reached: {len(heur_idx)} overlap cluster(s) were judged with offline heuristics.")

    done = 0
    for b in batches:
        cl = [(cid, [units[i] for i in m]) for cid, m in b]
        hints = {cid: heuristic_conflicts(cu) for cid, cu in cl}
        try:
            out = llm.complete_json(JUDGE_SYSTEM, _prompt_for(cl, hints))
            arr = out.get("clusters", out) if isinstance(out, dict) else out
            by = {str(r.get("cluster")): r for r in arr if isinstance(r, dict)}
        except LLMError as e:
            warnings.append(f"LLM judging failed for {len(b)} cluster(s) ({e}); heuristics were used instead.")
            by = {}
        for cid, m in b:
            ci = int(cid[1:]) - 1
            cu = [units[i] for i in m]
            v = _norm_llm_result(by[cid], cu, hmap) if cid in by else None
            if v is None:
                if cid in by:
                    warnings.append(f"Cluster {cid}: LLM verdict rejected (invalid or contained unsupported figures); heuristic merge used.")
                v = heuristic_judge(cu)
            verdicts[ci] = v
        done += len(b)
        progress("dedup", min(0.95, done / max(len(batches), 1)), f"Judged {done}/{len(batches)} batch(es) with {llm.model}…")
    for ci in heur_idx:
        verdicts[ci] = heuristic_judge([units[i] for i in clusters[ci]])
    progress("dedup", 0.97, "Assembling knowledge items…")

    # ---- build items; every unit is accounted for exactly once as covered / detail / conflict / standalone
    items: list[dict] = []
    conflicts: list[dict] = []
    in_cluster: set[int] = set()
    by_id = {u["id"]: u for u in units}
    id2i = {u["id"]: i for i, u in enumerate(units)}
    vecs: dict[str, np.ndarray] = {}

    def add_item(kind, relation, text, unit_ids, details, confl, method) -> dict:
        iid = f"K{len(items) + 1}"
        srcs = sorted({by_id[x]["source_id"] for x in unit_ids}, key=lambda s: int(s[1:]))
        item = {"id": iid, "kind": kind, "relation": relation, "text": text, "unit_ids": list(dict.fromkeys(unit_ids)),
                "details": details, "conflicts": [], "sources": srcs, "method": method}
        for c in confl:
            c = dict(c)
            c["id"] = f"X{len(conflicts) + 1}"
            c["item"] = iid
            conflicts.append(c)
            item["conflicts"].append(c["id"])
        v = E[[id2i[x] for x in item["unit_ids"]]].sum(axis=0)
        vecs[iid] = v / (np.linalg.norm(v) or 1)
        items.append(item)
        return item

    for ci, m in enumerate(clusters):
        v = verdicts[ci]
        cu = [units[i] for i in m]
        in_cluster.update(m)
        if v["relation"] == "unrelated":
            for u in cu:
                add_item("unique", "unique", u["text"], [u["id"]], [], [], v["method"])
            continue
        accounted = set(v["covers"]) | {d["unit_id"] for d in v["unique_details"]} | \
            {p["unit_id"] for c in v["conflicts"] for p in c["positions"]}
        stand = set(v["standalone"]) | {u["id"] for u in cu if u["id"] not in accounted}
        stand -= set(v["covers"])
        main_ids = [u["id"] for u in cu if u["id"] not in stand]
        if main_ids:
            add_item("merged" if len(main_ids) > 1 else "unique", v["relation"], v["merged"], main_ids,
                     v["unique_details"], v["conflicts"], v["method"])
        for u in cu:
            if u["id"] in stand:
                add_item("unique", "unique", u["text"], [u["id"]], [], [], v["method"])
    for i, u in enumerate(units):
        if i not in in_cluster:
            add_item("unique", "unique", u["text"], [u["id"]], [], [], "none")

    stats = {
        "units": n, "clusters": len(clusters), "items": len(items),
        "merged_items": sum(1 for it in items if it["kind"] == "merged"),
        "unique_items": sum(1 for it in items if it["kind"] == "unique"),
        "multi_source_items": sum(1 for it in items if len(it["sources"]) > 1),
        "details_preserved": sum(len(it["details"]) for it in items),
        "conflicts": len(conflicts), "llm_judged_clusters": sum(1 for v in verdicts.values() if v["method"] == "llm"),
        "heuristic_judged_clusters": sum(1 for v in verdicts.values() if v["method"] == "heuristic"),
    }
    # units whose information was folded into another unit's merged statement (the redundancy removed)
    stats["units_folded"] = sum(len(it["unit_ids"]) - 1 for it in items)
    return {"items": items, "conflicts": conflicts, "vecs": vecs, "stats": stats}
