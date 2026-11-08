import json
from pathlib import Path

import pytest

import lumina


class Ctx:
    def __init__(self):
        self.tools, self.hooks, self.skills = {}, {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (schema, handler)

    def register_hook(self, name, cb, **kw):
        self.hooks[name] = cb

    def register_skill(self, name, path, **kw):
        assert Path(path).exists()
        self.skills[name] = path


def test_register():
    c = Ctx()
    lumina.register(c)
    assert set(c.tools) == {"lumina_search", "lumina_create_album", "lumina_library_info"}
    assert "lumina_delete" not in c.tools and "lumina" in c.skills and "pre_tool_call" in c.hooks
    for name, (schema, _) in c.tools.items():
        assert schema["name"] == name and schema["parameters"]["type"] == "object"


def test_guard_blocks_bad_album_only():
    g = lumina._guard
    assert g("lumina_create_album", {"name": "", "uuids": ["A"]})["action"] == "block"
    assert g("lumina_create_album", {"name": "x", "uuids": []})["action"] == "block"
    assert g("lumina_create_album", {"name": "x", "uuids": ["A"]}) is None
    assert g("lumina_search", {}) is None


def test_tools_against_library(library, monkeypatch):
    monkeypatch.setenv("LUMINA_LIBRARY", library)
    c = Ctx()
    lumina.register(c)
    assert json.loads(c.tools["lumina_library_info"][1]({}))["photos"] > 0
    assert json.loads(c.tools["lumina_search"][1]({}))["error"] == "bad_args"
    assert json.loads(c.tools["lumina_create_album"][1]({"name": "", "uuids": []}))["error"] == "bad_args"


def test_no_library_and_no_access(monkeypatch, tmp_path):
    h = lumina._search
    monkeypatch.setenv("LUMINA_LIBRARY", str(tmp_path / "missing.photoslibrary"))
    out = json.loads(lumina._json(h)({"text": "x"}))
    assert out["error"] == "no_library"

    def boom(*a, **k):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(lumina.photos, "get_db", boom)
    assert json.loads(lumina._json(h)({"text": "x"}))["error"] == "no_access"


def test_search_display_line(monkeypatch):
    u = "0A1B2C3D-0000-1111-2222-333344445555"
    res = {"count": 1, "total_matches": 1, "photos": [{"uuid": u}]}
    monkeypatch.setattr(lumina.photos, "get_db", lambda: None)
    monkeypatch.setattr(lumina.photos, "search", lambda db, **k: res if k else None)
    out = json.loads(lumina._json(lumina._search)({"text": 'pass"port} {x\n', "place": "Goa"}))
    assert out["display"] == f'::lumina-photos{{ids="{u}" title="Pass port x \u00b7 Goa"}}'
    monkeypatch.setattr(lumina.photos, "search", lambda db, **k: {"count": 0, "total_matches": 0, "photos": []})
    assert "display" not in json.loads(lumina._json(lumina._search)({"text": "x"}))


D = '::lumina-photos{ids="A" title="x"}'


def _seen(sid="s", task=""):
    lumina._remember("lumina_search", json.dumps({"display": D}), session_id=sid, task_id=task)


def test_append_once_when_missing():
    lumina._PENDING.clear()
    _seen()
    assert lumina._append("Found it.", session_id="s") == f"Found it.\n\n{D}"
    assert lumina._append("Found it.", session_id="s") is None  # cleared on use


def test_append_skipped_when_present_or_unrelated():
    lumina._PENDING.clear()
    _seen()
    assert lumina._append(f"Here\n\n{D}", session_id="s") is None
    _seen()
    assert lumina._append("hi", session_id="other") is None
    lumina._remember("terminal", json.dumps({"display": D}), session_id="t")
    assert lumina._append("hi", session_id="t") is None


def test_remember_falls_back_to_task_id_and_is_bounded():
    lumina._PENDING.clear()
    _seen(sid="", task="task1")
    assert "task1" in lumina._PENDING
    for i in range(300):
        _seen(sid=f"s{i}")
    assert len(lumina._PENDING) == 200


def test_new_turn_clears_stale_and_routes_photo_requests():
    lumina._PENDING.clear()
    _seen()
    assert lumina._route("what's the weather", session_id="s") is None
    assert lumina._append("sunny", session_id="s") is None
    assert "lumina_search" in lumina._route("Find my passport photo", session_id="s")["context"]
    assert lumina._route("write a poem", session_id="s") is None


def test_hooks_registered():
    c = Ctx()
    lumina.register(c)
    assert {"post_tool_call", "transform_llm_output", "pre_llm_call"} <= set(c.hooks)


def test_title_builder():
    t = lumina._title
    assert t({"date_from": "2025-01-01", "date_to": "2025-12-31"}) == "Photos from 2025"
    assert t({"date_from": "2025-06-01", "date_to": "2025-06-30"}) == "June 2025"
    assert t({"date_from": "2025-03-05", "date_to": "2025-06-30"}) == "Mar 2025 \u2013 Jun 2025"
    assert t({"date_from": "2025-03-05"}) == "Since Mar 2025"
    assert t({"date_to": "2025-03-05"}) == "Before Mar 2025"
    assert t({"text": "passport"}) == "Passport"
    assert t({"person": "Mom", "label": "beach"}) == "Mom \u00b7 beach"
    assert t({"text": '"}{x'}) == "X"
    assert t({"date_from": "bogus"}) == "Photos"


def test_display_carries_all_uuids(monkeypatch):
    ids = [f"0A1B2C3D-0000-1111-2222-{i:012d}" for i in range(47)]
    res = {"count": 24, "total_matches": 47, "photos": [{"uuid": u} for u in ids[:24]], "all_uuids": ids}
    monkeypatch.setattr(lumina.photos, "get_db", lambda: None)
    monkeypatch.setattr(lumina.photos, "search", lambda db, **k: dict(res))
    out = json.loads(lumina._json(lumina._search)({"text": "passport"}))
    assert len(out["photos"]) == 24 and out["total_matches"] == 47 and "all_uuids" not in out
    assert out["display"] == f'::lumina-photos{{ids="{",".join(ids)}" title="Passport"}}'
