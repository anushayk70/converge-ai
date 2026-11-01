"""Turn analysed knowledge items into the 13-section, source-attributed report.

Every section has an offline (extractive, verbatim) builder and, when an LLM is configured, an LLM writer.
LLM output is constrained: it may only cite [U#] handles that exist, numbers it cannot trace to the provided
statements are flagged as unverified, and any knowledge item the writer omitted is appended verbatim so nothing is lost.
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict

import numpy as np

from ..config import settings
from ..llm import LLMError, llm
from .analysis import numbers_supported
from .textutil import (is_application, is_definition, is_example, is_future, is_stat, is_technical, numeric_facts,
                       shorten)

SECTION_TITLES = [
    "Title and Research Topic", "Executive Summary", "Introduction and Background",
    "Fundamental Concepts and Definitions", "Detailed Topic-wise Explanations",
    "Technical Details, Examples, and Applications", "Important Statistics and Research Findings",
    "Comparative Analysis of Sources", "Contradictions and Differing Viewpoints",
    "Unique Insights from Individual Sources", "Knowledge Gaps and Limitations",
    "Conclusion and Future Scope", "References and Source Mapping",
]

WRITE_SYSTEM = """You are the report-writing engine of CONVERGE AI, a multi-source knowledge synthesis system.
You write ONE section of a research report using ONLY the numbered source statements provided.
Rules:
1. Never add facts, numbers, names or dates that are not in the statements. If something is unknown, say it cannot be verified from the sources.
2. Cite every factual sentence with the handles of ALL supporting statements in square brackets, e.g. [U3] or [U3][U7]. Only use handles that were given.
3. Preserve every distinct number, example, name and technical detail; merge only information that is genuinely repeated.
4. Where statements disagree, state the disagreement explicitly, attribute each position (by source id like S1), cite both, and do not pick a winner unless a statement itself gives the reason.
5. Write clear, well-organised prose; use short paragraphs, bullet lists or small Markdown tables where they aid reading. Do not use H1/H2 headings (H3 '###' is allowed). No preamble, no closing remarks, no meta-commentary about the task.
6. Output Markdown only."""


class Cites:
    """Maps between prompt handles (U#), unit ids and markdown citation links."""

    def __init__(self, units: list[dict]):
        self.units = units
        self.by_id = {u["id"]: u for u in units}
        self.by_h = {u["h"]: u for u in units}
        self.removed = 0

    def chip(self, u: dict) -> str:
        return f"[{u['source_id']} · {u['label']}](cite:{u['id']})"

    def chips(self, unit_ids: list[str], limit: int = 5) -> str:
        seen, out = set(), []
        for x in unit_ids:
            if x in seen or x not in self.by_id:
                continue
            seen.add(x)
            out.append(self.chip(self.by_id[x]))
        if len(out) > limit:
            extra = len(out) - limit
            out = out[:limit] + [f"*+{extra} more*"]
        return " ".join(out)

    def handles(self, unit_ids: list[str]) -> str:
        return "".join(f"[{self.by_id[x]['h']}]" for x in unit_ids[:6] if x in self.by_id)

    _GROUP = re.compile(r"[\[(]\s*(U\d+(?:\s*[,;&]\s*U\d+)*)\s*[\])]")

    def resolve(self, md: str) -> str:
        def rep(m):
            hs = re.findall(r"U\d+", m.group(1))
            ok = [self.by_h[h] for h in hs if h in self.by_h]
            self.removed += len(hs) - len(ok)
            return " ".join(self.chip(u) for u in ok)
        md = self._GROUP.sub(rep, md)
        left = len(re.findall(r"(?<![\w/])U\d+(?!\w)", md))
        if left:
            self.removed += left
            md = re.sub(r"\s?(?<![\w/])U\d+(?!\w)", "", md)
        return re.sub(r"(\S) {2,}", r"\1 ", md)


def strip_handles(t: str) -> str:
    t = re.sub(r"(?<![\w/])U\d+(?!\w)", "", t)
    t = re.sub(r"\bS\d+\b|\bSection \d+\b|\bK\d+\b|\bX\d+\b", "", t)
    return re.sub(r"(?m)^\s*\d+[.)]\s", "", t)


def _meaningful(nums: list[str]) -> list[str]:
    out = []
    for n in nums:
        try:
            if "." in n or float(n) >= 10:
                out.append(n)
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------------------------------------------
class Builder:
    def __init__(self, ws, resources, units, analysis, item_vecs, emb_name, warnings, progress):
        self.ws, self.res, self.units = ws, resources, units
        self.items, self.conflicts = analysis["items"], analysis["conflicts"]
        self.vecs = item_vecs
        self.warnings = warnings
        self.progress = progress
        self.c = Cites(units)
        self.by_item = {i["id"]: i for i in self.items}
        self.ready = [r for r in resources if r["status"] == "ready"]
        self.failed = [r for r in resources if r["status"] == "error"]
        self.src_name = {r["source_id"]: r["name"] for r in resources}
        self.uidx = {u["id"]: i for i, u in enumerate(units)}
        self.use_llm = llm.enabled
        self.llm_sections = 0
        self.fallback_sections = 0
        self.unverified: list[str] = []
        self.themes: list[dict] = []

    # ---- helpers -----------------------------------------------------------
    def item_units(self, it) -> list[str]:
        ids = list(it["unit_ids"])
        for d in it["details"]:
            ids.append(d["unit_id"])
        return ids

    def item_cites(self, it, limit: int = 4) -> str:
        return self.c.chips(self.item_units(it), limit)

    def rank(self, it):
        return (-len(it["sources"]), -len(it["unit_ids"]), self.uidx[it["unit_ids"][0]])

    def _material(self, its: list[dict], with_units: bool = True) -> str:
        lines = []
        for it in its:
            tag = "multi-source" if len(it["sources"]) > 1 else "single-source"
            lines.append(f"ITEM {it['id']} - {it['relation']} - {tag} ({', '.join(it['sources'])})")
            if len(it["unit_ids"]) > 1 and it["method"] != "none":
                lines.append(f"  consolidated: {it['text']}")
            for uid in it["unit_ids"]:
                u = self.c.by_id[uid]
                lines.append(f"  [{u['h']}] ({u['source_id']} · {u['label']}) {u['text']}")
            for d in it["details"]:
                lines.append(f"  unique detail from [{self.c.by_id[d['unit_id']]['h']}]: {d['text']}")
            for cid in it["conflicts"]:
                cf = next(c for c in self.conflicts if c["id"] == cid)
                pos = "; ".join(f"[{self.c.by_id[p['unit_id']]['h']}] {p['claim']}" for p in cf["positions"])
                lines.append(f"  CONFLICT {cid}: {pos} :: {cf['explanation']}")
        return "\n".join(lines)

    def _write(self, name: str, instruction: str, material: str, max_tokens: int = 1800) -> str | None:
        if not self.use_llm:
            return None
        try:
            txt = llm.complete(WRITE_SYSTEM, f"SECTION: {name}\n{instruction}\n\nSOURCE STATEMENTS:\n{material}", max_tokens)
        except LLMError as e:
            self.warnings.append(f"Section '{name}' was generated in extractive mode because the LLM call failed: {e}")
            self.fallback_sections += 1
            self.use_llm = self.use_llm and not str(e).startswith("The LLM provider rejected")
            return None
        txt = txt.strip()
        txt = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", txt)
        bad = _meaningful(numbers_supported(strip_handles(txt), [material, instruction, self.ws["name"], self.ws["description"] or ""]))
        txt = self.c.resolve(txt)
        if bad:
            self.unverified += bad
            txt += (f"\n\n> ⚠ **Verification note:** these figures could not be matched to any source statement and should be "
                    f"treated as unverified: {', '.join(bad)}.")
        self.llm_sections += 1
        return txt

    # ---- themes ------------------------------------------------------------
    def make_themes(self) -> None:
        its = self.items
        n = len(its)
        if n == 0:
            return
        k = 1 if n < 6 else max(2, min(8, round((n / 2) ** 0.5)))
        labels = np.zeros(n, dtype=int)
        if k > 1:
            from sklearn.cluster import KMeans
            X = np.stack([self.vecs[i["id"]] for i in its])
            labels = KMeans(n_clusters=k, n_init=5, random_state=0).fit_predict(X)
        groups: dict[int, list[dict]] = defaultdict(list)
        for it, lb in zip(its, labels):
            groups[int(lb)].append(it)
        themes = [{"items": sorted(g, key=lambda it: (self.uidx[it["unit_ids"][0]]))} for g in groups.values()]
        themes.sort(key=lambda t: min(self.uidx[i["unit_ids"][0]] for i in t["items"]))
        titles = self._llm_titles(themes) if self.use_llm else None
        keywords = self._keywords(themes)
        for i, t in enumerate(themes):
            t["title"] = (titles[i] if titles and i < len(titles) and titles[i] else keywords[i])
            t["sources"] = sorted({s for it in t["items"] for s in it["sources"]}, key=lambda s: int(s[1:]))
        self.themes = themes
        for ti, t in enumerate(themes):
            for it in t["items"]:
                it["theme"] = ti

    def _keywords(self, themes) -> list[str]:
        from sklearn.feature_extraction.text import TfidfVectorizer
        docs = [" ".join(i["text"] for i in t["items"]) for t in themes]
        if len(docs) == 1:
            vec = TfidfVectorizer(stop_words="english", token_pattern=r"[A-Za-z][A-Za-z\-]{3,}")
        else:
            vec = TfidfVectorizer(stop_words="english", token_pattern=r"[A-Za-z][A-Za-z\-]{3,}", max_df=0.8)
        try:
            M = vec.fit_transform(docs).toarray()
        except ValueError:
            return [f"Theme {i + 1}" for i in range(len(docs))]
        terms = np.array(vec.get_feature_names_out())
        out = []
        for i, row in enumerate(M):
            top = [t for t in terms[np.argsort(-row)[:3]] if row[list(terms).index(t)] > 0]
            out.append(", ".join(w.capitalize() for w in top) or f"Theme {i + 1}")
        return out

    def _llm_titles(self, themes) -> list[str] | None:
        lines = []
        for i, t in enumerate(themes):
            lines.append(f"THEME {i + 1}:")
            for it in t["items"][:6]:
                lines.append("  - " + shorten(it["text"], 170))
        try:
            out = llm.complete_json("You name sections of a research report. Give each theme a concise, specific title (2-6 words, "
                                    "Title Case, no numbering) based only on its statements.",
                                    "Return JSON {\"titles\":[\"...\", ...]} with exactly one title per theme, in order.\n\n" + "\n".join(lines), 600)
            ts = out.get("titles") if isinstance(out, dict) else out
            if isinstance(ts, list) and len(ts) == len(themes):
                return [re.sub(r"^\W+|\W+$", "", str(x))[:70] for x in ts]
        except LLMError as e:
            self.warnings.append(f"Theme titles were derived from keywords because the LLM call failed: {e}")
        return None

    # ---- offline renderers -------------------------------------------------
    def item_bullet(self, it, show_details: bool = True) -> str:
        line = f"- {it['text']} {self.item_cites(it)}"
        if len(it["sources"]) > 1:
            line += f" — *consistent across {', '.join(it['sources'])}*"
        out = [line]
        if show_details:
            for d in it["details"]:
                u = self.c.by_id[d["unit_id"]]
                out.append(f"  - *Additional detail ({u['source_id']}):* {d['text']} {self.c.chip(u)}")
        for cid in it["conflicts"]:
            out.append(f"  - ⚠ *Sources disagree on this point — see Section 9 ({cid}).*")
        return "\n".join(out)

    # ---- one home per statement ---------------------------------------------
    def assign_homes(self) -> None:
        """Give every distinct statement exactly one home among the narrative sections (3, 4, 5, 6).

        Without this, the same statement would be re-listed by the introduction, the definitions, the topic sections and the
        technical section, undoing the deduplication in the final document. The executive summary, the statistics table and
        the per-source index (sections 2, 7, 10) remain compact *views* that may point at a statement again.
        """
        ranked = sorted(self.items, key=self.rank)
        self.home: dict[str, int] = {}
        central = sorted(self.items, key=lambda it: -float(np.mean([self.vecs[it["id"]] @ self.vecs[j["id"]] for j in self.items[:200]])))[:6]
        self.central = sorted(central, key=lambda it: self.uidx[it["unit_ids"][0]])
        for it in self.central:
            self.home[it["id"]] = 3
        self.defs = [it for it in ranked if it["id"] not in self.home and is_definition(it["text"])][:18]
        for it in self.defs:
            self.home[it["id"]] = 4
        self.groups6 = []
        for name, test, cap in (("Examples", is_example, 10), ("Applications", is_application, 12),
                                ("Technical details", lambda t: is_stat(t) and is_technical(t), 14)):
            its = [it for it in ranked if it["id"] not in self.home and test(it["text"])][:cap]
            for it in its:
                self.home[it["id"]] = 6
            if its:
                self.groups6.append((name, its))
        order = {"Technical details": 0, "Examples": 1, "Applications": 2}
        self.groups6.sort(key=lambda g: order[g[0]])
        for it in self.items:
            self.home.setdefault(it["id"], 5)

    # ---- sections ----------------------------------------------------------
    def s1(self) -> str:
        ws = self.ws
        mode = ("LLM-assisted synthesis (" + f"{llm.provider}/{llm.model})" if llm.enabled else
                "offline extractive synthesis (no LLM configured; every statement below is taken verbatim from the sources)")
        rows = "\n".join(f"| {r['source_id']} | {r['name']} | {r['kind'].upper()} | {r['stats'].get('units', 0)} |"
                         for r in self.ready)
        return (f"# {ws['name']}\n\n**Research topic:** {ws['name']}\n\n"
                + (f"**Scope / description:** {ws['description']}\n\n" if ws.get("description") else "")
                + f"**Generated:** {time.strftime('%d %B %Y, %H:%M', time.localtime())} · **Method:** {mode}\n\n"
                  f"**Sources synthesised ({len(self.ready)}):**\n\n| ID | Resource | Type | Knowledge units |\n|---|---|---|---|\n{rows}\n\n"
                  "*Citations such as* **[S1 · p. 3]** *point to the original source and location; open the report viewer and click one to see "
                  "the exact passage. Statements marked ⚠ need verification.*")

    def s2(self) -> str:
        ranked = sorted(self.items, key=self.rank)
        top = [i for i in ranked if len(i["sources"]) > 1][:5]
        for t in self.themes:  # breadth: best statement of every topic
            best = next((i for i in sorted(t["items"], key=self.rank) if i not in top), None)
            if best:
                top.append(best)
        top = sorted(top[:12], key=self.rank)
        if self.use_llm:
            lines = [f"- {it['text']} {self.c.handles(it['unit_ids'])} (sources: {', '.join(it['sources'])})" for it in top]
            ex = self._write("Executive Summary",
                             "Write an executive summary (150-250 words) for a decision-maker: what the sources collectively establish, "
                             "the most strongly corroborated points, the key numbers, and an explicit one-sentence note on any disagreement "
                             f"between sources ({len(self.conflicts)} conflict(s) were flagged). Topic: {self.ws['name']}. "
                             f"Sources: {len(self.ready)}.", "\n".join(lines), 900)
            if ex:
                return ex
        multi = sum(1 for i in self.items if len(i["sources"]) > 1)
        out = [f"This report consolidates **{len(self.ready)} sources** on *{self.ws['name']}*. Their {len(self.units)} knowledge units were compared "
               f"semantically and reduced to **{len(self.items)} distinct statements**; {multi} are supported by more than one source and "
               f"**{len(self.conflicts)}** potential disagreement(s) were flagged. *(Extractive summary: points below are verbatim from the sources, "
               "ranked by how many sources support them.)*", "", "**Key points**", ""]
        out += [self.item_bullet(it, show_details=False) for it in top[:7]]
        return "\n".join(out)

    def s3(self) -> str:
        central = self.central
        if self.use_llm:
            t = self._write("Introduction and Background",
                            "Write an introduction and background (150-300 words) that situates the topic and explains why these sources matter. "
                            f"Topic: {self.ws['name']}. User's scope note: {self.ws['description'] or 'none'}. Use only the statements for factual claims.",
                            "\n".join(f"- {it['text']} {self.c.handles(it['unit_ids'])}" for it in central), 1100)
            if t:
                return t
        intro = f"This report examines **{self.ws['name']}**" + (f" — {self.ws['description'].rstrip('.')}" if self.ws.get("description") else "") + \
            ". The statements most central to the collected material are:\n"
        return intro + "\n" + "\n".join(self.item_bullet(it, False) for it in central)

    def s4(self) -> str:
        defs = self.defs
        if not defs:
            return "*No explicit definitions were found in the supplied sources. Definitions are not supplied from outside knowledge, to avoid unverifiable claims.*"
        if self.use_llm:
            t = self._write("Fundamental Concepts and Definitions",
                            "Write a glossary-style section: for each key concept, a short definition drawn strictly from the statements "
                            "(bold the term). Merge repeated definitions; keep differences in wording that change meaning.",
                            self._material(defs), 1800)
            if t:
                return t
        return "\n".join(self.item_bullet(it) for it in defs)

    def s5(self) -> str:
        out = []
        budget = settings.llm_context_chars
        for t in self.themes:
            titems = [it for it in t["items"] if self.home.get(it["id"]) == 5]
            if not titems:
                continue
            body = None
            if self.use_llm:
                parts, cur, size = [], [], 0
                for it in titems:
                    m = self._material([it])
                    if cur and size + len(m) > budget:
                        parts.append(cur)
                        cur, size = [], 0
                    cur.append(it)
                    size += len(m)
                if cur:
                    parts.append(cur)
                texts = []
                for p in parts:
                    r = self._write(f"Topic: {t['title']}",
                                    "Explain this topic in detail for the report. Organise logically (definition -> mechanism/explanation -> "
                                    "implications). Keep every unique detail, example and number; merge repeated information into single "
                                    "sentences citing all supporting sources; call out disagreements. Do not repeat the topic title as a heading.",
                                    self._material(p), 2600)
                    if r is None:
                        texts = None
                        break
                    texts.append(r)
                body = "\n\n".join(texts) if texts else None
            if body is None:
                body = "\n".join(self.item_bullet(it) for it in titems)
            else:
                cited = set(re.findall(r"cite:(u_\w+)", body))
                missed = [it for it in titems if not (cited & set(self.item_units(it)))]
                if missed:
                    body += ("\n\n**Additional details preserved from the sources** *(not covered by the narrative above)*:\n\n"
                             + "\n".join(self.item_bullet(it) for it in missed))
            srcs = ", ".join(t["sources"])
            out.append(f"## {t['title']}\n\n*Covered by {srcs}" + (" — single-source topic, not independently corroborated" if len(t["sources"]) == 1 and len(self.ready) > 1 else "") + "*\n\n" + body)
        note = ("*Statements that fit a more specific role are placed once, in Section 3 (introduction), 4 (definitions) or 6 "
                "(technical details, examples, applications), not repeated here.*\n\n")
        return note + "\n\n".join(out) if out else "*No content.*"

    def s6(self) -> str:
        groups = self.groups6
        if not groups:
            return "*No further technical details, examples or applications beyond those placed in the other sections.*"
        if self.use_llm:
            mat = "\n\n".join(f"== {g} ==\n{self._material(its)}" for g, its in groups)
            t = self._write("Technical Details, Examples, and Applications",
                            "Write three '###' subsections: Technical details, Examples, Applications (omit a subsection if no statements fit). "
                            "Be specific and keep all parameters and numbers exactly as given.", mat[: settings.llm_context_chars], 2600)
            if t:
                return t
        return "\n\n".join(f"### {g}\n\n" + "\n".join(self.item_bullet(it, False) for it in its) for g, its in groups if its)

    def stat_rows(self) -> list[tuple[dict, list]]:
        rows = []
        for it in sorted(self.items, key=self.rank):
            texts = [it["text"]] + [d["text"] for d in it["details"]]
            facts = [f for t in texts for f in numeric_facts(t) if f.unit and f.unit != "year"]
            if facts:
                rows.append((it, facts))
        return rows

    def s7(self) -> str:
        rows = self.stat_rows()
        if not rows:
            return "*No quantitative statistics were found in the supplied sources.*"
        tbl = ["| Finding | Reported figure(s) | Sources |", "|---|---|---|"]
        for it, facts in rows[:30]:
            vals = ", ".join(dict.fromkeys(f.raw for f in facts))[:80]
            flag = " ⚠" if it["conflicts"] else ""
            tbl.append(f"| {shorten(it['text'], 200).replace('|', '/')}{flag} | {vals} | {self.item_cites(it, 3)} |")
        intro = ""
        if self.use_llm:
            intro = self._write("Important Statistics and Research Findings",
                                "Write a short paragraph (80-150 words) highlighting the most important quantitative findings and noting where "
                                "figures from different sources agree or differ. A full table follows; do not repeat it.",
                                self._material([r[0] for r in rows[:14]]), 700) or ""
        note = "\n\n*Figures are quoted verbatim from the sources; ⚠ marks a finding whose figures are disputed (see Section 9).*"
        return (intro + "\n\n" if intro else "") + "\n".join(tbl) + (f"\n\n*Showing 30 of {len(rows)} findings.*" if len(rows) > 30 else "") + note

    def source_stats(self) -> dict:
        st = {}
        for r in self.ready:
            sid = r["source_id"]
            mine = [it for it in self.items if sid in it["sources"]]
            st[sid] = {
                "name": r["name"], "kind": r["kind"], "units": r["stats"].get("units", 0),
                "items": len(mine), "shared": sum(1 for i in mine if len(i["sources"]) > 1),
                "unique": sum(1 for i in mine if i["sources"] == [sid]),
                "details": sum(1 for i in self.items for d in i["details"] if self.c.by_id[d["unit_id"]]["source_id"] == sid),
                "conflicts": sum(1 for c in self.conflicts if any(self.c.by_id[p["unit_id"]]["source_id"] == sid for p in c["positions"])),
                "themes": sorted({i["theme"] for i in mine if "theme" in i}),
            }
        return st

    def s8(self) -> str:
        st = self.source_stats()
        t = ["| Source | Type | Units | Shared with other sources | Unique statements | Added details | In a disagreement |",
             "|---|---|---|---|---|---|---|"]
        for sid, s in st.items():
            t.append(f"| **{sid}** {shorten(s['name'], 40)} | {s['kind'].upper()} | {s['units']} | {s['shared']} | {s['unique']} | {s['details']} | {s['conflicts']} |")
        sids = list(st)
        cover = []
        if 1 < len(sids) <= 8:
            cover = ["", "**Topic coverage by source**", "", "| Topic | " + " | ".join(sids) + " |", "|---|" + "---|" * len(sids)]
            for ti, th in enumerate(self.themes):
                cover.append(f"| {th['title']} | " + " | ".join("●" if ti in st[s]["themes"] else "·" for s in sids) + " |")
        pairs = Counter()
        for it in self.items:
            ss = it["sources"]
            for i in range(len(ss)):
                for j in range(i + 1, len(ss)):
                    pairs[(ss[i], ss[j])] += 1
        lines = []
        if pairs:
            (a, b), n = pairs.most_common(1)[0]
            lines.append(f"- Greatest overlap: **{a}** and **{b}** share {n} consolidated statement(s).")
        if st:
            top = max(st.items(), key=lambda kv: kv[1]["unique"] + kv[1]["details"])
            lines.append(f"- Largest unique contribution: **{top[0]}** ({top[1]['unique']} statements no other source makes, plus {top[1]['details']} added detail(s)).")
            if any(s["conflicts"] for s in st.values()):
                names = [k for k, s in st.items() if s["conflicts"]]
                lines.append(f"- Sources involved in flagged disagreements: {', '.join(names)}.")
        narrative = ""
        if self.use_llm:
            facts = "\n".join(f"{k}: {v['name']} ({v['kind']}) - shared {v['shared']}, unique {v['unique']}, details {v['details']}, conflicts {v['conflicts']}" for k, v in st.items())
            top_unique = []
            for sid in st:
                its = [i for i in self.items if i["sources"] == [sid]][:3]
                top_unique.append(f"Examples unique to {sid}:\n" + self._material(its) if its else "")
            narrative = self._write("Comparative Analysis of Sources",
                                    "In 100-180 words compare how the sources differ in emphasis, depth, perspective and type of evidence, "
                                    "citing example statements. Only describe differences visible in the statements and counts below.",
                                    facts + "\n\n" + "\n".join(top_unique), 900) or ""
        return "\n".join(t + cover) + "\n\n" + "\n".join(lines) + ("\n\n" + narrative if narrative else "")

    def s9(self) -> str:
        if not self.conflicts:
            return ("**No contradictions were detected between the sources.**\n\n*Detection compares semantically overlapping statements "
                    "(" + ("LLM-judged" if llm.enabled else "offline heuristic: differing figures, negation, opposite direction") +
                    "). Disagreements between statements that were never matched as overlapping cannot be found automatically.*")
        out = []
        for c in self.conflicts:
            rows = []
            for p in c["positions"]:
                u = self.c.by_id[p["unit_id"]]
                rows.append(f"- **{u['source_id']}** ({self.src_name.get(u['source_id'], '')}): “{shorten(p['claim'], 300)}” {self.c.chip(u)}")
            conf = ("Judged by the LLM" if c["method"] == "llm" else "**Potential conflict** — flagged by an automatic check; may be a difference of scope, date or definition")
            out.append(f"### {c['id']}: {shorten(c['topic'], 100)}\n\n" + "\n".join(rows)
                       + f"\n\n*{conf}.* {c['explanation'] if c['method'] == 'llm' else c['explanation'].replace('Potential conflict (automatic check): ', '')}\n\n"
                         "*Not resolved: this report does not pick a winner. Check the original passages above.*")
        return "\n\n".join(out)

    def s10(self) -> str:
        out = []
        for r in self.ready:
            sid = r["source_id"]
            uniq = [it for it in self.items if it["sources"] == [sid] and len(it["unit_ids"]) == 1]
            adds = [(it, d) for it in self.items for d in it["details"] if self.c.by_id[d["unit_id"]]["source_id"] == sid]
            def score(it):
                t = it["text"]
                return (is_stat(t) * 3 + is_example(t) * 2 + is_technical(t) + min(len(t) / 300, 1))
            top = sorted(uniq, key=lambda i: -score(i))[:3]
            block = [f"### {sid} — {r['name']}", ""]
            if not top and not adds:
                block.append("*No statements unique to this source: everything it says is also supported by at least one other source.*")
            else:
                block.append(f"*{len(uniq)} statement(s) found only here; {len(adds)} detail(s) added to topics other sources also cover.*\n")
                block += [f"- {shorten(it['text'], 170)} {self.item_cites(it, 2)}" for it in top]
                for it, d in adds[:3]:
                    block.append(f"- *Adds to a shared topic:* {shorten(d['text'], 170)} {self.c.chip(self.c.by_id[d['unit_id']])}")
                if len(uniq) > len(top):
                    block.append(f"- *…and {len(uniq) - len(top)} more statement(s) unique to this source, in Sections 3–6.*")
            out.append("\n".join(block))
        return "\n\n".join(out)

    def s11(self) -> str:
        g = []
        for t in self.themes:
            if len(t["sources"]) == 1 and len(self.ready) > 1:
                g.append(f"- **Single-source topic:** “{t['title']}” is covered only by {t['sources'][0]}; nothing in the other sources corroborates or challenges it.")
        single = sum(1 for i in self.items if len(i["sources"]) == 1)
        if self.items:
            g.append(f"- {single} of {len(self.items)} statements ({100 * single // len(self.items)}%) rest on a single source and could not be cross-checked.")
        if self.conflicts:
            g.append(f"- {len(self.conflicts)} disagreement(s) between sources remain unresolved (Section 9).")
        for r in self.failed:
            g.append(f"- **{r['source_id']} {r['name']}** could not be processed and is NOT reflected in this report: {r['error']}")
        for r in self.ready:
            for w in r["warnings"][:3]:
                g.append(f"- {r['source_id']} {r['name']}: {w}")
        if len(self.ready) < 2:
            g.append("- Only one source was synthesised, so cross-source comparison and contradiction detection were not possible.")
        if not llm.enabled:
            g.append("- No LLM was configured: deduplication and conflict checks used lexical/numeric heuristics, so subtle paraphrases or "
                     "conflicts may be missed. Statements are verbatim extracts rather than rewritten prose.")
        if not self.stat_rows():
            g.append("- No quantitative evidence was found in the sources.")
        if self.use_llm:
            titles = "\n".join(f"- {t['title']}: " + "; ".join(shorten(i['text'], 90) for i in t['items'][:3]) for t in self.themes)
            sug = self._write("Knowledge Gaps (model-suggested)",
                              "List 3-5 important sub-topics, questions or kinds of evidence that a reader of this topic would expect but the "
                              "statements below do NOT address. These come from general knowledge, not from the sources; start the list with the "
                              "sentence '*Suggested by the model from general knowledge — not found in your sources:*'. Do not state facts, only gaps. No citations.",
                              titles, 600)
            if sug:
                g.append("\n" + sug)
        return "\n".join(g) if g else "*No specific gaps were identified.*"

    def s12(self) -> str:
        fut = [it for it in sorted(self.items, key=self.rank) if is_future(it["text"])][:10]
        if self.use_llm:
            top = sorted(self.items, key=self.rank)[:10]
            t = self._write("Conclusion and Future Scope",
                            "Write a conclusion (120-220 words) synthesising the overall picture, then a '### Future scope' list drawn ONLY from "
                            "statements about future developments, plans, forecasts or open questions. Mention unresolved disagreements.",
                            "== most corroborated ==\n" + self._material(top) + "\n\n== forward-looking ==\n" + self._material(fut), 1400)
            if t:
                return t
        multi = [i for i in self.items if len(i["sources"]) > 1]
        c = (f"Across {len(self.ready)} sources, {len(multi)} of {len(self.items)} distinct statements are corroborated by more than one source"
             + (f", while {len(self.conflicts)} point(s) of disagreement remain open" if self.conflicts else "") + ". "
             "*(Extractive conclusion: no new claims are introduced.)*")
        if fut:
            c += "\n\n### Future scope according to the sources\n\n" + "\n".join(self.item_bullet(it, False) for it in fut)
        else:
            c += "\n\n*The sources contain no explicit forward-looking statements.*"
        return c

    def s13(self, sections_md: str) -> str:
        counts = Counter(self.c.by_id[x]["source_id"] for x in re.findall(r"cite:(u_\w+)", sections_md) if x in self.c.by_id)
        rows = ["| ID | Resource | Type | Origin | Units | Times cited |", "|---|---|---|---|---|---|"]
        for r in self.resources_all():
            origin = r["origin"] if r["origin"] else "uploaded file"
            status = "" if r["status"] == "ready" else " *(failed — not included)*"
            rows.append(f"| **{r['source_id']}** | {r['name']}{status} | {r['kind'].upper()} | {shorten(origin, 60)} | {r['stats'].get('units', 0)} | {counts.get(r['source_id'], 0)} |")
        return ("\n".join(rows) + "\n\n*Every citation in this report links to a stored passage; the exact original text, page or timestamp is "
                "shown when you click it. This report was generated from the listed resources only.*")

    def resources_all(self):
        return self.res

    # ---- orchestrate ---------------------------------------------------------
    def build(self) -> list[dict]:
        self.progress("synthesis", 0.05, "Grouping knowledge into topics…")
        self.make_themes()
        self.assign_homes()
        steps = [("s2", "Executive summary"), ("s3", "Introduction"), ("s4", "Definitions"), ("s5", "Topic-wise explanations"),
                 ("s6", "Technical details"), ("s7", "Statistics"), ("s8", "Source comparison"), ("s9", "Contradictions"),
                 ("s10", "Unique insights"), ("s11", "Gaps"), ("s12", "Conclusion")]
        built: dict[str, str] = {"s1": self.s1()}
        for i, (key, label) in enumerate(steps):
            self.progress("synthesis" if i < 6 else "report", (i + 1) / len(steps), f"Writing: {label}…")
            built[key] = getattr(self, key)()
        body = "\n".join(built.values())
        built["s13"] = self.s13(body)
        sections = []
        for n, title in enumerate(SECTION_TITLES, 1):
            sections.append({"id": f"s{n}", "number": n, "title": title, "markdown": built[f"s{n}"]})
        return sections
