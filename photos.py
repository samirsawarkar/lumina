"""All osxphotos / PhotoScript access for Lumina. Functions take an injected PhotosDB (testable)."""

from __future__ import annotations

import base64
import calendar
import datetime as dt
import io
import json
import logging
import os
import re
import sqlite3
import threading
import time
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


def _data_dir() -> Path:
    d = _cache_dir or Path(os.environ.get("HERMES_HOME") or "~/.hermes").expanduser() / "plugin-data" / "lumina"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file() -> Path:
    return _data_dir() / "ocr.sqlite"


def _sig(p) -> str:
    # modification date changes on edits; fingerprint covers Photos <=4 where it is None
    return f"{p.date_modified.isoformat() if getattr(p, 'date_modified', None) else ''}|{getattr(p, 'fingerprint', None) or ''}"


def _recognise(p) -> list[str]:
    """Live Vision recognition. iCloud-only originals: read the largest local derivative (detected_text() would use derivative [0])."""
    d = sorted((x for x in getattr(p, "path_derivatives", None) or [] if os.path.exists(x)), key=os.path.getsize, reverse=True)
    if d and not (getattr(p, "path", None) and os.path.exists(p.path)):
        from . import redact  # lazy: redact imports photos

        return [l.text for l in redact.ocr_boxes(d[0])]
    return [s for s, _ in p.detected_text()]


def _load_cache() -> dict:
    """{uuid: (sig, json text)} from the OCR cache, read once per scan. {} on any error."""
    try:
        con = sqlite3.connect(_cache_file())
        try:
            return {u: (g, t) for u, g, t in con.execute("SELECT uuid, sig, text FROM ocr")}
        finally:
            con.close()
    except Exception:
        return {}


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
        out = _recognise(p)
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


# ---- Apple's own text index (osxphotos search_info): lowercase single-word tokens, instant to read ----
SEARCH_BUDGET, EXPIRY_BUDGET, RECENT = 6.0, 20.0, dt.timedelta(days=3)  # seconds, seconds, "maybe not indexed yet"


def index_words(p) -> list[str]:
    try:
        return list(p.search_info.detected_text or [])
    except Exception:  # no search_info (old macOS / None) or unreadable
        return []


def _has_index(db, ps) -> bool:
    """Does Apple's text index exist? Computed once and stored on the db object (get_db() makes a new one when the library changes)."""
    r = getattr(db, "_lumina_indexed", None)
    if r is None:
        r = any(index_words(p) for p in ps)
        try:
            db._lumina_indexed = r
        except Exception:  # object refuses attributes: just recompute next time
            pass
    return r


def _cached_lines(cache, p):
    """Cached OCR lines for p from `_load_cache()`, or None (missing, stale or unreadable row)."""
    try:
        row = cache.get(p.uuid)
        return json.loads(row[1]) if row and row[0] == _sig(p) else None
    except Exception:
        return None


def _recent(p) -> bool:
    try:
        return dt.datetime.now(p.date_added.tzinfo) - p.date_added < RECENT
    except Exception:
        return False


def _ts(p):
    return p.date.timestamp() if p.date else 0


def _in_index(words, toks) -> bool:
    """Every query word is inside a token or splits completely into tokens ('wifi' = 'wi' + 'fi')."""
    long = [t for t in toks if len(t) > 1]  # 1-letter tokens (handwriting) would split anything

    def split(w):  # positions of w reachable by chaining tokens; linear in len(w) * tokens
        ok = {0}
        for i in range(len(w)):
            if i in ok:
                ok.update(i + len(t) for t in long if w.startswith(t, i))
        return len(w) in ok
    return all(any(w in t for t in toks) or split(w) for w in words)


# ---- search ----
def _is_local(p) -> bool:
    return bool(p.path and os.path.exists(p.path)) or any(os.path.exists(d) for d in (p.path_derivatives or []))


