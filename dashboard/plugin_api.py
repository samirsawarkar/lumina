"""Lumina backend routes, mounted at /api/plugins/lumina/. Thin wrappers over photos.py."""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

# Loaded by path: this file may be imported outside the plugin package.
_spec = importlib.util.spec_from_file_location("lumina_photos", Path(__file__).resolve().parent.parent / "photos.py")
photos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(photos)

router = APIRouter()
_UUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")


def _db():
    try:
        return photos.get_db()
    except photos.LuminaError as e:
        raise HTTPException(503 if e.code == "no_access" else 404, detail={"error": e.code, "message": e.message})


@lru_cache(maxsize=500)
def _thumb(uuid: str, size: str):
    # ponytail: cache keyed on uuid+size only; restart to pick up edited photos
    p = _db().get_photo(uuid)
    if p is None:
        return {"uuid": uuid, "date": None, "title": None, "place": None, "data_url": None}
    return {"uuid": uuid, "date": p.date.isoformat() if p.date else None, "title": p.title,
            "place": p.place.name if p.place else None, "data_url": photos.thumb_data_url(p, size)}


@router.get("/thumbs")
def thumbs(uuids: str, size: str = "s"):
    ids = [u for u in uuids.split(",") if u]
    if not ids or len(ids) > 60 or not all(_UUID.match(u) for u in ids) or size not in photos.SIZES:
        raise HTTPException(400, detail={"error": "bad_args", "message": "1-60 uuids and size s|m required."})
    return {"items": [_thumb(u, size) for u in ids]}


@router.get("/open")
def open_photo(uuid: str):
    if not _UUID.match(uuid):
        raise HTTPException(400, detail={"error": "bad_args", "message": "bad uuid"})
    try:
        photos.open_photo(uuid)
    except photos.LuminaError as e:
        raise HTTPException(500, detail={"error": e.code, "message": e.message})
    return {"ok": True}


_RID = re.compile(r"^[0-9a-f]{12}$")


@router.get("/redact")
def redact_view(id: str = ""):
    """Before/after for a Safe Send copy. Meta carries no sensitive values; paths are never returned."""
    if not _RID.fullmatch(id):
        return JSONResponse({"error": "bad_args"}, status_code=400)
    # The tool runs with Hermes' per-plugin data dir (plugin-data/<install id>); this router does not get it, so look in siblings too.
    home = photos._data_dir().parent
    d = next((x.parent for x in [photos._data_dir() / "redacted" / f"{id}.json", *home.glob(f"*/redacted/{id}.json")] if x.exists()),
             photos._data_dir() / "redacted")
    try:
        meta = json.loads((d / f"{id}.json").read_text())
        from PIL import Image, ImageOps

        im = Image.open(d / f"{id}.jpg").convert("RGB")
    except (OSError, ValueError):
        return JSONResponse({"error": "not_found"}, status_code=404)
    im.thumbnail((1024, 1024))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    before = None
    if meta.get("uuid"):
        p = _db().get_photo(meta["uuid"])
        before = photos.thumb_data_url(p, "m") if p is not None else None
    elif meta.get("path"):  # source file of a path-based copy; the path itself is never returned
        try:
            f = Path(meta["path"])
            if f.stat().st_size <= 30_000_000:
                with Image.open(f) as src:
                    src = ImageOps.exif_transpose(src).convert("RGB")
                src.thumbnail((1024, 1024))
                buf2 = io.BytesIO()
                src.save(buf2, "JPEG", quality=85)
                before = "data:image/jpeg;base64," + base64.b64encode(buf2.getvalue()).decode()
        except Exception:
            before = None
    return {**{k: meta.get(k) for k in ("id", "uuid", "document", "preset", "found", "remaining", "verified", "kinds")},
            "before": before,
            "after": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}


@router.get("/status")
def status():
    try:
        return {**photos.library_info(photos.get_db()), "access": "ok"}
    except photos.LuminaError as e:
        return {"access": "no_access" if e.code == "no_access" else "error", "error": e.code, "message": e.message,
                "photos": 0, "videos": 0, "people": [], "library_path_present": False}
