"""OCR helpers (Tesseract via pytesseract). Reports availability honestly instead of failing silently."""
from __future__ import annotations

from functools import lru_cache

from ..config import settings


@lru_cache(maxsize=1)
def ocr_status() -> dict:
    if not settings.ocr_enabled:
        return {"available": False, "message": "OCR disabled (OCR_ENABLED=false)."}
    try:
        import pytesseract
        v = pytesseract.get_tesseract_version()
        return {"available": True, "message": f"Tesseract {v}"}
    except Exception:
        return {"available": False,
                "message": "Tesseract OCR is not installed. Install it (e.g. `apt install tesseract-ocr` / "
                           "`brew install tesseract`) or configure a vision-capable LLM (LLM_VISION=true)."}


def ocr_available() -> bool:
    return ocr_status()["available"]


def ocr_image(img) -> tuple[str, float]:
    """Returns (text, mean_confidence 0-100)."""
    import pytesseract
    from PIL import Image, ImageOps

    im = ImageOps.exif_transpose(img).convert("L")
    if max(im.size) < 1400:  # upscale small screenshots for better recognition
        f = 1400 / max(im.size)
        im = im.resize((int(im.width * f), int(im.height * f)), Image.LANCZOS)
    im = ImageOps.autocontrast(im)
    data = pytesseract.image_to_data(im, lang=settings.ocr_lang, output_type=pytesseract.Output.DICT)
    words, confs, lines = [], [], {}
    for i, w in enumerate(data["text"]):
        if not w.strip():
            continue
        try:
            c = float(data["conf"][i])
        except ValueError:
            c = -1
        if c >= 0:
            confs.append(c)
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(w)
    # rebuild paragraphs (block, paragraph) separated by blank lines
    paras: dict[tuple, list[str]] = {}
    for (b, p, _l), ws in lines.items():
        paras.setdefault((b, p), []).append(" ".join(ws))
    text = "\n\n".join(" ".join(v) for v in paras.values())
    return text.strip(), (sum(confs) / len(confs) if confs else 0.0)
