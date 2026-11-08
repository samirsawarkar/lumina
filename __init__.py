"""Lumina: search Apple Photos from Hermes. Tools, a pre_tool_call guard, and a bundled skill."""

from __future__ import annotations

import datetime as dt
import json
import platform
import re
from pathlib import Path

from . import photos


def _json(fn):
    """Wrap a handler: run it, return a JSON string, map LuminaError / other failures to the error shape."""
    def handler(args, **kwargs):
        try:
            return json.dumps(fn(args or {}))
        except photos.LuminaError as e:
            out = {"error": e.code, "message": e.message}
        except PermissionError:
            out = {"error": "no_access", "message": photos.NO_ACCESS_MSG}
        except Exception as e:
            err = photos._classify(e)
            out = {"error": err.code, "message": err.message}
        return json.dumps(out)
    return handler


_SEARCH_KEYS = ("text", "person", "place", "label", "date_from", "date_to", "media", "limit")


def _month(s):
    return dt.date.fromisoformat(s[:10]).strftime("%b %Y")


def _title(a):
    """Friendly grid title; characters outside the allowlist are stripped so it cannot break the directive."""
    try:
        d0, d1 = (a.get("date_from") or "")[:10], (a.get("date_to") or "")[:10]
        if d0 and d1 and d0[:4] == d1[:4] and d0[5:] == "01-01" and d1[5:] == "12-31":
            t = f"Photos from {d0[:4]}"
        elif d0 and d1 and d0[:7] == d1[:7]:
            t = dt.date.fromisoformat(d0).strftime("%B %Y")
        elif d0 and d1:
            t = f"{_month(d0)} \u2013 {_month(d1)}"
        elif d0:
            t = f"Since {_month(d0)}"
        elif d1:
            t = f"Before {_month(d1)}"
        else:
            t = ""
    except ValueError:
        t = ""
    if not t:
        who = [str(a[k]) for k in ("text", "person") if a.get(k)]
        t = " \u00b7 ".join(who + [str(a[k]) for k in ("label", "place") if a.get(k)])
    t = re.sub(r"[^\w\s,.\-\u2013\u00b7]", " ", t)  # allowlist: no quotes, braces, backslashes
    t = " ".join(t.split())[:60]
    return t[:1].upper() + t[1:] or "Photos"


def _display(a, res, title=None):
    """Ready-to-paste transcript directive covering ALL matches (newest first, max 200)."""
    if not res["photos"]:
        return None
    return f'::lumina-photos{{ids="{",".join(res["all_uuids"])}" title="{title or _title(a)}"}}'


def _search(a):
    res = photos.search(photos.get_db(), **{k: a[k] for k in _SEARCH_KEYS if a.get(k) is not None})
    res.setdefault("all_uuids", [p["uuid"] for p in res["photos"]])
    if d := _display(a, res):
        res["display"] = d
    res.pop("all_uuids")
    return res


def _on_this_day(a):
    day = photos._day(a["date"]) if a.get("date") else dt.date.today()
    res = photos.search(photos.get_db(), on_day=day, limit=a.get("limit") or 24)
    res.setdefault("all_uuids", [p["uuid"] for p in res["photos"]])
    if d := _display(a, res, f"On this day \u00b7 {day.day} {day:%b}"):
        res["display"] = d
    res.pop("all_uuids")
    return res


def _export(a):
    return photos.export(photos.get_db(), a.get("uuids"))


def _read_text(a):
    res = photos.read_text(photos.get_db(), a.get("uuids"))
    if res["photos"]:
        t = _title({"text": next((p["title"] for p in res["photos"] if p["title"]), "Text from photo")})
        res["display"] = f'::lumina-photos{{ids="{",".join(p["uuid"] for p in res["photos"])}" title="{t}"}}'
    return res


def _make_memory(a):
    u = a.get("uuids")
    if not u:
        if not any(a.get(k) for k in _SEARCH_KEYS[:6]):
            raise photos.LuminaError("bad_args", "Give uuids from a previous lumina_search, or at least one search filter.")
        u = photos.search(photos.get_db(), **{k: a[k] for k in _SEARCH_KEYS[:6] if a.get(k)}, limit=200)["all_uuids"]
        if not u:
            raise photos.LuminaError("not_found", "No photos matched, so there is nothing to make a memory from.")
    return photos.make_memory(photos.get_db(), u, a.get("title") or _title(a))


