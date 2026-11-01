import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";

export default function CreateWorkspace() {
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    if (!name.trim()) return setErr("Please give the research topic a name.");
    setBusy(true); setErr("");
    try { const ws = await api.createWorkspace(name, desc); nav(`/app/w/${ws.id}`); } catch (er) { setErr(er.message); setBusy(false); }
  }
  async function demo() {
    setBusy(true);
    try { const ws = await api.demo(); nav(`/app/w/${ws.id}`); } catch (er) { setErr(er.message); setBusy(false); }
  }
  return (
    <div style={{ maxWidth: 680 }}>
      <div className="pagehead"><div><h1>New research workspace</h1><p className="muted">Name the topic. The description guides the report's introduction and scope, so say what you want to understand.</p></div></div>
      <form className="card" onSubmit={submit}>
        <label className="field"><span>Topic name</span><input type="text" value={name} maxLength={120} autoFocus onChange={(e) => setName(e.target.value)} placeholder="e.g. Lithium-ion battery technology" /></label>
        <label className="field"><span>Description <span className="muted" style={{ display: "inline", fontWeight: 400 }}>(optional)</span></span>
          <textarea value={desc} maxLength={1500} onChange={(e) => setDesc(e.target.value)} placeholder="What is this research about? Which questions should the report answer?" /></label>
        {err && <div className="alert bad" style={{ marginBottom: 14 }}>{err}</div>}
        <div className="row"><button className="btn primary" disabled={busy}>{busy ? <span className="spinner" /> : null} Create & add resources →</button>
          <button type="button" className="btn ghost" onClick={demo} disabled={busy}>or load the demo</button></div>
      </form>
    </div>
  );
}
