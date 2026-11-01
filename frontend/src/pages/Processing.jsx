import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api.js";

const ICON = { done: "✓", error: "!", pending: "" };

export default function Processing() {
  const { id } = useParams();
  const [job, setJob] = useState(null);
  const [err, setErr] = useState("");
  const running = job?.status === "running";
  const load = () => api.job(id).then(setJob).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, [id]);
  useEffect(() => { if (!running) return; const t = setInterval(load, 1000); return () => clearInterval(t); }, [running, id]);
  async function start() {
    setErr("");
    try { setJob(await api.process(id)); } catch (e) { setErr(e.message); }
  }
  if (!job) return <div className="row muted">{err ? <div className="alert bad">{err}</div> : <><span className="spinner" /> Loading…</>}</div>;
  const total = job.stages.reduce((a, s) => a + (s.status === "done" ? 1 : s.progress || 0), 0) / job.stages.length;
  const s = job.summary;
  return (
    <>
      <div className="pagehead">
        <div><h1>Processing</h1><p className="muted">Extract → embed → compare → deduplicate → synthesise → validate.</p></div>
        <div className="row">
          {job.status === "done" && <Link className="btn primary" to={`/app/w/${id}/report`}>Open report →</Link>}
          <button className="btn" onClick={start} disabled={running}>{job.status === "idle" ? "Start synthesis" : "Run again"}</button>
        </div>
      </div>
      {err && <div className="alert bad" style={{ marginBottom: 14 }}>{err}</div>}
      {job.status === "error" && <div className="alert bad" style={{ marginBottom: 14 }}><span>✕</span><div><b>Processing stopped.</b> {job.error}</div></div>}
      {job.status === "done" && <div className="alert ok" style={{ marginBottom: 14 }}>✓ Report generated.</div>}
      {job.status === "idle" && <div className="alert info" style={{ marginBottom: 14 }}>Nothing has run yet. Press <b>Start synthesis</b>.</div>}
      {job.mode && (job.mode.llm || job.mode.embedder) && (
        <div className="row wrap" style={{ marginBottom: 16, gap: 8 }}>
          <span className={"pill " + (job.mode.llm?.enabled ? "ok" : "warn")}>LLM: {job.mode.llm?.enabled ? job.mode.llm.message : "offline extractive mode"}</span>
          {job.mode.embedder && <span className="pill blue">Embeddings: {job.mode.embedder}</span>}
          {job.mode.index && <span className="pill blue">Index: {job.mode.index}</span>}
        </div>
      )}
      <div className="card">
        <div className="row spread" style={{ marginBottom: 8 }}><b>Overall progress</b><span className="muted small">{Math.round(total * 100)}%</span></div>
        <div className="bar"><i style={{ width: `${total * 100}%` }} /></div>
        <div style={{ marginTop: 14 }}>
          {job.stages.map((st, i) => (
            <div className="stage" key={st.key}>
              <div className={"dot " + st.status}>{st.status === "running" ? <span className="spinner" style={{ width: 14, height: 14 }} /> : ICON[st.status] || i + 1}</div>
              <div className="grow">
                <div className="row spread"><b>{st.label}</b><span className="muted small">{st.status === "running" ? `${Math.round(st.progress * 100)}%` : st.status}</span></div>
                {st.status === "running" && <div className="bar" style={{ margin: "8px 0" }}><i style={{ width: `${st.progress * 100}%` }} /></div>}
                {st.detail && <div className={"small " + (st.status === "error" ? "errtext" : "muted")}>{st.detail}</div>}
              </div>
            </div>
          ))}
        </div>
      </div>
      {s && (
        <div className="grid cols4" style={{ marginTop: 18 }}>
          <div className="stat"><b>{s.sources}</b><span>sources used</span></div>
          <div className="stat"><b>{s.units}</b><span>knowledge units</span></div>
          <div className="stat"><b>{s.items}</b><span>distinct statements</span></div>
          <div className="stat"><b>{s.multi_source_items}</b><span>confirmed by 2+ sources</span></div>
          <div className="stat"><b>{s.details_preserved}</b><span>unique details kept</span></div>
          <div className="stat"><b>{s.conflicts}</b><span>conflicts flagged</span></div>
        </div>
      )}
      {job.warnings?.length > 0 && <div className="alert warn" style={{ marginTop: 18, flexDirection: "column", gap: 4 }}><b>Notes & warnings</b>{job.warnings.map((w, i) => <div key={i} className="small">• {w}</div>)}</div>}
      {job.log?.length > 0 && <div className="log" style={{ marginTop: 18 }}>{job.log.slice().reverse().map((l, i) => <div key={i}>{new Date(l.t * 1000).toLocaleTimeString()} · {l.text}</div>)}</div>}
    </>
  );
}