def _snippet(p, indexed=False) -> str | None:
    if indexed and not index_words(p):
        return None  # Apple's index has no text for this photo; skip a live recognition
    try:
        t = " ".join(ocr_text(p))
    except Exception:
        return None
    return t[:80] or None


def photo_dict(p, indexed=False) -> dict:
    return {
        "uuid": p.uuid,
        "date": p.date.isoformat() if p.date else None,
        "title": p.title,
        "place": p.place.name if p.place else None,
        "persons": [x for x in p.persons or [] if x != "_UNKNOWN_"],
        "labels": list(p.labels or [])[:8],
        "text_snippet": _snippet(p, indexed),
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
           media="any", limit=24, on_day=None) -> dict:
    """on_day (a date): only photos from the same month/day in earlier years (used by On this day)."""
    if not any([text, person, place, label, date_from, date_to, on_day]):
        raise LuminaError("bad_args", "Give at least one of text, person, place, label, date_from, date_to.")
    if media not in ("photo", "video", "screenshot", "any"):
        raise LuminaError("bad_args", "media must be photo, video, screenshot or any.")
    try:
        limit = max(1, min(int(limit or 24), 200))
    except (TypeError, ValueError):
        raise LuminaError("bad_args", "limit must be an integer.") from None
    d0, d1 = (_day(date_from) if date_from else None), (_day(date_to) if date_to else None)
    low = lambda s: (s or "").lower().replace("-", "")  # noqa: E731  (wifi == Wi-Fi)
    md = {(on_day.month, on_day.day)} if on_day else set()
    if on_day and md == {(2, 28)} and not calendar.isleap(on_day.year):
        md.add((2, 29))  # leap-day photos surface on Feb 28 in common years
    hits, pending = [], []
    t0 = time.monotonic()
    ps = list(db.photos(movies=True))
    indexed = _has_index(db, ps)
    words = [w for w in re.split(r"[\s\-]+", text.lower()) if w] if text else []
    cache = _load_cache() if text else {}
    # ponytail: linear scan over db.photos(); add an index if libraries >100k feel slow
    for p in ps:
        if p.hidden or p.intrash:
            continue
        day = p.date.date() if p.date else None
        if (d0 or d1) and day is None:
            continue
        if (d0 and day < d0) or (d1 and day > d1):
            continue
        if on_day and (day is None or day.year >= on_day.year or (day.month, day.day) not in md):
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
                toks = index_words(p)
                lines = _cached_lines(cache, p)
                if toks and _in_index(words, toks):
                    pass
                elif lines is not None:
                    if not any(t in low(s) for s in lines):
                        continue
                elif p.ismovie or (indexed and (toks or not _recent(p))):
                    continue  # Apple's index covers it (no match) or it is not worth a live read
                else:
                    pending.append(p)
                    continue
        hits.append(p)
    partial = False
    for p in sorted(pending, key=_ts, reverse=True):  # bounded live recognition; results fill the cache for next time
        if time.monotonic() - t0 > SEARCH_BUDGET:
            partial = True
            break
        try:
            if any(low(text) in low(s) for s in ocr_text(p)):
                hits.append(p)
        except Exception:
            pass
    hits.sort(key=_ts, reverse=True)
    return {"count": min(len(hits), limit), "total_matches": len(hits),
            "photos": [photo_dict(p, indexed) for p in hits[:limit]], "all_uuids": [p.uuid for p in hits[:200]],
            **({"text_scan": "partial", "note": "Only part of the library was read for text this time; say so, and a repeat search will cover more."}
               if partial else {}),
            **({"by_year": {str(y): sum(1 for p in hits if p.date.year == y)
                            for y in sorted({p.date.year for p in hits}, reverse=True)}} if on_day else {})}


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


