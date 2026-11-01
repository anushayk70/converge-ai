# Converge AI

**Many sources in, one trustworthy report out.** Converge AI takes several resources about one topic (PDF, DOCX, TXT, web pages,
pasted text, images, audio, video), compares them *against each other*, and produces a single structured research report in which

* repeated information is **merged once** (semantic deduplication),
* information that only **one source** has is **kept**, with attribution,
* **disagreements are flagged** with every position and its source, never silently resolved,
* every claim is **cited** (source, page / section / timestamp) and the exact original passage is one click away.

This is not "summarise each file": five per-file summaries repeat each other, hide contradictions and lose provenance.
`tests/verify_synthesis.py` demonstrates the difference on the bundled demo: 48 passages become 37 distinct statements,
5 statements are backed by several sources, 2 conflicts are flagged with both sides, and 48 of 48 passages are traceable.

## Quick start

Requires Python 3.10+ and Node 18+ (Node is only needed to build the UI once).

```bash
cd converge-ai
python -m venv .venv && source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then edit (see below); the app also runs with the defaults
cd frontend && npm install && npm run build && cd ..
python run.py                                            # http://127.0.0.1:8000
```

Click **Try the demo** on the landing page. It creates a workspace from `sample_data/`: a PDF, a DOCX, a TXT, an HTML article
and pasted text about lithium-ion batteries, with overlapping facts, source-only details and deliberate disagreements.

