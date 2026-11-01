import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api.js";
import Markdown from "../components/Markdown.jsx";
import CitationDrawer from "../components/CitationDrawer.jsx";

const SUGGEST = ["What do the sources agree on?", "Where do the sources disagree?", "Which details appear in only one source?", "Summarise the key statistics."];

export default function Chat() {
  const { id } = useParams();
  const [ws, setWs] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [cite, setCite] = useState(null);
  const end = useRef();
  useEffect(() => { api.workspace(id).then(setWs).catch(() => {}); }, [id]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, busy]);

  async function ask(text) {
    const question = (text ?? q).trim();
    if (question.length < 2 || busy) return;
    const history = msgs.filter((m) => !m.error).map((m) => ({ role: m.role, content: m.text }));
    setMsgs((m) => [...m, { role: "user", text: question }]); setQ(""); setBusy(true);
    try {
      const r = await api.chat(id, question, history);
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, mode: r.mode, cites: r.citations, notes: r.notes }]);
    } catch (e) { setMsgs((m) => [...m, { role: "assistant", text: e.message, error: true }]); }
    setBusy(false);
  }
  const ready = ws?.resources.filter((r) => r.status === "ready").length ?? 0;
  const citeMap = (cites = []) => Object.fromEntries(cites.map((c) => [c.unit_id, c]));

  return (
    <>
      <div className="pagehead"><div><h1>Ask your sources</h1><p className="muted">Answers draw on every resource in “{ws?.name || "…"}” at once, cite where each fact comes from, and point out disagreements.</p></div>
        <Link className="btn" to={`/app/w/${id}/report`}>View report</Link></div>
      {ws && ready === 0 && <div className="alert warn" style={{ marginBottom: 14 }}>No resources are ready yet. <Link to={`/app/w/${id}`}>Add or fix resources →</Link></div>}
      <div className="chat">
        <div className="msgs">
          {msgs.length === 0 && (
            <div className="card tint"><h3>Try a question</h3><p className="muted small">Answers use only your uploaded sources. If the sources don't contain the answer, you'll be told so.</p>
              <div className="suggest">{SUGGEST.map((s) => <button key={s} onClick={() => ask(s)} disabled={!ready}>{s}</button>)}</div></div>)}
          {msgs.map((m, i) => m.role === "user" ? <div key={i} className="msg user">{m.text}</div> : (
            <div key={i} className="msg ai">
              {m.error ? <div className="errtext">{m.text}</div> : <Markdown text={m.text} citations={citeMap(m.cites)} onCite={(cid) => setCite(citeMap(m.cites)[cid])} />}
              {m.notes?.map((n, j) => <div key={j} className="alert warn small" style={{ marginTop: 10 }}>{n}</div>)}
              {m.cites?.length > 0 && (
                <details style={{ marginTop: 12 }}><summary className="small muted" style={{ cursor: "pointer" }}>{m.cites.length} passages consulted from {new Set(m.cites.map((c) => c.source_id)).size} sources</summary>
                  {m.cites.map((c) => <div key={c.unit_id} className="passage small" style={{ cursor: "pointer", margin: "6px 0" }} onClick={() => setCite(c)}><span className="pill blue">{c.source_id} · {c.label}</span> {c.text.slice(0, 200)}{c.text.length > 200 ? "…" : ""}</div>)}
                </details>)}
              {m.mode === "extractive" && <div className="small muted" style={{ marginTop: 8 }}>Extractive mode: passages shown verbatim. Configure an LLM for written answers.</div>}
            </div>
          ))}
          {busy && <div className="row muted"><span className="spinner" /> Searching all sources…</div>}
          <div ref={end} />
        </div>
        <form className="row" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <input type="text" className="grow" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask a question across all sources…" disabled={!ready} />
          <button className="btn primary" disabled={busy || !ready || q.trim().length < 2}>Ask</button>
        </form>
      </div>
      <CitationDrawer cite={cite} onClose={() => setCite(null)} />
    </>
  );
}
