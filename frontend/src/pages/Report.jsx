import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api.js";
import Markdown from "../components/Markdown.jsx";
import CitationDrawer from "../components/CitationDrawer.jsx";

export default function Report() {
  const { id } = useParams();
  const [rep, setRep] = useState(null);
  const [err, setErr] = useState("");
  const [cite, setCite] = useState(null);
  const [view, setView] = useState("report");
  const [active, setActive] = useState(1);

  useEffect(() => { api.report(id).then(setRep).catch((e) => setErr(e.message)); }, [id]);
  useEffect(() => {
    if (!rep || view !== "report") return;
    const els = rep.sections.map((s) => document.getElementById("sec-" + s.number)).filter(Boolean);
    const io = new IntersectionObserver((es) => { const v = es.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]; if (v) setActive(+v.target.id.slice(4)); }, { rootMargin: "-10% 0px -75% 0px" });
    els.forEach((e) => io.observe(e));
    return () => io.disconnect();
  }, [rep, view]);

  const srcName = useMemo(() => Object.fromEntries((rep?.sources || []).map((s) => [s.source_id, s.name])), [rep]);
  const openRaw = (u) => setCite({ source_id: u.source_id, label: u.label, source_name: srcName[u.source_id] || u.source_id, text: u.text });

  if (err) return (
    <div className="card empty"><h2>No report yet</h2><p className="muted">{err}</p><Link className="btn primary" to={`/app/w/${id}/process`}>Go to processing →</Link></div>);
  if (!rep) return <div className="row muted"><span className="spinner" /> Loading report…</div>;
  const st = rep.stats;
  const extractive = rep.mode.synthesis === "extractive";

  return (
    <>
      <div className="pagehead">
        <div><h1>{rep.title}</h1><p className="muted">Generated {new Date(rep.generated_at * 1000).toLocaleString()} · {rep.sources.filter((s) => s.status === "ready").length} sources</p></div>
        <div className="row wrap">
          <a className="btn primary" href={api.exportUrl(id, "pdf")} download>⬇ PDF</a>
          <a className="btn primary" href={api.exportUrl(id, "docx")} download>⬇ DOCX</a>
          <a className="btn" href={api.exportUrl(id, "md")} download>Markdown</a>
          <Link className="btn" to={`/app/w/${id}/chat`}>✦ Ask sources</Link>
        </div>
      </div>
      {rep.stale && <div className="alert warn" style={{ marginBottom: 14 }}>The resources have changed since this report was generated. <Link to={`/app/w/${id}/process`}>Re-run synthesis →</Link></div>}
      {extractive && <div className="alert info" style={{ marginBottom: 14 }}><span>ℹ</span><div><b>Offline extractive mode.</b> No LLM was configured, so this report assembles verbatim statements from your sources (merged, de-duplicated and cited) rather than writing new prose. Configure <code>LLM_PROVIDER</code> for narrative synthesis.</div></div>}
      {rep.warnings.length > 0 && <details className="alert warn" style={{ marginBottom: 14, display: "block" }}><summary style={{ cursor: "pointer" }}><b>{rep.warnings.length} note{rep.warnings.length > 1 ? "s" : ""} about this report</b></summary>{rep.warnings.map((w, i) => <div key={i} className="small" style={{ marginTop: 6 }}>• {w}</div>)}</details>}

      <div className="grid cols4" style={{ marginBottom: 20 }}>
        <div className="stat"><b>{st.units}</b><span>passages read</span></div>
        <div className="stat"><b>{st.items}</b><span>distinct statements</span></div>
        <div className="stat"><b>{st.units_folded}</b><span>redundant passages folded in</span></div>
        <div className="stat"><b>{st.details_preserved}</b><span>unique details preserved</span></div>
        <div className="stat"><b>{st.conflicts}</b><span>conflicts flagged</span></div>
      </div>

      <div className="tabs">
        {[["report", "Report"], ["merge", `How sources were merged (${rep.consolidations.length})`], ["conflicts", `Conflicts (${rep.conflicts.length})`], ["sources", "Sources"]].map(([k, l]) => <button key={k} className={view === k ? "on" : ""} onClick={() => setView(k)}>{l}</button>)}
      </div>

      {view === "report" && (
        <div className="rep">
          <nav className="toc card flat" style={{ padding: 12 }} aria-label="Table of contents">
            <div className="small muted" style={{ padding: "4px 12px 8px", textTransform: "uppercase", letterSpacing: ".08em" }}>Contents</div>
            {rep.sections.map((s) => <a key={s.number} href={`#sec-${s.number}`} className={active === s.number ? "on" : ""} onClick={() => setActive(s.number)}>{s.number}. {s.title}</a>)}
          </nav>
          <article className="paper">
            {rep.sections.map((s) => (
              <section key={s.number} id={"sec-" + s.number}>
                {s.number > 1 && <><div className="secnum">Section {s.number}</div><h2>{s.title}</h2></>}
                <Markdown text={s.markdown} citations={rep.citations} onCite={(cid) => setCite(rep.citations[cid])} />
              </section>
            ))}
          </article>
        </div>
      )}

      {view === "merge" && (
        <div className="stack">
          <p className="muted">Each card is a group of passages from different places that were found to overlap. The merged statement appears once in the report; details that only one source adds are kept, with attribution. <b>Click a passage to see the original.</b></p>
          {rep.consolidations.length === 0 && <div className="card empty muted">No overlapping passages were found.</div>}
          {rep.consolidations.map((c) => (
            <div className="card" key={c.item}>
              <div className="row wrap" style={{ marginBottom: 8, gap: 6 }}>
                <span className={"pill " + (c.relation === "duplicate" ? "ok" : c.relation === "conflicting" ? "warn" : "")}>{c.relation}</span>
                {c.sources.map((s) => <span className="pill blue" key={s}>{s}</span>)}
                <span className="muted small">judged by {c.method === "llm" ? "LLM" : "heuristics"}</span>
              </div>
              <div style={{ fontWeight: 500, marginBottom: 12 }}>{c.text}</div>
              <div className="small muted" style={{ marginBottom: 6 }}>{c.units.length} source passages</div>
              {c.units.map((u) => (
                <div key={u.unit_id} className="passage" style={{ cursor: "pointer", margin: "6px 0" }} onClick={() => openRaw(u)}>
                  <span className="pill blue" style={{ marginRight: 8 }}>{u.source_id} · {u.label}</span>{u.text.length > 300 ? u.text.slice(0, 300) + "…" : u.text}
                </div>
              ))}
              {c.details.length > 0 && <div className="small" style={{ marginTop: 10 }}><b>Unique details retained:</b>{c.details.map((d, i) => <div key={i} style={{ marginTop: 4 }}>＋ <span className="pill">{d.source_id}</span> {d.text.length > 240 ? d.text.slice(0, 240) + "…" : d.text}</div>)}</div>}
            </div>
          ))}
        </div>
      )}

      {view === "conflicts" && (
        <div>
          {rep.conflicts.length === 0 && <div className="card empty muted">No contradictory claims were detected between sources.</div>}
          {rep.conflicts.map((c) => (
            <div className="conf" key={c.id}>
              <div className="row spread"><b>{c.topic}</b><span className={"pill " + (c.confidence === "judged" ? "bad" : "warn")}>{c.confidence === "judged" ? "judged by LLM" : "potential — verify"}</span></div>
              {c.positions.map((p, i) => (<blockquote key={i} onClick={() => openRaw({ ...p, text: p.claim })} style={{ cursor: "pointer" }}><span className="pill blue">{p.source_id} · {p.label}</span> {p.claim}</blockquote>))}
              <div className="small muted">{c.explanation}</div>
            </div>
          ))}
        </div>
      )}

      {view === "sources" && (
        <div className="card"><table className="compare-table" style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th>ID</th><th>Resource</th><th>Type</th><th>Status</th><th>Units</th></tr></thead>
          <tbody>{rep.sources.map((s) => <tr key={s.source_id}><td><span className="pill blue">{s.source_id}</span></td><td>{s.origin ? <a href={s.origin} target="_blank" rel="noreferrer">{s.name}</a> : s.name}{s.error && <div className="errtext">{s.error}</div>}</td><td>{s.kind}</td><td><span className={"pill " + (s.status === "ready" ? "ok" : "bad")}>{s.status}</span></td><td>{s.units}</td></tr>)}</tbody></table></div>
      )}
      <CitationDrawer cite={cite} onClose={() => setCite(null)} />
    </>
  );
}
