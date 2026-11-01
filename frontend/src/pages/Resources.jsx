import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, KIND_LABEL } from "../api.js";

const STATUS = { queued: ["blue", "Queued"], processing: ["warn", "Extracting…"], ready: ["ok", "Ready"], error: ["bad", "Failed"] };

export default function Resources() {
  const { id } = useParams();
  const nav = useNavigate();
  const [ws, setWs] = useState(null);
  const [cap, setCap] = useState(null);
  const [tab, setTab] = useState("files");
  const [url, setUrl] = useState("");
  const [pasteName, setPasteName] = useState("");
  const [paste, setPaste] = useState("");
  const [msgs, setMsgs] = useState([]);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [preview, setPreview] = useState(null);
  const fileRef = useRef();

  const load = useCallback(() => api.workspace(id).then(setWs).catch((e) => setMsgs([{ t: "bad", m: e.message }])), [id]);
  useEffect(() => { load(); api.capabilities().then(setCap).catch(() => {}); }, [load]);
  const active = ws?.resources.some((r) => r.status === "queued" || r.status === "processing");
  useEffect(() => { if (!active) return; const t = setInterval(load, 1500); return () => clearInterval(t); }, [active, load]);

  const say = (t, m) => setMsgs((x) => [...x.slice(-4), { t, m }]);

  async function upload(files) {
    if (!files?.length) return;
    setBusy(true);
    try {
      const { results } = await api.upload(id, files);
      results.filter((r) => !r.ok).forEach((r) => say("bad", `${r.name}: ${r.error}`));
    } catch (e) { say("bad", e.message); }
    setBusy(false); load();
  }
  async function addUrl(e) {
    e.preventDefault(); if (!url.trim()) return;
    setBusy(true);
    try { await api.addUrl(id, url.trim()); setUrl(""); } catch (er) { say("bad", er.message); }
    setBusy(false); load();
  }
  async function addText(e) {
    e.preventDefault(); setBusy(true);
    try { await api.addText(id, pasteName, paste); setPaste(""); setPasteName(""); } catch (er) { say("bad", er.message); }
    setBusy(false); load();
  }
  async function remove(r) {
    try { await api.removeResource(r.id); load(); } catch (e) { say("bad", e.message); }
  }
  async function retry(r) { try { await api.retryResource(r.id); load(); } catch (e) { say("bad", e.message); } }
  async function togglePreview(r) {
    if (preview?.id === r.id) return setPreview(null);
    try { const d = await api.units(r.id); setPreview({ id: r.id, units: d.units }); } catch (e) { say("bad", e.message); }
  }
  async function go() {
    try { await api.process(id); nav(`/app/w/${id}/process`); } catch (e) { say("bad", e.message); }
  }

  if (!ws) return <div className="row muted"><span className="spinner" /> Loading…</div>;
  const ready = ws.resources.filter((r) => r.status === "ready").length;
  const mediaNote = cap && [
    !cap.transcription.available && "Audio/video: " + cap.transcription.message,
    !cap.ocr.available && "Images: " + cap.ocr.message,
  ].filter(Boolean);

  return (
    <>
      <div className="pagehead">
        <div><h1>{ws.name}</h1><p className="muted">{ws.description || "Add the resources that cover this topic."}</p></div>
        <div className="row">
          {ws.has_report && <Link className="btn" to={`/app/w/${id}/report`}>View report</Link>}
          <button className="btn primary" disabled={!ready || active} onClick={go}>{active ? <span className="spinner" /> : "✦"} Synthesise {ready} source{ready === 1 ? "" : "s"} →</button>
        </div>
      </div>
      {ws.report_stale && <div className="alert warn" style={{ marginBottom: 16 }}>Resources changed since the last report. Run synthesis again to include the changes.</div>}
      {msgs.map((m, i) => <div key={i} className={"alert " + m.t} style={{ marginBottom: 10 }}><span style={{ flex: 1 }}>{m.m}</span><a href="#" onClick={(e) => { e.preventDefault(); setMsgs(msgs.filter((_, j) => j !== i)); }}>dismiss</a></div>)}

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="tabs">{[["files", "Upload files"], ["url", "Website URL"], ["text", "Paste text"]].map(([k, l]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
        {tab === "files" && (
          <>
            <div className={"drop" + (over ? " over" : "")} onClick={() => fileRef.current.click()}
              onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
              onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}>
              <div style={{ fontSize: "1.8rem" }}>{busy ? <span className="spinner" /> : "⇪"}</div>
              <b>Drag & drop files here, or click to browse</b>
              <div className="muted small" style={{ marginTop: 6 }}>PDF · DOCX · TXT/MD · HTML · images · audio · video — up to {cap?.max_upload_mb ?? 100} MB each</div>
              <input ref={fileRef} type="file" multiple hidden onChange={(e) => { upload(e.target.files); e.target.value = ""; }} />
            </div>
            {mediaNote?.length > 0 && <div className="alert info small" style={{ marginTop: 12 }}><span>ℹ</span><div>{mediaNote.map((m, i) => <div key={i}>{m}</div>)}</div></div>}
          </>
        )}
        {tab === "url" && (
          <form onSubmit={addUrl} className="row wrap"><input className="grow" type="url" placeholder="https://example.com/article" value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="btn primary" disabled={busy || !url.trim()}>Add page</button>
            <div className="muted small" style={{ flexBasis: "100%" }}>The main article text is extracted. Pages that need a login or are rendered only by JavaScript may fail — paste the text instead.</div></form>
        )}
        {tab === "text" && (
          <form onSubmit={addText}><label className="field"><span>Title (optional)</span><input type="text" value={pasteName} onChange={(e) => setPasteName(e.target.value)} placeholder="e.g. Interview notes" /></label>
            <textarea value={paste} onChange={(e) => setPaste(e.target.value)} placeholder="Paste notes, an article, a transcript…" style={{ minHeight: 160 }} />
            <div style={{ marginTop: 12 }}><button className="btn primary" disabled={busy || paste.trim().length < 40}>Add text</button> <span className="muted small">minimum 40 characters</span></div></form>
        )}
      </div>

      <h2>Resources <span className="muted" style={{ fontSize: "1rem" }}>({ws.resources.length})</span></h2>
      {ws.resources.length === 0 && <div className="card empty muted">Nothing here yet. Add at least two sources so Converge AI has something to compare.</div>}
      {ws.resources.map((r) => {
        const [cls, label] = STATUS[r.status] || ["", r.status];
        return (
          <div key={r.id}>
            <div className="res">
              <div className="sid">{r.source_id}</div>
              <div className="grow">
                <div className="name" title={r.name}>{r.name}</div>
                <div className="muted small row wrap" style={{ gap: 8 }}>
                  <span>{KIND_LABEL[r.kind] || r.kind}</span>
                  {r.status === "ready" && <><span>·</span><span>{r.stats.units} knowledge units</span>{r.stats.pages ? <><span>·</span><span>{r.stats.pages} pages</span></> : null}</>}
                </div>
                {r.error && <div className="errtext">{r.error}</div>}
                {r.warnings?.map((w, i) => <div key={i} className="small" style={{ color: "var(--warn)" }}>⚠ {w}</div>)}
              </div>
              <span className={"pill " + cls}>{r.status === "processing" && <span className="spinner" style={{ width: 11, height: 11, borderWidth: 2 }} />}{label}</span>
              {r.status === "ready" && <button className="btn sm" onClick={() => togglePreview(r)}>{preview?.id === r.id ? "Hide" : "Preview"}</button>}
              {r.status === "error" && <button className="btn sm" onClick={() => retry(r)}>Retry</button>}
              <button className="btn sm ghost danger" title="Remove" onClick={() => remove(r)}>✕</button>
            </div>
            {preview?.id === r.id && (
              <div className="card flat small" style={{ margin: "8px 0 4px", maxHeight: 280, overflow: "auto" }}>
                <div className="muted" style={{ marginBottom: 8 }}>Extracted knowledge units, with the location that will be cited:</div>
                {preview.units.map((u) => <div key={u.id} style={{ marginBottom: 10 }}><span className="pill blue">{r.source_id} · {u.label}</span> {u.text.slice(0, 260)}{u.text.length > 260 ? "…" : ""}</div>)}
              </div>
            )}
          </div>
        );
      })}
    </>
  );
}
