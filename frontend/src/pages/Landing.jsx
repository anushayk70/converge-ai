import { Link, useNavigate } from "react-router-dom";
import { useState } from "react";
import { api } from "../api.js";
import { Logo } from "../components/Layout.jsx";

export default function Landing() {
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function demo() {
    setBusy(true); setErr("");
    try { const ws = await api.demo(); nav(`/app/w/${ws.id}`); } catch (e) { setErr(e.message); setBusy(false); }
  }
  return (
    <div className="land">
      <nav>
        <span className="brand" style={{ padding: 0 }}><Logo /> Converge AI</span>
        <Link className="btn" to="/app">Open dashboard →</Link>
      </nav>
      <header className="hero">
        <span className="pill">Multi-source knowledge synthesis</span>
        <h1 style={{ marginTop: 18 }}>Many sources in. <em>One trustworthy report</em> out.</h1>
        <p className="lead">Upload PDFs, Word files, web pages, notes, images, recordings and videos about one topic. Converge AI compares them all, merges what repeats, keeps what is unique, flags what disagrees, and cites every claim back to its source.</p>
        <div className="row" style={{ justifyContent: "center" }} >
          <Link className="btn primary" to="/app/new">Create a workspace</Link>
          <button className="btn" onClick={demo} disabled={busy}>{busy ? <span className="spinner" /> : "▶"} Try the demo (5 sample sources)</button>
        </div>
        {err && <div className="alert bad" style={{ maxWidth: 560, margin: "18px auto 0" }}>{err}</div>}
      </header>

      <h2 className="section-title">Why not just ask a chatbot to summarise each file?</h2>
      <div className="compare">
        <div className="card flat">
          <h3>🗂 Summarising files one by one</h3>
          <ul>
            <li>Five files give you five summaries that repeat each other.</li>
            <li>The same fact appears three times, worded three ways.</li>
            <li>Two sources quietly disagree, and nobody notices.</li>
            <li>A detail that only one source has gets lost in the shuffle.</li>
            <li>No reliable way to trace a sentence back to its page.</li>
          </ul>
        </div>
        <div className="card tint" style={{ borderColor: "#cfc8f5" }}>
          <h3>✦ Converge AI</h3>
          <ul style={{ color: "var(--ink)" }}>
            <li><b>Semantic deduplication</b>: repeated facts are merged into one statement.</li>
            <li><b>Unique details preserved</b>: a number or example only one source has survives the merge.</li>
            <li><b>Contradictions flagged</b>: each position is shown with its source, never silently resolved.</li>
            <li><b>Every claim cited</b>: source name, page number or timestamp, and the exact passage one click away.</li>
            <li><b>One structured report</b>, exportable to PDF and DOCX.</li>
          </ul>
        </div>
      </div>

      <h2 className="section-title">How it works</h2>
      <div className="steps">
        {[["Collect", "Drop in files, paste text or add website links. Text, OCR, speech transcription and video frames are extracted for you."],
          ["Compare", "Every passage is embedded and searched against all the others to find overlap, across sources."],
          ["Judge", "An LLM decides whether overlapping passages are duplicates, complementary or in conflict. Differences are kept."],
          ["Synthesise", "A 13-section report: summary, concepts, findings, comparisons, contradictions, gaps and references."],
          ["Ask & export", "Question all your sources at once with cited answers, then export the report as PDF or DOCX."]].map(([t, d]) => (
          <div className="card" key={t}><h3>{t}</h3><p className="muted small" style={{ margin: 0 }}>{d}</p></div>))}
      </div>

      <div className="card tint center" style={{ marginTop: 56, padding: 40 }}>
        <h2>Honest by design</h2>
        <p className="muted" style={{ maxWidth: 640, margin: "0 auto" }}>Converge AI never invents facts or references. Figures that cannot be matched to a source are marked as unverified, and when an LLM, OCR or transcription service isn't configured, it tells you instead of pretending the work was done.</p>
      </div>
    </div>
  );
}
