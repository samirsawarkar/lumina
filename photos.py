"""All osxphotos / PhotoScript access for Lumina. Functions take an injected PhotosDB (testable)."""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

NO_ACCESS_MSG = (
    "Lumina cannot read the Photos library. Open System Settings > Privacy & Security > "
    "Full Disk Access, enable Hermes (or the terminal app running it), then restart it."
)

log = logging.getLogger(__name__)
PHOTOS_ERR_MSG = "Apple Photos could not complete that request. Check that Photos is available and try again."


class LuminaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


# ---- library handle: opened lazily, reloaded when the library file changes ----
_lock = threading.Lock()
_db = None
_stamp = None


def _library_path() -> Path | None:
    env = os.environ.get("LUMINA_LIBRARY")  # tests / non-default libraries
    if env:
        return Path(env)
    import osxphotos.utils as u

    p = u.get_last_library_path()
    return Path(p) if p else None


def get_db():
    """Open (or reuse) the PhotosDB. Raises LuminaError(no_access|no_library)."""
    global _db, _stamp
    import osxphotos

    path = _library_path()
    if path is None or not path.exists():
        raise LuminaError("no_library", "No Apple Photos library found.")
    try:
        stamp = (path / "database" / "Photos.sqlite").stat().st_mtime
        with _lock:
            if _db is None or stamp != _stamp:
                _db, _stamp = osxphotos.PhotosDB(dbfile=str(path)), stamp
            return _db
    except FileNotFoundError:
        raise LuminaError("no_library", "No Apple Photos library found.") from None
    except Exception as e:
        raise _classify(e) from None


def _classify(exc: Exception) -> LuminaError:
    if isinstance(exc, PermissionError) or "Operation not permitted" in str(exc):
        return LuminaError("no_access", NO_ACCESS_MSG)
    log.warning("Photos access failed", exc_info=exc)
    return LuminaError("photos_app_error", PHOTOS_ERR_MSG)


# ---- OCR cache: detected_text() is slow Apple Vision OCR, so persist it per photo ----
_cache_dir: Path | None = None


def set_cache_dir(path) -> None:
    global _cache_dir
    _cache_dir = Path(path) if path else None


def _cache_file() -> Path:
    d = _cache_dir or Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser() / "plugin-data" / "lumina"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ocr.sqlite"


def _sig(p) -> str:
    # modification date changes on edits; fingerprint covers Photos <=4 where it is None
    return f"{p.date_modified.isoformat() if getattr(p, 'date_modified', None) else ''}|{getattr(p, 'fingerprint', None) or ''}"


def ocr_text(p) -> list[str]:
    """Detected text strings for a still image, cached in sqlite by uuid + signature. Cache errors are logged, never raised."""
    if p.ismovie:
        return []
    con = sig = None
    try:
        sig = _sig(p)
        con = sqlite3.connect(_cache_file())
        con.execute("CREATE TABLE IF NOT EXISTS ocr (uuid TEXT PRIMARY KEY, sig TEXT, text TEXT)")
        row = con.execute("SELECT sig, text FROM ocr WHERE uuid=?", (p.uuid,)).fetchone()
        if row and row[0] == sig:
            return json.loads(row[1])
    except Exception as e:
        log.warning("OCR cache read failed", exc_info=e)
    try:
        out = [s for s, _ in p.detected_text()]
        if con is not None:
            try:
                con.execute("INSERT OR REPLACE INTO ocr VALUES (?,?,?)", (p.uuid, sig, json.dumps(out)))
                con.commit()
            except Exception as e:
                log.warning("OCR cache write failed", exc_info=e)
        return out
    finally:
        if con is not None:
            con.close()


# ---- search ----
def _is_local(p) -> bool:
    return bool(p.path and os.path.exists(p.path)) or any(os.path.exists(d) for d in (p.path_derivatives or []))


def _snippet(p) -> str | None:
    try:
        t = " ".join(ocr_text(p))
    except Exception:
        return None
    return t[:80] or None


def photo_dict(p) -> dict:
    return {
        "uuid": p.uuid,
        "date": p.date.isoformat() if p.date else None,
        "title": p.title,
        "place": p.place.name if p.place else None,
        "persons": [x for x in p.persons or [] if x != "_UNKNOWN_"],
        "labels": list(p.labels or [])[:8],
        "text_snippet": _snippet(p),
        "is_local": _is_local(p),
        "is_screenshot": bool(p.screenshot),
        "is_video": bool(p.ismovie),
    }