def _album(a):
    photos.get_db()  # surfaces no_access / no_library before touching Photos
    return photos.create_album(a.get("name"), a.get("uuids"))


def _info(_a):
    return photos.library_info(photos.get_db())


def _guard(tool_name, args, **kwargs):
    """Veto malformed album / export requests before they reach Apple Photos or the disk."""
    if tool_name == "lumina_export":
        u = (args or {}).get("uuids")
        if not isinstance(u, list) or not 0 < len(u) <= 10 or not all(isinstance(x, str) and photos.UUID_RE.fullmatch(x) for x in u):
            return {"action": "block", "message": "lumina_export needs 1-10 photo uuids from a previous lumina_search."}
        return None
    if tool_name == "lumina_read_text":
        u = (args or {}).get("uuids")
        if not isinstance(u, list) or not 0 < len(u) <= 5 or not all(isinstance(x, str) and photos.UUID_RE.fullmatch(x) for x in u):
            return {"action": "block", "message": "lumina_read_text needs 1-5 photo uuids from a previous lumina_search."}
        return None
    if tool_name == "lumina_make_memory":
        u = (args or {}).get("uuids")
        if u is not None and (not isinstance(u, list) or not 0 < len(u) <= 200 or not all(isinstance(x, str) and photos.UUID_RE.fullmatch(x) for x in u)):
            return {"action": "block", "message": "lumina_make_memory uuids must be 1-200 photo uuids from a previous lumina_search."}
        return None
    if tool_name != "lumina_create_album":
        return None
    u = (args or {}).get("uuids")
    if not str((args or {}).get("name") or "").strip() or not isinstance(u, list) or not 0 < len(u) <= 500:
        return {"action": "block", "message": "lumina_create_album needs a name and 1-500 uuids from a previous lumina_search."}
    return None


_PENDING_MEDIA = {}  # session -> MEDIA line from this turn's lumina_make_memory (all platforms)
_PENDING = {}  # session -> display from this turn's lumina_search; capped, cleared on use and at the next turn
_MARKER = "::lumina-photos{"
_PHOTO_RE = re.compile(r"\b(photos?|pictures?|pics?|albums?|selfies?|screenshots?|find my|passport|receipt)\b", re.I)
_ASK_RE = re.compile(r"\b(when (does|is)|what'?s my|what is my|how much|number|password|expir\w*|total|code|pin)\b", re.I)
_DOC_RE = re.compile(r"\b(passport|receipt|bill|ticket|card|wi-?fi|licen[cs]e)\b", re.I)
_SEND_RE = re.compile(r"\b(send|share|text|message|forward|whatsapp|telegram)\b", re.I)
_MEMORY_RE = re.compile(r"\b(memory|memories|recap|slideshow|video|movie|film|montage|reel)\b", re.I)
_THIS_DAY_RE = re.compile(r"\bon this day\b", re.I)
_TRIP_RE = re.compile(r"\b(trips?|holidays?|vacations?|weddings?|birthdays?|years?|(19|20)\d\d)\b", re.I)
_LOCAL_SURFACES = {"", "cli", "tui", "desktop", "local"}  # Hermes agent.platform values with an in-app photo grid


def _messaging(platform):
    return (platform or "").lower() not in _LOCAL_SURFACES


_ASK_HINT = ("The answer may be written inside one of the user's photos. Call lumina_search with a `text` filter for the document "
             "(e.g. 'passport', 'wi-fi', 'total'), then lumina_read_text with the best 1-3 uuids, and answer with the fact in one short "
             "sentence. Do not list uuids.")
_SEND_HINT = ("The user is on a messaging platform and wants photos sent. Call lumina_search, then lumina_export with the chosen uuids "
              "(max 10) and put each returned MEDIA:<path> line on its own line in your reply so the image is delivered. "
              "Do not paste the ::lumina-photos directive here.")
