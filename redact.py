"""Safe Send: black out sensitive fields on a copy, re-read the copy to prove they are gone, stamp it.

Sensitive VALUES never leave this module: not in meta files, return values, logs or errors.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
import time
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import photos

log = logging.getLogger(__name__)
LuminaError = photos.LuminaError

PRESETS = {
    "hotel": {"number", "dob", "mrz"},  # hotels need the name and the face
    "airline": {"mrz", "dob"},  # airlines need name + document number
    "all": {"number", "dob", "mrz", "name", "contact", "password"},
}
TTL = 24 * 3600
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp", ".gif"}
MAX_IMAGE_BYTES = 30_000_000


def image_file(path):
    """Resolved Path of an existing image file (known extension, <= 30 MB), else None."""
    try:
        p = Path(path).expanduser().resolve()
        return p if p.suffix.lower() in IMAGE_EXTS and p.is_file() and p.stat().st_size <= MAX_IMAGE_BYTES else None
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


@dataclass
class Line:
    text: str
    box: tuple  # (x0, y0, x1, y1) pixels, top-left origin
    _cand: object = None
    _size: tuple = (0, 0)

    def sub(self, start: int, end: int):
        """Pixel box of text[start:end], or None when Vision cannot give one."""
        if self._cand is None or end <= start:
            return None
        try:
            r, _ = self._cand.boundingBoxForRange_error_((start, end - start), None)
            return _px(r.boundingBox(), *self._size)
        except Exception:
            return None


@dataclass
class Hit:
    kind: str
    value: str
    box: tuple | None
    idx: int = 0


def _px(bb, w, h):
    x, y, bw, bh = bb.origin.x, bb.origin.y, bb.size.width, bb.size.height
    return (x * w, (1 - y - bh) * h, (x + bw) * w, (1 - y) * h)


def _pixel_size(path):
    """(w, h) of the image as Vision sees it (EXIF orientation applied), via ImageIO so HEIC works; Pillow as fallback."""
    try:
        import Quartz
        from Foundation import NSURL

        pr = Quartz.CGImageSourceCopyPropertiesAtIndex(Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(str(path)), None), 0, None)
        w, h = int(pr["PixelWidth"]), int(pr["PixelHeight"])
        return (h, w) if int(pr.get("Orientation", 1)) in (5, 6, 7, 8) else (w, h)
    except Exception:
        from PIL import Image

        with Image.open(path) as im:
            return im.size


def ocr_boxes(path, correct=False) -> list[Line]:
    """Apple Vision OCR (accurate) with pixel boxes. Imports Vision lazily; raises LuminaError when unavailable."""
    try:
        import Vision
        from Foundation import NSURL
    except ImportError:
        raise LuminaError("redact_unavailable", "On-device text recognition is not available here.") from None
    w, h = _pixel_size(path)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)
    req.setUsesLanguageCorrection_(correct)  # False for redaction (exact characters), True for reading
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    ok, _ = handler.performRequests_error_([req], None)
    if not ok:
        raise LuminaError("redact_failed", "Could not read the text in that photo.")
    out = []
    for o in req.results() or []:
        c = o.topCandidates_(1)[0]
        out.append(Line(str(c.string()), _px(o.boundingBox(), w, h), c, (w, h)))
    out.sort(key=lambda l: (l.box[1], l.box[0]))
    return out


# ---- classifier ----
_I = re.I
_LABELS = {
    "number": re.compile(r"\b(?:no|number|id|card\s*no|ref(?:erence)?|pnr|booking|ticket|account|acct|policy|licen[cs]e)\b\.?\s*[:#]\s*([A-Za-z0-9][A-Za-z0-9 \-]{2,})", _I),
    "name": re.compile(r"\b(?:surname|given\s+names?|names?|passenger|member|borrower|holder)\s*:\s*(\S.*)$", _I),
    "password": re.compile(r"\b(?:password|passcode|pin)\s*:\s*(\S.*)$", _I),
}
_BARE_PW = re.compile(r"\b(?:password|passcode|pin)\s*:?\s*$", _I)
_DOB = re.compile(r"\b(?:birth|dob|born)\b[\s:.\-]*(.*)$", _I)
_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]{6,}|[A-Z0-9]+(?:-[A-Z0-9]+)+)(?![A-Za-z0-9])")
_RUN = re.compile(r"(?<![\w.,$:/+])\d(?:[ \-]?\d){5,}(?![\w.,:/])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.]\d{1,2}[/.]\d{2,4}")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d ()\-]{7,}\d(?![\w.])")
_MRZ = re.compile(r"[A-Z0-9<]{20,}")


def _is_mrz(s: str) -> bool:
    return "<<<" in s or bool(_MRZ.fullmatch(s.replace(" ", "")))


def classify(texts: list[str]) -> list[tuple]:
    """(line index, kind, start, end) for every sensitive span, all kinds. Pure text, no boxes."""
    out = set()
    for i, s in enumerate(texts):
        if _is_mrz(s):
            out.add((i, "mrz", 0, len(s)))
            continue
        for kind, rx in _LABELS.items():
            for m in rx.finditer(s):
                v = m.group(1)
                if kind == "number" and not re.search(r"\d", v):
                    continue
                out.add((i, kind, m.start(1), m.start(1) + len(v.rstrip())))
        if _BARE_PW.search(s) and i + 1 < len(texts) and len(texts[i + 1].split()) == 1:  # one token: not prose
            out.add((i + 1, "password", 0, len(texts[i + 1])))
        if m := _DOB.search(s):
            if m.group(1).strip():
                out.add((i, "dob", m.start(1), m.start(1) + len(m.group(1).rstrip())))
            elif i + 1 < len(texts) and re.search(r"\d", texts[i + 1]) and ":" not in texts[i + 1]:
                out.add((i + 1, "dob", 0, len(texts[i + 1])))
        for m in _TOKEN.finditer(s):
            if re.search(r"\d", m.group()) and not _DATE.fullmatch(m.group()):
                out.add((i, "number", m.start(), m.end()))
        for m in _RUN.finditer(s):
            if not _DATE.fullmatch(m.group()) and not re.search(r"\+[\d ()\-]*$", s[:m.start()]):  # +1 555 ... is a phone
                out.add((i, "number", m.start(), m.end()))
        for m in _EMAIL.finditer(s):
            out.add((i, "contact", m.start(), m.end()))
        for m in _PHONE.finditer(s):
            out.add((i, "contact", m.start(), m.end()))
    out = {h for h in out if h[3] > h[2]}
    return sorted(h for h in out if not any(o != h and o[:2] == h[:2] and o[2] <= h[2] and h[3] <= o[3] for o in out))


def find_sensitive(lines, preset: str = "hotel") -> list[Hit]:
    """Hits for the preset. `lines` are Line objects (boxes filled in) or plain strings (box None)."""
    kinds = PRESETS[preset]
    texts = [getattr(l, "text", l) for l in lines]
    hits = []
    for i, kind, a, b in classify(texts):
        if kind not in kinds:
            continue
        ln = lines[i]
        box = None
        if hasattr(ln, "box"):
            box = ln.sub(a, b) if (a, b) != (0, len(ln.text)) else None
            box = box or ln.box
        hits.append(Hit(kind, texts[i][a:b], box, i))
    mrz = [h for h in hits if h.kind == "mrz" and h.box]
    if mrz:  # partial MRZ reads: everything from the first MRZ line down goes, full width
        top = min(h.box[1] for h in mrz)
        w, ht = lines[0]._size if lines and lines[0]._size[0] else (0, 0)
        if w:
            band = (w * 0.02, top, w * 0.98, ht)
            have = {h.idx for h in mrz}
            hits += [Hit("mrz", l.text, None, i) for i, l in enumerate(lines) if i not in have and l.box[1] >= top - 1]
            for h in hits:
                if h.kind == "mrz":
                    h.box = band
    return hits


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.upper())


# ---- pixels ----
def _blackout(im, boxes) -> None:
    from PIL import ImageDraw

    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in boxes:
        pad = max(4, 0.15 * (y1 - y0))
        d.rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), fill=(0, 0, 0))


def _stamp(src: Path, out: Path, found: int) -> None:
    from PIL import Image, ImageDraw

    im = Image.open(src).convert("RGB")
    w, h = im.size
    size = max(12, w // 38)
    strip = size * 2
    canvas = Image.new("RGB", (w, h + strip), (14, 40, 30))
    canvas.paste(im, (0, 0))
    ImageDraw.Draw(canvas).text(
        (w // 2, h + strip // 2), f"Lumina verified on-device · {found} sensitive items hidden · 0 remain",
        font=photos._font(size), fill=(220, 255, 235), anchor="mm")
    canvas.save(out, "JPEG", quality=90)


def _make(src, out: Path, preset: str) -> dict:
    """src image -> verified, stamped JPEG at `out`. Raises not_found (nothing sensitive) / redact_unverified; no file left on failure."""
    from PIL import Image, ImageOps

    work = Path(tempfile.mkdtemp(prefix="red-", dir=photos._data_dir()))
    try:
        try:
            img = Image.open(src)
            img.load()
        except Exception:  # e.g. HEIC: Pillow cannot read it, macOS can
            conv = work / "conv.jpg"
            subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(conv)], check=True, capture_output=True, timeout=60)
            img = Image.open(conv)
        base = ImageOps.exif_transpose(img).convert("RGB")
        base.thumbnail((photos.EXPORT_MAX_SIDE,) * 2)
        flat = work / "in.png"
        base.save(flat)
        lines = ocr_boxes(flat)
        hits = find_sensitive(lines, preset)
        if not hits:
            raise LuminaError("not_found", "No sensitive details found in this photo.")
        im = Image.new("RGB", base.size)
        im.paste(base)  # fresh image: pixels only, no EXIF / GPS
        _blackout(im, [h.box for h in hits])
        red = work / "red.jpg"
        im.save(red, "JPEG", quality=90)
        again = ocr_boxes(red)
        norm = _norm(" ".join(l.text for l in again))
        vals = {n for h in hits if len(n := _norm(h.value)) >= 3}
        remaining = sum(v in norm for v in vals) + len(find_sensitive(again, preset))
        if remaining:
            raise LuminaError("redact_unverified", "Could not prove the copy is safe; nothing was sent.")
        _stamp(red, out, len(hits))
        return {"found": len(hits), "remaining": 0, "verified": True, "kinds": sorted({h.kind for h in hits}),
                "document": safe_name([l.text for l in lines])}
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def redact(db, uuid: str | None = None, preset: str = "hotel", path: str | None = None) -> dict:
    """Safe copy of one library photo (`uuid`) or one image file (`path`)."""
    if (uuid is None) == (path is None):
        raise LuminaError("bad_args", "Give exactly one of uuid or path.")
    if path is not None:
        if (file := image_file(path)) is None:
            raise LuminaError("bad_args", "path must be an existing image file (jpg, png, heic, ...) up to 30 MB.")
    elif not isinstance(uuid, str) or not photos.UUID_RE.fullmatch(uuid):
        raise LuminaError("bad_args", "uuid must be a photo id from lumina_search.")
    if preset not in PRESETS:
        raise LuminaError("bad_args", "preset must be hotel, airline or all.")
    d = photos._data_dir() / "redacted"
    d.mkdir(exist_ok=True)
    for f in d.iterdir():
        try:
            if time.time() - f.stat().st_mtime > TTL:
                f.unlink()
        except OSError:
            pass
    p = None
    if path is None:
        p = db.get_photo(uuid)
        if p is None or p.intrash:
            raise LuminaError("not_found", "Could not find that photo.")
    tmp = Path(tempfile.mkdtemp(prefix="red-", dir=photos._data_dir()))
    try:
        if p is not None:
            file = tmp / "src.jpg"
            try:
                ok = photos._export_one(p, file)
            except Exception as e:
                log.warning("redact export failed: %s", type(e).__name__)
                ok = False
            if not ok:  # original not on disk (iCloud): the largest local derivative will do
                ders = [f for f in map(Path, getattr(p, "path_derivatives", None) or []) if f.is_file()]
                if not ders:
                    raise LuminaError("not_found", "That photo has no local file (stored in iCloud) or is a video.")
                file = max(ders, key=lambda f: f.stat().st_size)
        rid = _uuid.uuid4().hex[:12]
        out = d / f"{rid}.jpg"
        r = _make(file, out, preset)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    meta = {"id": rid, "uuid": p.uuid if p else None, "document": r["document"], "preset": preset, "found": r["found"], "remaining": 0,
            "verified": True, "kinds": r["kinds"], "created": datetime.now().isoformat(timespec="seconds")}
    if path is not None:
        meta["path"] = str(file)  # stays on this Mac; the desktop card needs a "before" picture
    (d / f"{rid}.json").write_text(json.dumps(meta))
    return {k: meta[k] for k in ("id", "document", "preset", "found", "remaining", "verified", "kinds")} | {
        "media": f"MEDIA:{out}", "display": f'::lumina-redact{{id="{rid}"}}'}


def looks_like_id(texts: list[str]) -> bool:
    """Guardian: plain OCR strings contain a number, dob or mrz hit."""
    return any(k in PRESETS["hotel"] for _, k, _, _ in classify(texts))


_DOC_TYPE_RE = re.compile(r"\b(passport|driv\w* licen[cs]e|licen[cs]e|identity card|id card|residence permit|visa|boarding pass|"
                          r"vaccination (?:record|card)|membership|library card|gym|insurance card|health card|ticket|card)\b", re.I)
_ORG_RE = re.compile(r"\b(gym|bank|library|club|clinic|hospital|airline|airways|rail|university|school|insurance|studio|ltd|inc|llc)\b", re.I)


def safe_name(texts: list[str]) -> str:
    """Document title from lines that contain nothing sensitive (a name line must never become the title)."""
    # Only a known document-type word may become the title: a free first line can be a person's name.
    joined = " ".join(texts)
    m = _DOC_TYPE_RE.search(joined)
    if m:
        return m.group(0).title()
    bad = {i for i, *_ in classify(texts)}
    rest = [t for i, t in enumerate(texts) if i not in bad and _ORG_RE.search(t)]
    return photos.document_name(rest) if rest else "Document"
