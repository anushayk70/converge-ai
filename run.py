"""Start CONVERGE AI: `python run.py`"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))
import uvicorn  # noqa: E402

from app.config import settings  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False, log_level="info")
