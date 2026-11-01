"""Extract one resource, chunk it and persist the units. Never raises: failures become a clear status."""
from __future__ import annotations

import logging
import traceback

from . import db
from .extractors import ExtractionError, extract_resource
from .pipeline.chunking import make_units

log = logging.getLogger("converge.ingest")


def process_resource(rid: str) -> None:
    res = db.get_resource(rid)
    if not res:
        return
    db.set_resource_status(rid, "processing")
    try:
        ex = extract_resource(res)
        units = make_units(ex.segments)
        if not units:
            raise ExtractionError("Text was found but it was too short or fragmentary to analyse.")
        db.replace_units(rid, res["workspace_id"], units)
        kinds: dict[str, int] = {}
        for u in units:
            kinds[u["kind"]] = kinds.get(u["kind"], 0) + 1
        stats = {"units": len(units), "words": sum(len(u["text"].split()) for u in units), "kinds": kinds, **ex.meta}
        if ex.meta.get("title") and res["kind"] in ("url",) and res["name"] == res["origin"]:
            db.x("UPDATE resources SET name=? WHERE id=?", (str(ex.meta["title"])[:140], rid))
        db.set_resource_status(rid, "ready", "", ex.warnings, stats)
    except ExtractionError as e:
        db.set_resource_status(rid, "error", str(e))
    except Exception as e:  # unexpected - log the traceback, show a short message
        log.error("Unexpected extraction failure for %s\n%s", rid, traceback.format_exc())
        db.set_resource_status(rid, "error", f"Unexpected error while reading this resource: {type(e).__name__}: {e}")
