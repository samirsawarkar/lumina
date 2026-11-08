"""Lumina: search Apple Photos from Hermes. Tools, a pre_tool_call guard, and a bundled skill."""

from __future__ import annotations

import json
import platform
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


def _search(a):
    return photos.search(photos.get_db(), **{k: a[k] for k in _SEARCH_KEYS if a.get(k) is not None})


def _album(a):
    photos.get_db()  # surfaces no_access / no_library before touching Photos
    return photos.create_album(a.get("name"), a.get("uuids"))


def _info(_a):
    return photos.library_info(photos.get_db())


def _guard(tool_name, args, **kwargs):
    """Veto malformed album requests before they reach Apple Photos."""
    if tool_name != "lumina_create_album":
        return None
    u = (args or {}).get("uuids")
    if not str((args or {}).get("name") or "").strip() or not isinstance(u, list) or not 0 < len(u) <= 500:
        return {"action": "block", "message": "lumina_create_album needs a name and 1-500 uuids from a previous lumina_search."}
    return None


_STR = {"type": "string"}
_SCHEMAS = {
    "lumina_search": {
        "name": "lumina_search",
        "description": "Search the user's Apple Photos library by free text (text found in images, scene labels, titles, keywords, places), "
                       "named person, place, scene label, date range or media type. At least one filter is required. Newest first. "
                       "Returns photo ids (uuid) and metadata, never file paths.",
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
    "lumina_library_info": {
        "name": "lumina_library_info",
        "description": "Photo and video counts, top named people, and whether Lumina can read the library. Use for onboarding or permission checks.",
        "parameters": {"type": "object", "properties": {}},
    },
}
_HANDLERS = {"lumina_search": _search, "lumina_create_album": _album, "lumina_library_info": _info}


def register(ctx):
    for name, fn in _HANDLERS.items():
        ctx.register_tool(
            name=name, toolset="lumina", schema=_SCHEMAS[name], handler=_json(fn),
            check_fn=lambda: platform.system() == "Darwin", description=_SCHEMAS[name]["description"],
        )
    ctx.register_hook("pre_tool_call", _guard)
    ctx.register_skill("lumina", Path(__file__).parent / "skills" / "lumina" / "SKILL.md")
