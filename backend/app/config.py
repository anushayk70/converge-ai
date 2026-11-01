"""Central configuration. Everything comes from environment variables (see .env.example)."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class Settings:
    llm_provider: str = "none"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = ""
    llm_vision: bool = False
    llm_timeout: int = 120
    llm_max_retries: int = 4
    llm_min_interval: float = 0.0
    llm_max_pairs: int = 300
    llm_context_chars: int = 24000

    embedding_backend: str = "auto"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_api_base_url: str = ""
    embedding_api_key: str = ""
    embedding_api_model: str = ""

    transcribe_provider: str = "auto"
    whisper_model: str = "base"
    transcribe_api_base_url: str = ""
    transcribe_api_key: str = ""
    transcribe_model: str = "whisper-1"

    ocr_enabled: bool = True
    ocr_lang: str = "eng"
    video_max_frames: int = 8

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    max_upload_mb: int = 100
    max_units: int = 4000
    allow_private_urls: bool = False

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "converge.db"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    data_dir = Path(os.getenv("DATA_DIR", "") or (ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = (ROOT / data_dir).resolve()
    s = Settings(
        llm_provider=(os.getenv("LLM_PROVIDER", "none") or "none").strip().lower(),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        llm_base_url=(os.getenv("LLM_BASE_URL", "") or "").rstrip("/"),
        llm_vision=_bool("LLM_VISION", False),
        llm_timeout=_int("LLM_TIMEOUT_SECONDS", 120),
        llm_max_retries=_int("LLM_MAX_RETRIES", 4),
        llm_min_interval=_float("LLM_MIN_INTERVAL_SECONDS", 0.0),
        llm_max_pairs=_int("LLM_MAX_PAIRS", 300),
        llm_context_chars=_int("LLM_CONTEXT_CHARS", 24000),
        embedding_backend=(os.getenv("EMBEDDING_BACKEND", "auto") or "auto").strip().lower(),
        embedding_model=os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        embedding_api_base_url=(os.getenv("EMBEDDING_API_BASE_URL", "") or "").rstrip("/"),
        embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
        embedding_api_model=os.getenv("EMBEDDING_API_MODEL", ""),
        transcribe_provider=(os.getenv("TRANSCRIBE_PROVIDER", "auto") or "auto").strip().lower(),
        whisper_model=os.getenv("WHISPER_MODEL", "base"),
        transcribe_api_base_url=(os.getenv("TRANSCRIBE_API_BASE_URL", "") or "").rstrip("/"),
        transcribe_api_key=os.getenv("TRANSCRIBE_API_KEY", ""),
        transcribe_model=os.getenv("TRANSCRIBE_MODEL", "whisper-1"),
        ocr_enabled=_bool("OCR_ENABLED", True),
        ocr_lang=os.getenv("OCR_LANG", "eng"),
        video_max_frames=_int("VIDEO_MAX_FRAMES", 8),
        host=os.getenv("HOST", "127.0.0.1"),
        port=_int("PORT", 8000),
        data_dir=data_dir,
        max_upload_mb=_int("MAX_UPLOAD_MB", 100),
        max_units=_int("MAX_UNITS", 4000),
        allow_private_urls=_bool("ALLOW_PRIVATE_URLS", False),
    )
    s.ensure_dirs()
    return s


settings = load_settings()


def have_binary(name: str) -> bool:
    return shutil.which(name) is not None
