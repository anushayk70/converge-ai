"""Image (OCR / vision), audio (transcription) and video (audio + representative frames)."""
from __future__ import annotations

import io
import subprocess
import tempfile
from pathlib import Path

from ..config import have_binary, settings
from ..llm import LLMError, llm
from .base import Extraction, ExtractionError, Segment, clean_text
from .ocr import ocr_available, ocr_image, ocr_status
from .transcribe import group_transcript, transcribe, transcription_status, _duration

VISION_PROMPT = ("Read this image for a research knowledge base. 1) Transcribe ALL visible text exactly. "
                 "2) Describe charts, tables, diagrams and photos factually, including every number, label and "
                 "axis. Write 'unclear' where something is unreadable; do not guess or add outside knowledge. "
                 "Plain paragraphs, no preamble.")


def _image_text(img, jpeg_bytes: bytes | None = None) -> tuple[str, str, list[str]]:
    """Returns (text, kind, warnings) using vision LLM first, Tesseract second."""
    warnings: list[str] = []
    if llm.vision:
        try:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "JPEG", quality=88)
            txt = llm.describe_image(jpeg_bytes or buf.getvalue(), "image/jpeg", VISION_PROMPT)
            if txt.strip():
                return txt.strip(), "vision", warnings
        except LLMError as e:
            warnings.append(f"Vision model failed ({e}); fell back to OCR.")
    if ocr_available():
        txt, conf = ocr_image(img)
        if conf and conf < 60:
            warnings.append(f"Low OCR confidence ({conf:.0f}%) - extracted text may contain errors.")
        return txt, "ocr", warnings
    raise ExtractionError("No way to read this image: " + ocr_status()["message"])


def extract_image(path: Path) -> Extraction:
    from PIL import Image, ImageOps
    try:
        img = ImageOps.exif_transpose(Image.open(path))
        img.load()
    except Exception as e:
        raise ExtractionError(f"Could not open the image: {e}")
    text, kind, warnings = _image_text(img)
    paras = [clean_text(p) for p in text.split("\n\n") if clean_text(p)]
    if not paras:
        raise ExtractionError("No text or content could be read from this image.")
    if kind == "ocr":
        warnings.append("Text was read with OCR; diagrams or photos without text are not described "
                        "(enable a vision-capable LLM with LLM_VISION=true for that).")
    return Extraction([Segment(p, kind=kind) for p in paras], warnings, {"size": f"{img.width}x{img.height}"})


def extract_audio(path: Path) -> Extraction:
    segs = group_transcript(transcribe(path))
    return Extraction(segs, ["Transcribed automatically; names and numbers may be misheard."],
                      {"duration": round(_duration(path))})


def _has_audio(path: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def _frame_diff(a, b) -> float:
    import numpy as np
    A = np.asarray(a.convert("L").resize((48, 48)), dtype=float)
    B = np.asarray(b.convert("L").resize((48, 48)), dtype=float)
    return float(abs(A - B).mean())


def extract_video(path: Path) -> Extraction:
    if not have_binary("ffmpeg") or not have_binary("ffprobe"):
        raise ExtractionError("FFmpeg is required for video files. Install it (e.g. `apt install ffmpeg` / "
                              "`brew install ffmpeg`) and restart Converge AI.")
    from PIL import Image
    warnings: list[str] = []
    segs: list[Segment] = []
    duration = _duration(path)

    # 1) audio track
    if _has_audio(path):
        if transcription_status()["available"]:
            with tempfile.TemporaryDirectory() as td:
                wav = Path(td) / "audio.mp3"
                r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                                    "-b:a", "48k", str(wav)], capture_output=True, text=True)
                if r.returncode != 0:
                    raise ExtractionError(f"FFmpeg could not extract audio: {r.stderr.strip()[:200]}")
                try:
                    segs += group_transcript(transcribe(wav))
                    warnings.append("Speech was transcribed automatically; names and numbers may be misheard.")
                except ExtractionError as e:
                    warnings.append(f"Audio track not transcribed: {e}")
        else:
            warnings.append("Audio track skipped: " + transcription_status()["message"])
    else:
        warnings.append("The video has no audio track.")

    # 2) representative frames (evenly spaced, near-duplicates dropped)
    if llm.vision or ocr_available():
        n = max(1, settings.video_max_frames)
        times = [(i + 0.5) * duration / n for i in range(n)] if duration > 0 else [0.0]
        prev, got = None, 0
        with tempfile.TemporaryDirectory() as td:
            for i, t in enumerate(times):
                fp = Path(td) / f"f{i}.jpg"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path),
                                "-frames:v", "1", "-q:v", "3", str(fp)], capture_output=True)
                if not fp.exists():
                    continue
                img = Image.open(fp)
                if prev is not None and _frame_diff(prev, img) < 4.0:
                    continue
                prev = img.copy()
                try:
                    text, kind, w = _image_text(img, fp.read_bytes() if llm.vision else None)
                except ExtractionError:
                    continue
                warnings += [x for x in w if x not in warnings]
                if len(text.strip()) >= 15:
                    segs.append(Segment(clean_text(text), t_start=t, kind=kind))
                    got += 1
        if got:
            warnings.append(f"{got} representative frame(s) analysed ({'vision model' if llm.vision else 'OCR'}).")
    else:
        warnings.append("Frames skipped: no OCR (Tesseract) or vision LLM available.")

    if not segs:
        raise ExtractionError("Nothing could be extracted from this video. " + " ".join(warnings))
    segs.sort(key=lambda s: s.t_start or 0)
    return Extraction(segs, warnings, {"duration": round(duration)})
