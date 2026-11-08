"""Lumina backend routes, mounted at /api/plugins/lumina/. Thin wrappers over photos.py."""

from __future__ import annotations

import importlib.util
import re
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

# Loaded by path: this file may be imported outside the plugin package.
_spec = importlib.util.spec_from_file_location("lumina_photos", Path(__file__).resolve().parent.parent / "photos.py")
photos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(photos)

router = APIRouter()
_UUID = re.compile(r"^[0-9A-Fa-f-]{8,64}$")


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


@router.get("/status")
def status():
    try:
        return {**photos.library_info(photos.get_db()), "access": "ok"}
    except photos.LuminaError as e:
        return {"access": "no_access", "error": e.code, "message": e.message,
                "photos": 0, "videos": 0, "people": [], "library_path_present": False}
