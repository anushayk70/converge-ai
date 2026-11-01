import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";
import { api } from "../api.js";

export const Logo = () => (
  <span className="logo"><svg width="18" height="18" viewBox="0 0 32 32"><path d="M5 9c7 0 7 7 11 7s4-7 11-7M5 23c7 0 7-7 11-7s4 7 11 7" stroke="#fff" strokeWidth="3" fill="none" strokeLinecap="round" /></svg></span>
);

export function CapabilityBanner() {
  const [cap, setCap] = useState(null);
  useEffect(() => { api.capabilities().then(setCap).catch(() => {}); }, []);
  if (!cap) return null;
  const msgs = [];
  if (!cap.llm.enabled) msgs.push(cap.llm.message);
  if (!cap.embeddings.semantic_available) msgs.push(cap.embeddings.message);
  if (!msgs.length) return null;
  return (
    <div className="alert warn" style={{ marginBottom: 22 }}>
      <span>⚠</span>
      <div><b>Running with limited capabilities.</b> {msgs.join(" ")} <span className="muted">Reports stay accurate (statements are quoted from your sources) but will be less fluent and less precise at spotting paraphrased overlap. See <code>.env.example</code>.</span></div>
    </div>
  );
}

export default function Layout() {
  const { id } = useParams();
  const [open, setOpen] = useState(false);
  const [ws, setWs] = useState(null);
  useEffect(() => { setOpen(false); if (id) api.workspace(id).then(setWs).catch(() => setWs(null)); else setWs(null); }, [id]);
  return (
    <div className="shell">
      <aside className={"side" + (open ? " open" : "")}>
        <Link to="/" className="brand"><Logo /> Converge AI</Link>
        <nav className="nav">
          <NavLink end to="/app">▦ &nbsp;Workspaces</NavLink>
          <NavLink to="/app/new">＋ &nbsp;New workspace</NavLink>
          {id && (<>
            <div className="label">{ws ? ws.name : "Workspace"}</div>
            <NavLink end to={`/app/w/${id}`}>⇪ &nbsp;Resources</NavLink>
            <NavLink to={`/app/w/${id}/process`}>⚙ &nbsp;Processing</NavLink>
            <NavLink to={`/app/w/${id}/report`}>▤ &nbsp;Report</NavLink>
            <NavLink to={`/app/w/${id}/chat`}>✦ &nbsp;Ask sources</NavLink>
          </>)}
        </nav>
      </aside>
      <main className="main">
        <button className="btn sm menu-btn" onClick={() => setOpen(!open)}>☰ Menu</button>
        <CapabilityBanner />
        <Outlet />
      </main>
    </div>
  );
}
