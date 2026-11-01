export default function CitationDrawer({ cite, onClose }) {
  if (!cite) return null;
  return (
    <>
      <div className="drawer-bg" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-label="Source passage">
        <div className="row spread"><span className="pill blue">{cite.source_id} · {cite.label}</span><button className="btn sm ghost" onClick={onClose}>Close ✕</button></div>
        <h3 style={{ marginTop: 16 }}>{cite.source_name}</h3>
        <p className="muted small">Exact passage extracted from the original source{cite.page ? ` (page ${cite.page})` : ""}{cite.t_start != null ? ` (at ${Math.floor(cite.t_start / 60)}:${String(Math.floor(cite.t_start % 60)).padStart(2, "0")})` : ""}.</p>
        <div className="passage">{cite.text}</div>
        {cite.context && cite.context !== cite.text && (<><div className="small muted">Surrounding context</div><div className="passage" style={{ borderColor: "#cfe3fb" }}>{cite.context}</div></>)}
      </aside>
    </>
  );
}
