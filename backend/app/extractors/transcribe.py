"""Speech-to-text: local faster-whisper or any OpenAI-compatible /audio/transcriptions endpoint (e.g. Groq)."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import httpx

from ..config import settings
from .base import ExtractionError, Segment

_model = None


def _local_importable() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _api_configured() -> bool:
    return bool(settings.transcribe_api_base_url and settings.transcribe_api_key)


def provider() -> str:
    p = settings.transcribe_provider
    if p == "none":
        return "none"
    if p == "local":
        return "local" if _local_importable() else "none"
    if p == "api":
        return "api" if _api_configured() else "none"
    if _local_importable():
        return "local"
    return "api" if _api_configured() else "none"


def transcription_status() -> dict:
    p = provider()
    if p == "local":
        return {"available": True, "provider": "local", "message": f"faster-whisper ({settings.whisper_model})"}
    if p == "api":
        return {"available": True, "provider": "api", "message": f"API: {settings.transcribe_api_base_url} ({settings.transcribe_model})"}
    return {"available": False, "provider": "none",
            "message": "Speech transcription is not configured. Either `pip install faster-whisper` "
                       "(TRANSCRIBE_PROVIDER=local) or set TRANSCRIBE_API_BASE_URL / TRANSCRIBE_API_KEY / "
                       "TRANSCRIBE_MODEL for an OpenAI-compatible endpoint such as Groq's whisper-large-v3-turbo."}


def _to_mp3(src: Path, dst: Path, start: float | None = None, dur: float | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise ExtractionError(f"ffmpeg could not read this media file: {r.stderr.strip()[:200]}")


def _duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _transcribe_local(path: Path) -> list[tuple[float, str]]:
    global _model
    try:
        from faster_whisper import WhisperModel
        if _model is None:
            _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
        segs, _info = _model.transcribe(str(path), vad_filter=True)
        return [(s.start, s.text.strip()) for s in segs if s.text.strip()]
    except ExtractionError:
        raise
    except Exception as e:
        raise ExtractionError(f"Local Whisper transcription failed ({e}). The model '{settings.whisper_model}' may "
                              "need to be downloaded first (needs internet on first use).")


def _transcribe_api(path: Path) -> list[tuple[float, str]]:
    if not _have_ffmpeg():
        raise ExtractionError("FFmpeg is required to prepare audio for API transcription (install ffmpeg).")
    total = _duration(path)
    part_len = 1200.0  # 20 min parts keep each upload well under typical 25 MB limits
    parts = [(0.0, None)] if 0 < total <= part_len else [(s, part_len) for s in range(0, int(total or part_len) + 1, int(part_len))]
    out: list[tuple[float, str]] = []
    for start, dur in parts:
        with tempfile.TemporaryDirectory() as td:
            mp3 = Path(td) / "part.mp3"
            _to_mp3(path, mp3, start if dur else None, dur)
            try:
                with open(mp3, "rb") as fh:
                    r = httpx.post(settings.transcribe_api_base_url + "/audio/transcriptions",
                                   headers={"Authorization": f"Bearer {settings.transcribe_api_key}"},
                                   data={"model": settings.transcribe_model, "response_format": "verbose_json"},
                                   files={"file": ("part.mp3", fh, "audio/mpeg")}, timeout=600)
            except httpx.HTTPError as e:
                raise ExtractionError(f"Could not reach the transcription API: {e}")
            if r.status_code in (401, 403):
                raise ExtractionError("The transcription API rejected the key (check TRANSCRIBE_API_KEY).")
            if r.status_code != 200:
                raise ExtractionError(f"Transcription API error HTTP {r.status_code}: {r.text[:200]}")
            js = r.json()
            segs = js.get("segments") or []
            if segs:
                out += [(float(start) + float(s.get("start", 0)), s.get("text", "").strip()) for s in segs if s.get("text", "").strip()]
            elif js.get("text", "").strip():
                out.append((float(start), js["text"].strip()))
    return out


def _have_ffmpeg() -> bool:
    from ..config import have_binary
    return have_binary("ffmpeg")


def transcribe(path: Path) -> list[tuple[float, str]]:
    p = provider()
    if p == "none":
        raise ExtractionError(transcription_status()["message"])
    items = _transcribe_local(path) if p == "local" else _transcribe_api(path)
    if not items:
        raise ExtractionError("No speech was detected in this recording.")
    return items


def group_transcript(items: list[tuple[float, str]], window: float = 45.0) -> list[Segment]:
    """Merge short Whisper segments into ~window-second passages that keep their start timestamp."""
    segs: list[Segment] = []
    cur_t, buf = None, []
    for t, txt in items:
        if cur_t is None:
            cur_t = t
        buf.append(txt)
        if t - cur_t >= window:
            segs.append(Segment(" ".join(buf), t_start=cur_t, kind="transcript"))
            cur_t, buf = None, []
    if buf:
        segs.append(Segment(" ".join(buf), t_start=cur_t or 0.0, kind="transcript"))
    return segs