_MEMORY_HINT = ("The user wants a memory video. Call lumina_make_memory with the matching filters (text/person/place/label/date_from/date_to), "
                "or uuids from the last search, then put the returned `media` line on its own line in your reply and add one warm sentence.")
_HINT = ("Lumina's lumina_search is the source of truth for the user's Apple Photos library: use it, not terminal or file search, "
         "for photo questions. Present what it finds without judging whether images are real or samples. The photo grid is attached "
         "automatically, so answer in one or two short sentences and do not list uuids or file paths.")


def _remember(tool_name, result, session_id="", **kwargs):
    """post_tool_call: keep the latest lumina_search display for this session."""
    key = session_id  # transform_llm_output / pre_llm_call only receive session_id
    if tool_name == "lumina_make_memory" and key:
        try:
            m = json.loads(result).get("media")
        except Exception:
            return
        if m:
            _PENDING_MEDIA.pop(key, None)
            _PENDING_MEDIA[key] = m
            while len(_PENDING_MEDIA) > 200:
                _PENDING_MEDIA.pop(next(iter(_PENDING_MEDIA)))
        return
    if tool_name not in ("lumina_search", "lumina_on_this_day", "lumina_read_text") or not key:
        return
    try:
        d = json.loads(result).get("display")
    except Exception:
        return
    if d:
        _PENDING.pop(key, None)
        _PENDING[key] = d
        while len(_PENDING) > 200:
            _PENDING.pop(next(iter(_PENDING)))


def _append(response_text="", session_id="", platform="", **kwargs):
    """transform_llm_output: add the grid directive if the model forgot it (desktop only; chat apps cannot render it)."""
    m = _PENDING_MEDIA.pop(session_id, None)
    if m and m not in response_text:
        return f"{response_text.rstrip()}\n\n{m}"
    d = _PENDING.pop(session_id, None)
    if d and _MARKER not in response_text and not _messaging(platform):
        return f"{response_text.rstrip()}\n\n{d}"
    return None


def _route(user_message="", session_id="", platform="", **kwargs):
    """pre_llm_call: new turn drops stale state; photo-ish requests get a nudge toward lumina_search."""
    _PENDING.pop(session_id, None)
    _PENDING_MEDIA.pop(session_id, None)
    if (isinstance(user_message, str) and _MEMORY_RE.search(user_message) and not _THIS_DAY_RE.search(user_message)
            and (_PHOTO_RE.search(user_message) or _TRIP_RE.search(user_message))):
        return {"context": _HINT + " " + _MEMORY_HINT}
    if isinstance(user_message, str) and _ASK_RE.search(user_message) and (_PHOTO_RE.search(user_message) or _DOC_RE.search(user_message)):
        return {"context": _HINT + " " + _ASK_HINT}
    if isinstance(user_message, str) and _PHOTO_RE.search(user_message):
        return {"context": _HINT + (" " + _SEND_HINT if _messaging(platform) and _SEND_RE.search(user_message) else "")}
    return None