# ---- export for messaging platforms: resized JPEGs the gateway can upload via MEDIA: tags ----
UUID_RE = re.compile(r"[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
EXPORT_MAX_SIDE, EXPORT_TTL = 2048, 24 * 3600


def _export_one(p, out: Path) -> bool:
    """Write p as a JPEG (long side <= 2048, EXIF dropped) to out. False when there is no local file."""
    from PIL import Image, ImageOps

    src = None
    if p.ismovie:
        return False
    if p.path and os.path.exists(p.path):
        from osxphotos import ExportOptions, PhotoExporter

        # convert_to_jpeg turns HEIC/PNG/etc. into JPEG without opening the Photos app
        r = PhotoExporter(p).export(str(out.parent), out.name, options=ExportOptions(convert_to_jpeg=True, overwrite=True))
        src = r.exported[0] if r.exported else None
    else:
        derived = sorted((d for d in (p.path_derivatives or []) if os.path.exists(d)), key=os.path.getsize, reverse=True)
        src = derived[0] if derived else None
    if not src:
        return False
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    im.thumbnail((EXPORT_MAX_SIDE, EXPORT_MAX_SIDE))
    im.save(out, "JPEG", quality=88)  # fresh mtime: the gateway's strict mode trusts recently produced files
    return True


def export(db, uuids) -> dict:
    """Export photos to <data_dir>/exports/<uuid>.jpg. Paths appear only in `media`, never in errors."""
    if not isinstance(uuids, list) or not 0 < len(uuids) <= 10 or not all(isinstance(u, str) and UUID_RE.fullmatch(u) for u in uuids):
        raise LuminaError("bad_args", "uuids must be a list of 1 to 10 photo ids from lumina_search.")
    d = _data_dir() / "exports"
    d.mkdir(exist_ok=True)
    for f in d.glob("*.jpg"):
        try:
            if time.time() - f.stat().st_mtime > EXPORT_TTL:
                f.unlink()
        except OSError:
            pass
    media, skipped = [], []
    for u in dict.fromkeys(uuids):
        p = db.get_photo(u)
        if p is None or p.intrash:
            skipped.append({"uuid": u, "reason": "not_found"})
            continue
        out = d / f"{p.uuid}.jpg"
        try:
            ok = _export_one(p, out)
        except Exception as e:
            log.warning("export failed", exc_info=e)
            skipped.append({"uuid": u, "reason": "export_failed"})
            continue
        if ok:
            media.append(f"MEDIA:{out}")
        else:
            skipped.append({"uuid": u, "reason": "no local file (stored in iCloud) or a video"})
    return {"exported": len(media), "media": media, "skipped": skipped}


READ_TEXT_MAX = 4000


def read_text(db, uuids) -> dict:
    """Full OCR text (capped) for up to 5 photos; hidden / missing ids go to `skipped`. No file paths."""
    if not isinstance(uuids, list) or not 0 < len(uuids) <= 5 or not all(isinstance(u, str) and UUID_RE.fullmatch(u) for u in uuids):
        raise LuminaError("bad_args", "uuids must be a list of 1 to 5 photo ids from lumina_search.")
    out, skipped = [], []
    for u in dict.fromkeys(uuids):
        p = db.get_photo(u)
        if p is None or p.hidden or p.intrash:
            skipped.append({"uuid": u, "reason": "not_found"})
            continue
        try:
            text = "\n".join(ocr_text(p))[:READ_TEXT_MAX]
        except Exception:
            log.warning("ocr failed for %s", u, exc_info=True)
            skipped.append({"uuid": u, "reason": "unreadable"})
            continue
        out.append({"uuid": p.uuid, "date": p.date.isoformat() if p.date else None, "title": p.title, "text": text})
    return {"photos": out, "skipped": skipped}


_EXP_KEY = re.compile(r"\b(expiry|expires|expiration|exp\.?\s*date|exp|valid\s+(?:thru|through|until|till|to)|use\s+by|renew\s+by|good\s+thru)\b[\s:.\-]*", re.I)
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
_DATE = re.compile(
    r"(?P<iy>\d{4})-(?P<im>\d{1,2})-(?P<id>\d{1,2})"
    r"|(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]{3,9})\.?,?\s+(?P<y1>\d{4})"
    r"|(?P<m2>[A-Za-z]{3,9})\.?\s+(?P<d2>\d{1,2}),?\s+(?P<y2>\d{4})"
    r"|(?P<m3>[A-Za-z]{3,9})\.?,?\s+(?P<y3>\d{4})"
    r"|(?<!\d)(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y4>\d{4})(?!\d)"
    r"|(?<!\d)(?P<m5>\d{1,2})/(?P<y5>\d{4}|\d{2})(?!\d)")


def _month_end(y, m):
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _dates_in(s):
    out = []
    for m in _DATE.finditer(s):
        g = m.groupdict()
        try:
            if g["iy"]:
                out.append(dt.date(int(g["iy"]), int(g["im"]), int(g["id"])))
            elif g["d1"] or g["d2"]:
                mon = _MONTHS.get((g["m1"] or g["m2"])[:3].lower())
                if mon:
                    out.append(dt.date(int(g["y1"] or g["y2"]), mon, int(g["d1"] or g["d2"])))
            elif g["m3"]:
                mon = _MONTHS.get(g["m3"][:3].lower())
                if mon:
                    out.append(_month_end(int(g["y3"]), mon))
            elif g["a"]:
                a, b = int(g["a"]), int(g["b"])
                # ambiguous 03/04/2030: assume day-first (documents are international); a number > 12 settles it
                d, mo = (b, a) if a <= 12 < b else (a, b)
                out.append(dt.date(int(g["y4"]), mo, d))
            else:
                y = int(g["y5"])
                out.append(_month_end(y + 2000 if y < 100 else y, int(g["m5"])))
        except ValueError:
            pass
    return out


def _just_date(s):
    """True if `s` is essentially only a date (at most 2 stray letters once the date and punctuation are removed)."""
    return len(re.findall(r"[A-Za-z]", _DATE.sub(" ", s))) <= 2


def parse_expiry(lines: list[str]) -> dt.date | None:
    """Latest date found after an expiry keyword (same line, else the next line); None if there is none."""
    found = []
    for i, line in enumerate(lines):
        for k in _EXP_KEY.finditer(line):
            ds = _dates_in(line[k.end():]) or (_dates_in(lines[i + 1]) if i + 1 < len(lines) and _just_date(lines[i + 1]) else [])
            found += ds
    return max(found) if found else None


def document_name(lines: list[str]) -> str:
    """First OCR line with letters, title-cased, max 40 chars."""
    for s in lines:
        if re.search(r"[A-Za-z]", s):
            return s.strip().title()[:40].strip()
    return "Document"


def _exp_hint(toks) -> bool:
    """Apple tokens that can lead to a _EXP_KEY phrase: expir*, exp, valid + thru/..., use/renew + by, good + thru."""
    t = {x.rstrip(".") for x in toks}
    return (any(x.startswith("expir") for x in t) or "exp" in t or "valid" in t and bool(t & {"thru", "through", "until", "till", "to"})
            or bool({"use", "renew"} & t and "by" in t) or {"good", "thru"} <= t)


def expiry(db, within_days=None, today=None) -> dict:
    """Documents with an expiry date in OCR text, soonest first; expired ones have negative days_left."""
    today = today or dt.date.today()
    t0, items, partial = time.monotonic(), [], False
    ps = sorted((p for p in db.photos(movies=False) if not (p.hidden or p.intrash or p.ismovie)), key=_ts, reverse=True)
    indexed = _has_index(db, ps)
    for p in ps:
        if indexed:  # a missed keyword hides a document, so the set is a superset; unindexed recent photos are read live
            toks = index_words(p)
            if toks and not _exp_hint(toks) or not toks and not _recent(p):
                continue
        if time.monotonic() - t0 > EXPIRY_BUDGET:
            partial = True
            break
        try:
            lines = ocr_text(p)
            d = parse_expiry(lines)
            if d is None:
                continue
            left = (d - today).days
            items.append({"uuid": p.uuid, "document": document_name(lines), "expires": d.isoformat(), "days_left": left,
                          "status": "expired" if left < 0 else "soon" if left <= 60 else "ok"})
        except Exception:
            continue
    if within_days is not None:
        items = [i for i in items if i["days_left"] <= within_days]
    items.sort(key=lambda i: i["expires"])
    return {"count": len(items), "items": items, "all_uuids": [i["uuid"] for i in items[:200]],
            **({"partial": True, "note": "Only part of the library was read this time; say so, and ask again to cover more."} if partial else {})}


# ---- "Make me a memory": Ken Burns slideshow (MP4 via ffmpeg, GIF fallback) ----
MEM_MAX, MEM_TTL, MEM_W, MEM_H, MEM_FPS, MEM_CLIP, MEM_FADE = 12, 7 * 24 * 3600, 1280, 720, 25, 2.0, 0.4
_FONTS = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc")


def _font(size):
    from PIL import ImageFont

    for f in _FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _pick(ps, k=MEM_MAX):
    """Evenly spaced across the (date-sorted) list, endpoints included; a favorite neighbour wins over a plain pick."""
    n = len(ps)
    if n <= k:
        return ps
    idx = [round(j * (n - 1) / (k - 1)) for j in range(k)]
    out, taken = [], set()
    for i in idx:
        if not ps[i].favorite:
            for j in (i - 1, i + 1):
                if 0 <= j < n and ps[j].favorite and j not in idx and j not in taken:
                    i = j
                    break
        taken.add(i)
        out.append(ps[i])
    return out


def _frame(src, caption=None, title=None, subtitle=None, size=(MEM_W, MEM_H)):
    """Cover-cropped frame with a dark bottom gradient and caption; with `title`, a darkened title card."""
    from PIL import Image, ImageDraw, ImageOps

    w, h = size
    im = ImageOps.fit(ImageOps.exif_transpose(Image.open(src)).convert("RGB"), size, Image.LANCZOS)
    s = h / 720
    if title:
        im = Image.blend(im, Image.new("RGB", size, (0, 0, 0)), 0.55)
    else:
        grad = Image.linear_gradient("L").resize((w, h // 3))  # black at top -> white at bottom
        shade = Image.new("L", size, 0)
        shade.paste(grad.point(lambda v: int(v * 0.75)), (0, h - h // 3))
        im = Image.composite(Image.new("RGB", size, (0, 0, 0)), im, shade)
    d = ImageDraw.Draw(im)
    if title:
        d.text((w / 2, h * 0.45), title, font=_font(int(84 * s)), fill="white", anchor="mm")
        if subtitle:
            d.text((w / 2, h * 0.58), subtitle, font=_font(int(36 * s)), fill=(220, 220, 220), anchor="mm")
    elif caption:
        d.text((int(56 * s), h - int(48 * s)), caption, font=_font(int(40 * s)), fill="white", anchor="lm")
    return im


def _encode_mp4(ffmpeg, frames, out: Path) -> bool:
    import subprocess

    n, fr = len(frames), round(MEM_CLIP * MEM_FPS)
    cmd = [ffmpeg, "-y", "-loglevel", "error"]
    for f in frames:
        cmd += ["-i", str(f)]
    parts = [f"[{i}:v]scale={MEM_W * 2}:{MEM_H * 2},zoompan=z='1+0.08*on/{fr}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':"
             f"d={fr}:s={MEM_W}x{MEM_H}:fps={MEM_FPS},setsar=1,scale=in_range=full:out_range=limited,format=yuv420p[v{i}]" for i in range(n)]
    last = "v0"
    for i in range(1, n):  # chained crossfades; each starts where the previous output would end, minus the overlap
        off = round(i * (MEM_CLIP - MEM_FADE), 3)
        parts.append(f"[{last}][v{i}]xfade=transition=fade:duration={MEM_FADE}:offset={off}[x{i}]")
        last = f"x{i}"
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("ffmpeg failed: %s", e)
        return False
    if r.returncode:
        log.warning("ffmpeg failed: %s", r.stderr.decode(errors="replace")[-500:])
    return r.returncode == 0 and out.exists()


def make_memory(db, uuids, title) -> dict:
    """Render up to 12 of the given photos into a ~20 s slideshow under <data_dir>/memories. Paths only in `media`."""
    import shutil
    import tempfile
    import uuid as _uuid

    if not isinstance(uuids, list) or not 0 < len(uuids) <= 200 or not all(isinstance(u, str) and UUID_RE.fullmatch(u) for u in uuids):
        raise LuminaError("bad_args", "uuids must be a list of 1 to 200 photo ids from lumina_search.")
    title = " ".join(str(title or "").split())[:60] or "Memories"
    d = _data_dir() / "memories"
    d.mkdir(exist_ok=True)
    for f in d.iterdir():
        try:
            if time.time() - f.stat().st_mtime > MEM_TTL:
                f.unlink()
        except OSError:
            pass
    ps, skipped = [], 0
    for u in dict.fromkeys(uuids):
        p = db.get_photo(u)
        if p is None or p.hidden or p.intrash or p.ismovie or p.screenshot or not _is_local(p):
            skipped += 1
        else:
            ps.append(p)
    ps.sort(key=lambda p: p.date.timestamp() if p.date else 0)
    chosen = _pick(ps)
    skipped += len(ps) - len(chosen)
    work = Path(tempfile.mkdtemp(prefix="mem-", dir=_data_dir()))
    try:
        used = []
        for p in chosen:
            f = work / f"src{len(used)}.jpg"
            try:
                if not _export_one(p, f):
                    skipped += 1
                    continue
            except Exception as e:
                log.warning("memory export failed", exc_info=e)
                skipped += 1
                continue
            used.append((p, f))
        if not used:
            raise LuminaError("not_found", "No photos with a local file to make a memory from.")
        dates = [p.date for p, _ in used if p.date]
        sub = None
        if dates:
            a, b = min(dates).strftime("%b %Y"), max(dates).strftime("%b %Y")
            sub = a if a == b else f"{a} – {b}"
        gif = not shutil.which("ffmpeg")
        size = (960, 540) if gif else (MEM_W, MEM_H)
        ims = [_frame(used[0][1], title=title, subtitle=sub, size=size)]
        for p, f in used:
            cap = p.place.name if p.place else (f"{p.date.day} {p.date:%b %Y}" if p.date else None)
            ims.append(_frame(f, caption=cap, size=size))
        name = _uuid.uuid4().hex[:12]
        if gif:
            out = d / f"{name}.gif"
            try:
                ims[0].save(out, save_all=True, append_images=ims[1:], duration=1600, loop=0)
            except Exception:
                out.unlink(missing_ok=True)
                raise
            secs = 1.6 * len(ims)
        else:
            for i, im in enumerate(ims):
                im.save(work / f"f{i:02d}.jpg", quality=92)
            out = d / f"{name}.mp4"
            if not _encode_mp4(shutil.which("ffmpeg"), [work / f"f{i:02d}.jpg" for i in range(len(ims))], out):
                out.unlink(missing_ok=True)
                raise LuminaError("render_failed", "Could not render the memory video.")
            secs = len(ims) * (MEM_CLIP - MEM_FADE) + MEM_FADE
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"media": f"MEDIA:{out}", "photos": len(used), "duration_s": round(secs, 1), "title": title,
            "format": out.suffix[1:], "skipped": skipped}