Development mode with hot reload: run `python run.py` in one terminal and `cd frontend && npm run dev` in another
(http://localhost:5173 proxies `/api` to the backend).

System binaries (optional, only for those inputs): **Tesseract** for OCR (`apt install tesseract-ocr` / `brew install tesseract`)
and **FFmpeg** for audio and video.

## What works without any API key

| Capability | Without configuration | With configuration |
|---|---|---|
| PDF / DOCX / TXT / HTML / URL / pasted text | Full extraction with page, section or paragraph locations | – |
| Image OCR | Works if Tesseract is installed | `LLM_VISION=true` adds image descriptions |
| Video | Frames (OCR) via FFmpeg | Audio is transcribed if transcription is configured |
| Audio / video speech | Not available: clear message, source flagged with a warning (never faked) | `pip install faster-whisper`, or an API (e.g. Groq Whisper) |
| Similarity / dedup | Lexical TF-IDF fallback (weaker on paraphrases) | `pip install sentence-transformers` for real semantic embeddings |
| Relationship judgement, synthesis, Q&A | **Offline extractive mode**: verbatim statements are merged, cited and ranked; no new prose is written | Any LLM provider: written sections, LLM-judged dedup and contradictions, fluent answers |
| PDF / DOCX / Markdown export | Works | – |

The UI shows which mode is active (a banner on every page, a badge on the processing page, a note on the report). In offline
mode the report says so, and the *Knowledge gaps* section repeats it.

## Environment variables

All are optional; `.env.example` has the full annotated list. **Never commit your `.env`.** No key is hard-coded anywhere.

| Variable | Meaning |
|---|---|
| `LLM_PROVIDER` | `none` (default, offline mode), `anthropic`, or `openai_compatible` |
| `LLM_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL` | Credentials, model and endpoint. `openai_compatible` works with Groq, OpenRouter, Google Gemini's OpenAI-compatible endpoint, OpenAI, and local servers (Ollama, LM Studio, vLLM) |
| `LLM_VISION` | `true` if the model accepts images |
| `LLM_MIN_INTERVAL_SECONDS` | Throttle for free-tier rate limits (for example `2.5`) |
| `LLM_MAX_PAIRS`, `LLM_CONTEXT_CHARS` | Cost controls: overlap clusters judged by the LLM per run, and characters per synthesis prompt |
| `EMBEDDING_BACKEND` | `auto` (sentence-transformers if available, else TF-IDF), `sentence-transformers`, `api`, or `tfidf` |
| `EMBEDDING_MODEL` | Default `sentence-transformers/all-MiniLM-L6-v2` |
| `TRANSCRIBE_PROVIDER`, `WHISPER_MODEL`, `TRANSCRIBE_API_*` | `auto`, `local`, `api` or `none` speech-to-text |
| `OCR_ENABLED`, `OCR_LANG`, `VIDEO_MAX_FRAMES` | OCR and video-frame sampling |
| `HOST`, `PORT`, `DATA_DIR`, `MAX_UPLOAD_MB`, `MAX_UNITS` | Server and limits |
| `ALLOW_PRIVATE_URLS` | Default `false`: URL import refuses localhost and private addresses (SSRF protection). Enable only on a trusted local machine |

Free options: Groq (`LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=https://api.groq.com/openai/v1`), Gemini's free tier, or fully
local with Ollama (`LLM_BASE_URL=http://localhost:11434/v1`, `LLM_API_KEY=ollama`).

## How the pipeline works

1. **Extract** (`backend/app/extractors/`): PyMuPDF (pages, OCR for scanned pages), python-docx (headings, tables), BeautifulSoup
   (main article text), Tesseract, Whisper, FFmpeg. Text becomes *knowledge units* that keep the source id and location
   (`S1 · p. 3`, `S2 · § Costs`, `S5 · ¶2`, `S6 · 02:14`).
2. **Embed and index** (`pipeline/embeddings.py`): sentence-transformers (or the fallback) and a FAISS inner-product index.
3. **Compare** (`pipeline/analysis.py`): nearest-neighbour search across all units clusters passages that describe the same concept.
   Passages from *different* sources are linked at a lower similarity than passages from the same source.
4. **Judge** (`pipeline/analysis.py`): the LLM labels each cluster *duplicate / complementary / conflicting / unrelated*, writes one merged
   statement, and must list unique details and conflicting claims. Anything it does not account for is re-emitted, so information
   cannot silently vanish, and merged text containing numbers absent from the sources is rejected. Offline, transparent heuristics
   (sentence-level novelty, numeric and negation checks) do this job and label results "potential".
5. **Synthesise** (`pipeline/synthesis.py`): 13 sections. Each statement has one home among Sections 3 to 6; the summary,
   statistics table and per-source index are compact views. LLM output is checked: citations to non-existent passages are removed
   and unmatched figures get a visible "unverified" note.
6. **Export** (`export/`): PDF (ReportLab) and DOCX (python-docx), with tables, a contents list and the source map.
7. **Ask** (`pipeline/chat.py`): retrieval across all sources, answers with citations, and a warning when retrieved passages are
   involved in a flagged conflict.

## Tests

```bash
python tests/verify_synthesis.py   # multi-source synthesis, no lost unique facts, conflict flagged, no repeated passages (offline)
python tests/e2e_llm_mock.py       # LLM code path and hallucination guards against a scripted fake provider
python tests/e2e_offline.py        # prints the full offline report for inspection
```

`tests/mock_llm_server.py` is a deterministic stand-in for an LLM endpoint. It validates plumbing and safeguards, **not** the prose
quality of a real model.

## Limitations (please read)

* **Offline mode is a fallback, not the best mode.** With TF-IDF similarity, loosely related paragraphs are sometimes grouped
  (nothing is lost, but the group is labelled "complementary"), and paraphrases that share few words are missed. Install
  `sentence-transformers` and configure an LLM for the intended quality. The real-model path has been tested against a scripted
  mock, not a live provider, so expect to tune prompts and thresholds for your model.
* Contradictions are only searched among passages found to overlap. Automatic checks say "potential conflict" until an LLM has judged them.
* Audio and video speech needs a transcription backend. Video frames are sampled (default 8), so on-screen text between samples is missed.
* URL import handles static HTML only; JavaScript-rendered or login-protected pages fail with a message (paste the text instead).
* Jobs run in-process (one thread per workspace). This is a single-user MVP with no authentication.