def _day(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        raise LuminaError("bad_args", f"Bad date {s!r}; use YYYY-MM-DD.") from None


def search(db, text=None, person=None, place=None, label=None, date_from=None, date_to=None,
           media="any", limit=24) -> dict:
    if not any([text, person, place, label, date_from, date_to]):
        raise LuminaError("bad_args", "Give at least one of text, person, place, label, date_from, date_to.")
    if media not in ("photo", "video", "screenshot", "any"):
        raise LuminaError("bad_args", "media must be photo, video, screenshot or any.")
    try:
        limit = max(1, min(int(limit or 24), 200))
    except (TypeError, ValueError):
        raise LuminaError("bad_args", "limit must be an integer.") from None
    d0, d1 = (_day(date_from) if date_from else None), (_day(date_to) if date_to else None)
    low = lambda s: (s or "").lower()  # noqa: E731
    hits = []
    # ponytail: linear scan over db.photos(); add an index if libraries >100k feel slow
    for p in db.photos(movies=True):
        if p.hidden or p.intrash:
            continue
        day = p.date.date() if p.date else None
        if (d0 or d1) and day is None:
            continue
        if (d0 and day < d0) or (d1 and day > d1):
            continue
        if media == "photo" and (p.ismovie or p.screenshot):
            continue
        if media == "video" and not p.ismovie:
            continue
        if media == "screenshot" and not p.screenshot:
            continue
        if person and not any(low(person) in low(x) for x in p.persons or []):
            continue
        if place and low(place) not in low(p.place.name if p.place else ""):
            continue
        if label and not any(low(label) in low(x) for x in p.labels or []):
            continue
        if text:
            t = low(text)
            hay = [p.title, p.description, p.place.name if p.place else "", *(p.labels or []), *(p.keywords or [])]
            if not any(t in low(h) for h in hay):
                try:
                    if not any(t in low(s) for s in ocr_text(p)):
                        continue
                except Exception:
                    continue
        hits.append(p)
    hits.sort(key=lambda p: p.date.timestamp() if p.date else 0, reverse=True)
    return {"count": min(len(hits), limit), "total_matches": len(hits),
            "photos": [photo_dict(p) for p in hits[:limit]]}


def library_info(db) -> dict:
    ps = [p for p in db.photos(movies=True) if not p.intrash]
    people = sorted(((n, c) for n, c in db.persons_as_dict.items() if n != "_UNKNOWN_"), key=lambda x: -x[1])
    return {
        "photos": sum(1 for p in ps if not p.ismovie),
        "videos": sum(1 for p in ps if p.ismovie),
        "people": [n for n, _ in people[:20]],
        "library_path_present": bool(db.library_path and os.path.exists(db.library_path)),
    }


# ---- album + open in Photos (PhotoScript, needs Automation permission for Photos) ----
def create_album(name: str, uuids: list[str]) -> dict:
    if not isinstance(name, str) or not name.strip():
        raise LuminaError("bad_args", "name is required.")
    if not isinstance(uuids, list) or not uuids or len(uuids) > 500 or not all(isinstance(u, str) for u in uuids):
        raise LuminaError("bad_args", "uuids must be a list of 1 to 500 photo ids.")
    import photoscript

    try:
        lib = photoscript.PhotosLibrary()
        album = lib.album(name) if name in lib.album_names() else lib.create_album(name)
        album.add([photoscript.Photo(u) for u in uuids])
    except Exception as e:  # AppleScript errors are untyped
        raise _classify(e) from None
    return {"album": name, "added": len(uuids)}


def open_photo(uuid: str) -> None:
    import photoscript

    try:
        photoscript.Photo(uuid).spotlight()
    except Exception as e:
        log.warning("open_photo failed", exc_info=e)
        raise LuminaError("not_found", "Could not show that photo in Photos.") from None


# ---- thumbnails ----
SIZES = {"s": 256, "m": 768}


def thumb_data_url(p, size: str = "s") -> str | None:
    """Biggest on-disk derivative, else local original; downscaled with Pillow if available."""
    side = SIZES.get(size, 256)
    derived = sorted((d for d in (p.path_derivatives or []) if os.path.exists(d)), key=os.path.getsize, reverse=True)
    src = derived[0] if derived else (p.path if p.path and os.path.exists(p.path) else None)
    if not src or p.ismovie and not derived:
        return None
    try:
        from PIL import Image

        im = Image.open(src)
        im = im.convert("RGB")
        im.thumbnail((side, side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=80)
        data = buf.getvalue()
    except Exception:  # no Pillow, or unreadable format (HEIC): fall back to raw derivative JPEG
        if not derived or not derived[0].lower().endswith((".jpg", ".jpeg")):
            return None
        data = Path(derived[0]).read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()
