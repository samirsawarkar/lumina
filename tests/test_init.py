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