_STR = {"type": "string"}
_SCHEMAS = {
    "lumina_search": {
        "name": "lumina_search",
        "description": "Search the user's Apple Photos library by free text (text found in images, scene labels, titles, keywords, places), "
                       "named person, place, scene label, date range or media type. At least one filter is required. Newest first. "
                       "Returns photo ids (uuid) and metadata, never file paths. Results include a `display` line: paste it verbatim on its own line in your answer to show the photos.",
        "parameters": {"type": "object", "properties": {
            "text": {**_STR, "description": "Free text, e.g. 'passport'"},
            "person": {**_STR, "description": "Named person in Photos, e.g. 'Mom'"},
            "place": {**_STR, "description": "Place name, e.g. 'Goa'"},
            "label": {**_STR, "description": "Scene label, e.g. 'beach'"},
            "date_from": {**_STR, "description": "ISO date YYYY-MM-DD"},
            "date_to": {**_STR, "description": "ISO date YYYY-MM-DD"},
            "media": {**_STR, "enum": ["photo", "video", "screenshot", "any"]},
            "limit": {"type": "integer", "description": "Default 24, max 200"},
        }},
    },
    "lumina_create_album": {
        "name": "lumina_create_album",
        "description": "Create (or add to an existing same-name) album in Apple Photos from photo uuids returned by lumina_search.",
        "parameters": {"type": "object", "required": ["name", "uuids"], "properties": {
            "name": _STR,
            "uuids": {"type": "array", "items": _STR, "maxItems": 500},
        }},
    },
    "lumina_on_this_day": {
        "name": "lumina_on_this_day",
        "description": "Photos taken on this day (same month and day) in earlier years, newest year first, with a `by_year` count. "
                       "Optional ISO `date` (default today). Results include a `display` line: paste it verbatim on its own line.",
        "parameters": {"type": "object", "properties": {
            "date": {**_STR, "description": "ISO date YYYY-MM-DD, default today"},
            "limit": {"type": "integer", "description": "Default 24, max 200"},
        }},
    },
    "lumina_export": {
        "name": "lumina_export",
        "description": "Export up to 10 photos (uuids from lumina_search) as JPEGs so a messaging app (Telegram, WhatsApp, ...) can deliver them. "
                       "Returns `media` lines like MEDIA:/path.jpg: put EACH one on its own line in your reply to send the image. "
                       "Only for messaging platforms; on desktop use the ::lumina-photos display.",
        "parameters": {"type": "object", "required": ["uuids"], "properties": {
            "uuids": {"type": "array", "items": _STR, "minItems": 1, "maxItems": 10},
        }},
    },
    "lumina_read_text": {
        "name": "lumina_read_text",
        "description": "Read the full text written inside up to 5 photos (uuids from lumina_search). Use after lumina_search when the user asks "
                       "for a fact written in a photo (dates, numbers, passwords, totals). Answer with just the fact in one short sentence. "
                       "The `display` line is added to your answer automatically.",
        "parameters": {"type": "object", "required": ["uuids"], "properties": {
            "uuids": {"type": "array", "items": _STR, "minItems": 1, "maxItems": 5},
        }},
    },
    "lumina_make_memory": {
        "name": "lumina_make_memory",
        "description": "Make a short (~20 s) memory video from photos: picks up to 12 spread across the time span, with slow zoom and captions. "
                       "Give `uuids` from lumina_search, or search filters (text, person, place, label, date_from, date_to). "
                       "Put the returned `media` line on its own line in your reply, with no other text on that line; it plays inline in "
                       "Hermes Desktop and is delivered as a video on Telegram/WhatsApp. Say one warm sentence about it.",
        "parameters": {"type": "object", "properties": {
            "uuids": {"type": "array", "items": _STR, "minItems": 1, "maxItems": 200},
            "title": {**_STR, "description": "Title card text; default is derived from the filters"},
            "text": _STR, "person": _STR, "place": _STR, "label": _STR,
            "date_from": {**_STR, "description": "ISO date YYYY-MM-DD"},
            "date_to": {**_STR, "description": "ISO date YYYY-MM-DD"},
        }},
    },
    "lumina_library_info": {
        "name": "lumina_library_info",
        "description": "Photo and video counts, top named people, and whether Lumina can read the library. Use for onboarding or permission checks.",
        "parameters": {"type": "object", "properties": {}},
    },
}
_HANDLERS = {"lumina_search": _search, "lumina_create_album": _album, "lumina_library_info": _info,
             "lumina_on_this_day": _on_this_day, "lumina_export": _export,
             "lumina_read_text": _read_text, "lumina_make_memory": _make_memory}


def register(ctx):
    try:  # documented per-plugin data dir; falls back to HERMES_HOME/plugin-data/lumina
        photos.set_cache_dir(ctx.state.data_dir)
    except Exception:
        pass
    for name, fn in _HANDLERS.items():
        ctx.register_tool(
            name=name, toolset="lumina", schema=_SCHEMAS[name], handler=_json(fn),
            check_fn=lambda: platform.system() == "Darwin", description=_SCHEMAS[name]["description"],
        )
    for name, fn in (("pre_tool_call", _guard), ("post_tool_call", _remember),
                     ("transform_llm_output", _append), ("pre_llm_call", _route)):
        ctx.register_hook(name, fn)
    ctx.register_skill("lumina", Path(__file__).parent / "skills" / "lumina" / "SKILL.md")
