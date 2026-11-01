import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, timeAgo } from "../api.js";

export default function Dashboard() {
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const load = () => api.workspaces().then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  async function demo() {
    setBusy(true);
    try { const ws = await api.demo(); nav(`/app/w/${ws.id}`); } catch (e) { setErr(e.message); setBusy(false); }
  }
  async function del(w, e) {
    e.preventDefault();
    if (!confirm(`Delete "${w.name}" and all its resources and report?`)) return;
    try { await api.deleteWorkspace(w.id); load(); } catch (er) { setErr(er.message); }
  }
  const stat = (k) => (list || []).reduce((a, w) => a + (k === "res" ? w.resource_count : w.has_report ? 1 : 0), 0);
  return (
    <>
      <div className="pagehead">
        <div><h1>Research workspaces</h1><p className="muted">Each workspace holds the sources for one topic and produces one synthesised report.</p></div>
        <Link className="btn primary" to="/app/new">＋ New workspace</Link>
      </div>
      {err && <div className="alert bad" style={{ marginBottom: 16 }}>{err}</div>}
      {list && list.length > 0 && (
        <div className="grid cols4" style={{ marginBottom: 24 }}>
          <div className="stat"><b>{list.length}</b><span>workspaces</span></div>
          <div className="stat"><b>{stat("res")}</b><span>resources collected</span></div>
          <div className="stat"><b>{stat("rep")}</b><span>reports generated</span></div>
        </div>
      )}
      {!list && !err && <div className="row muted"><span className="spinner" /> Loading…</div>}
      {list && list.length === 0 && (
        <div className="card empty">
          <h2>No workspaces yet</h2>
          <p className="muted">Create one for your topic, or load the demo to see how five overlapping sources become one report.</p>
          <div className="row" style={{ justifyContent: "center" }}>
            <Link className="btn primary" to="/app/new">Create workspace</Link>
            <button className="btn" onClick={demo} disabled={busy}>{busy ? "Loading…" : "Load demo data"}</button>
          </div>
        </div>
      )}
      <div className="grid cols3">
        {(list || []).map((w) => (
          <Link key={w.id} to={w.has_report ? `/app/w/${w.id}/report` : `/app/w/${w.id}`} className="card ws-card">
            <div className="row spread"><h3 style={{ margin: 0 }}>{w.name}</h3>
              <button className="btn sm ghost danger" onClick={(e) => del(w, e)} title="Delete workspace">🗑</button></div>
            <p className="muted small" style={{ margin: "8px 0 14px", display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{w.description || "No description"}</p>
            <div className="row wrap" style={{ gap: 6 }}>
              <span className="pill">{w.resource_count} resource{w.resource_count === 1 ? "" : "s"}</span>
              {w.error_count > 0 && <span className="pill bad">{w.error_count} failed</span>}
              {w.job_status === "running" && <span className="pill warn">processing…</span>}
              {w.has_report ? <span className={"pill " + (w.report_stale ? "warn" : "ok")}>{w.report_stale ? "report outdated" : "report ready"}</span> : <span className="pill blue">no report yet</span>}
            </div>
            <div className="muted small" style={{ marginTop: 12 }}>Updated {timeAgo(w.updated_at)}</div>
          </Link>
        ))}
      </div>
    </>
  );
}
