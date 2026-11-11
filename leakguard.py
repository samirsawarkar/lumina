"""Leak Guard: catches the common routes by which any tool or reply could send a raw image of an ID document off this Mac."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from . import privacy, redact

log = logging.getLogger(__name__)

_EXT = r"\.(?:jpe?g|png|heic|heif|tiff?|webp|bmp|gif)"
_QUOTED = re.compile(r"""(["'])(?:[\w.-]*[=@]+)?((?:~|/)[^"'\n]*?""" + _EXT + r")\1", re.I)
_BARE = re.compile(r"""(?<![\w.])(?:~|/)(?:\\ |[^\s"'<>|;&])*?""" + _EXT + r"(?![\w])", re.I)
_MEDIA = re.compile(r"MEDIA:\s*((?:~|/).*?" + _EXT + r")\s*$", re.I | re.M)  # the rest of a MEDIA: line is the path, spaces included
_REL = re.compile(r"""(?:(?<=[\s"'=@:])|^)([\w.+\-/]+""" + _EXT + r")(?![\w])", re.I | re.M)
_CD = re.compile(r"""\bcd\s+("[^"]+"|'[^']+'|\S+)""")
# ponytail: relative names are resolved (terminal / execute_code only) against workdir, each `cd <dir>` and the process cwd. Not seen: unquoted
# paths with spaces, relative names with spaces, chained relative cds, other env vars, globs (*.jpg), paths built in code or shell variables
_NAME_RE = re.compile(r"send|message|mail|upload|post|share|slack|telegram|discord|whatsapp|signal|tweet", re.I)
# base64 and xxd stay on purpose: their output puts the whole image into the model's context, which leaves this Mac
_NET_RE = re.compile(r"\b(curl|wget|scp|sftp|rsync|ftp|nc|ncat|ssh|mail|mailx|sendmail|mutt|osascript|rclone|aws|gsutil|gh|base64|xxd|"
                     r"requests|urllib|httpx|smtplib|fetch)\b", re.I)
_RAW_RE = re.compile(r"\b(original|unredacted|un-redacted|as is|anyway|without (?:hiding|redacting|blurring|covering))\b", re.I)
_NO_RE = re.compile(r"\b(don'?t|not|never)\b", re.I)  # "No, send the original" is a yes to the original, so a bare "no" does not count
_MAX_TEXT, _MAX_FILES = 200_000, 4
_RAW = set()  # sessions whose user asked for the raw file THIS turn
_CACHE = {}  # (real path, mtime_ns, size) -> document name or None
HELD = "Lumina held back an image that shows an ID document; it can make a safe copy with the sensitive parts hidden."


def note_turn(session_id, user_message):
    """pre_llm_call: only the user's own words lift the guard, and only for this turn."""
    _RAW.discard(session_id)
    # ponytail: keyword match; a negation anywhere in the message keeps the guard on
    if isinstance(user_message, str) and _RAW_RE.search(user_message) and not _NO_RE.search(user_message):
        _RAW.add(session_id)


def _off(session_id):
    return os.environ.get("LUMINA_LEAK_GUARD", "").strip().lower() in ("0", "off", "false") or session_id in _RAW


def _strings(v, out, n=0):
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, dict):
        v = list(v.values())
    if isinstance(v, (list, tuple)):
        for x in v:
            if sum(map(len, out)) < _MAX_TEXT:
                _strings(x, out)
    return out


def image_paths(args, rel=False) -> list[Path]:
    """Existing image files (<= 30 MB, at most 4) named anywhere in a tool call's string arguments; `rel` also resolves relative names."""
    found, home = [], os.path.expanduser("~")
    wd = args.get("workdir") if isinstance(args, dict) and isinstance(args.get("workdir"), str) else ""
    for s in _strings(args, [])[:200]:
        s = s[:_MAX_TEXT].replace("${HOME}", home).replace("$HOME", home)
        raws = [m.group(2) for m in _QUOTED.finditer(s)] + _MEDIA.findall(s) + [m.group(0).replace("\\ ", " ") for m in _BARE.finditer(s)]
        if rel:
            base = Path(wd).expanduser() if wd else Path.cwd()
            bases = [base] + [base / d.strip("\"'") for d in _CD.findall(s)] + [Path.cwd()]
            bases += [Path(d.strip("\"'")).expanduser() for d in _CD.findall(s)]
            raws += [str(b / r) for r in _REL.findall(s) for b in bases]
        for raw in raws:
            try:
                if (p := redact.image_file(raw)) and p not in found:
                    found.append(p)
            except (OSError, RuntimeError, ValueError):
                pass
    return found[:_MAX_FILES]


def outbound(tool_name, args) -> bool:
    n = tool_name or ""
    if n.startswith("lumina_") or n == "vision_analyze":
        return False
    if n.startswith("browser_") or _NAME_RE.search(n):
        return True
    if n not in ("terminal", "execute_code"):
        return False
    text = " ".join(_strings(args, []))
    for rx in (_QUOTED, _MEDIA, _BARE, _REL):  # a folder called Mail is not a mail command
        text = rx.sub(" ", text)
    return bool(_NET_RE.search(text))


def _safe_copy(p: Path) -> bool:
    """A verified Lumina safe copy: <12 hex>.jpg in a `redacted` dir whose .json meta says verified."""
    try:
        return (p.parent.name == "redacted" and re.fullmatch(r"[0-9a-f]{12}\.jpg", p.name) is not None
                and json.loads(p.with_suffix(".json").read_text()).get("verified") is True)
    except (OSError, ValueError):
        return False


def sensitive(p: Path):
    """Document name if the image looks like an ID document, else None. Fails open."""
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
        if key not in _CACHE:
            if _safe_copy(p):
                r = None
            else:
                texts = [l.text for l in redact.ocr_boxes(p)]
                k = privacy.kind_of(texts)  # precise: passwords (wifi signs), receipts, serial numbers are not blocked
                r = privacy._document(k, texts) if k in ("passport", "card", "id") else None  # fixed vocabulary, never recognised text
            if len(_CACHE) >= 300:
                _CACHE.pop(next(iter(_CACHE)))
            _CACHE[key] = r
        return _CACHE[key]
    except Exception as e:
        log.warning("Leak Guard check failed, allowing: %s", type(e).__name__)
        return None


def check(tool_name, args, session_id=""):
    """pre_tool_call for non-Lumina tools: a block directive, or None."""
    try:
        if _off(session_id) or not outbound(tool_name, args):
            return None
        for p in image_paths(args, tool_name in ("terminal", "execute_code")):
            if name := sensitive(p):
                return {"action": "block", "message": (
                    f"Lumina Leak Guard stopped this call: an image in it looks like an ID document ({name}). Nothing left this Mac. "
                    "Start your reply with one plain sentence saying exactly that; do not discuss the command, servers or ports, and do not probe further. "
                    "Then make a safe copy now with the sensitive parts hidden: call lumina_redact with path=<that image file> (preset hotel, airline or all by recipient), show it, and offer to send it instead. Only if the user says in their own words "
                    "that they want the original (for example \"send the original\") is the block lifted.")}
    except Exception as e:
        log.warning("Leak Guard failed, allowing: %s", type(e).__name__)
    return None


def scrub(text, session_id=""):
    """transform_llm_output (messaging): reply text without MEDIA lines for sensitive images, or None if unchanged."""
    try:
        if _off(session_id) or "MEDIA:" not in text:
            return None
        keep = [l for l in text.splitlines() if not (l.strip().startswith("MEDIA:") and any(sensitive(p) for p in image_paths(l)))]
        if len(keep) == len(text.splitlines()):
            return None
        return "\n".join(keep).rstrip() + f"\n\n{HELD}"
    except Exception as e:
        log.warning("Leak Guard failed, allowing: %s", type(e).__name__)
        return None
