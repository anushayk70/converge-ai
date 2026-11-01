"""Dispatch extraction by resource kind."""
from __future__ import annotations

from pathlib import Path

from .base import Extraction, ExtractionError, Segment  # noqa: F401

EXT_KIND = {
    ".pdf": "pdf", ".docx": "docx", ".txt": "txt", ".md": "txt", ".markdown": "txt", ".text": "txt",
    ".html": "html", ".htm": "html",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image", ".bmp": "image", ".tif": "image", ".tiff": "image", ".gif": "image",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".ogg": "audio", ".flac": "audio", ".aac": "audio", ".opus": "audio",
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video", ".avi": "video", ".m4v": "video",
}


def detect_kind(filename: str) -> str | None:
    return EXT_KIND.get(Path(filename).suffix.lower())


def extract_resource(res: dict) -> Extraction:
    kind = res["kind"]
    path = Path(res["stored_path"]) if res.get("stored_path") else None
    if kind == "url":
        from .web import extract_url
        return extract_url(res["origin"])
    if path is None or not path.exists():
        raise ExtractionError("The stored file is missing. Remove this resource and add it again.")
    if kind == "pdf":
        from .documents import extract_pdf
        return extract_pdf(path)
    if kind == "docx":
        from .documents import extract_docx
        return extract_docx(path)
    if kind in ("txt", "text"):
        from .documents import extract_txt
        return extract_txt(path)
    if kind == "html":
        from .documents import extract_html_file
        return extract_html_file(path)
    if kind == "image":
        from .media import extract_image
        return extract_image(path)
    if kind == "audio":
        from .media import extract_audio
        return extract_audio(path)
    if kind == "video":
        from .media import extract_video
        return extract_video(path)
    raise ExtractionError(f"Unsupported resource type '{kind}'.")
