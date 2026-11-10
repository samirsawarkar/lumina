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
    assert set(c.tools) == {"lumina_search", "lumina_create_album", "lumina_library_info", "lumina_on_this_day", "lumina_export", "lumina_read_text", "lumina_expiry", "lumina_make_memory", "lumina_redact", "lumina_privacy_check"}
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


def _seen(sid="s"):
    lumina._remember("lumina_search", json.dumps({"display": D}), session_id=sid, task_id="task-ignored")


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


def test_remember_is_keyed_by_session_and_bounded():
    lumina._PENDING.clear()
    _seen(sid="")  # no session id: nothing stored (task_id is never consumed by later hooks)
    assert not lumina._PENDING
    _seen(sid="abc")
    assert lumina._append("x", session_id="abc") == f"x\n\n{D}"  # stored AND retrieved with the same key
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


UU = "CADFE55A-D7D7-4E4D-998E-D9B4C9F3D68F"


def test_export_guard():
    g = lumina._guard
    for bad in (None, [], [UU] * 11, ["../etc/passwd"], [UU, 5], "x", [UU + "x"]):
        assert g("lumina_export", {"uuids": bad})["action"] == "block"
    assert g("lumina_export", {"uuids": [UU] * 10}) is None


def test_export_register_and_hint():
    c = Ctx()
    lumina.register(c)
    assert {"lumina_export", "lumina_on_this_day"} <= set(c.tools)
    send = "send me my beach photos"
    assert "lumina_export" in lumina._route(user_message=send, platform="telegram")["context"]
    assert "lumina_export" not in lumina._route(user_message=send, platform="desktop")["context"]
    assert "lumina_export" not in lumina._route(user_message=send, platform="cli")["context"]
    lumina._PENDING["s"] = "::lumina-photos{}"
    assert lumina._append("hi", "s", platform="telegram") is None  # chat apps cannot render the directive
    lumina._PENDING["s"] = "::lumina-photos{}"
    assert "::lumina-photos" in lumina._append("hi", "s", platform="desktop")


def test_export_output_shape(library, monkeypatch, tmp_path):
    from PIL import Image

    from lumina import photos

    monkeypatch.setenv("LUMINA_LIBRARY", library)
    monkeypatch.setattr(photos, "_cache_dir", tmp_path)
    db = photos.get_db()
    ps = [p for p in db.photos() if p.path and Path(p.path).exists()][:2]
    stale = tmp_path / "exports" / "old.jpg"
    stale.parent.mkdir()
    stale.write_bytes(b"x")
    import os
    os.utime(stale, (1, 1))
    out = json.loads(_call("lumina_export", {"uuids": [p.uuid for p in ps] + ["00000000-0000-0000-0000-000000000000"]}))
    assert out["exported"] == len(ps) == len(out["media"])
    assert out["skipped"] == [{"uuid": "00000000-0000-0000-0000-000000000000", "reason": "not_found"}]
    assert not stale.exists()
    for m, p in zip(out["media"], ps):
        assert m == f"MEDIA:{tmp_path / 'exports' / (p.uuid + '.jpg')}"
        with Image.open(m[6:]) as im:
            assert im.format == "JPEG" and max(im.size) <= 2048
    assert json.loads(_call("lumina_export", {"uuids": ["bad"]}))["error"] == "bad_args"
    assert str(tmp_path) not in _call("lumina_export", {"uuids": ["bad"]})


def _call(name, args):
    c = Ctx()
    lumina.register(c)
    return c.tools[name][1](args)


def test_on_this_day(library, monkeypatch):
    monkeypatch.setenv("LUMINA_LIBRARY", library)
    from lumina import photos

    db = photos.get_db()
    # Deterministic pick of a photo search can return (search skips hidden ones).
    p = sorted((p for p in db.photos() if p.date and not p.hidden), key=lambda p: p.uuid)[0]
    d = p.date.date().replace(year=p.date.year + 1) if not (p.date.month == 2 and p.date.day == 29) else None
    out = json.loads(_call("lumina_on_this_day", {"date": d.isoformat()}))
    assert p.uuid in [x["uuid"] for x in out["photos"]] or out["total_matches"] > 24
    assert out["display"].startswith("::lumina-photos{") and f"On this day · {d.day} " in out["display"]
    assert sum(out["by_year"].values()) == out["total_matches"] and str(p.date.year) in out["by_year"]
    # same-year photos never appear
    same = json.loads(_call("lumina_on_this_day", {"date": p.date.date().isoformat()}))
    assert p.uuid not in [x["uuid"] for x in same["photos"]]
    assert json.loads(_call("lumina_on_this_day", {"date": "nope"}))["error"] == "bad_args"


def test_read_text_register_guard_route():
    c = Ctx()
    lumina.register(c)
    assert "lumina_read_text" in c.tools
    g = lumina._guard
    for bad in (None, [], [UU] * 6, ["nope"], [UU, 5], "x"):
        assert g("lumina_read_text", {"uuids": bad})["action"] == "block"
    assert g("lumina_read_text", {"uuids": [UU] * 5}) is None and g("lumina_read_text", {"uuids": [UU]}) is None
    for q in ("When does my passport expire?", "what's my wifi password", "How much was the restaurant bill?"):
        assert "lumina_read_text" in lumina._route(user_message=q, session_id="s")["context"]
    for q in ("what is the capital of France", "what's the weather"):
        assert lumina._route(user_message=q, session_id="s") is None
    assert "lumina_read_text" not in lumina._route(user_message="Find my photos", session_id="s")["context"]


def test_read_text_against_library(library, monkeypatch):
    from lumina import photos

    monkeypatch.setenv("LUMINA_LIBRARY", library)
    p = sorted((p for p in photos.get_db().photos() if not p.hidden and not p.intrash), key=lambda p: p.uuid)[0]
    monkeypatch.setattr(photos, "ocr_text", lambda q: ["Expiry: 01 JAN 2034", "x" * 5000])
    raw = _call("lumina_read_text", {"uuids": [p.uuid, "00000000-0000-0000-0000-000000000000"]})
    out = json.loads(raw)
    [r] = out["photos"]
    assert r["uuid"] == p.uuid and r["text"].startswith("Expiry: 01 JAN 2034\n") and len(r["text"]) == 4000
    assert out["skipped"] == [{"uuid": "00000000-0000-0000-0000-000000000000", "reason": "not_found"}]
    assert out["display"] == f'::lumina-photos{{ids="{p.uuid}" title="{lumina._title({"text": p.title}) if p.title else "Text from photo"}"}}'
    assert "path" not in raw and library not in raw
    monkeypatch.setattr(photos, "ocr_text", lambda q: [])
    assert json.loads(_call("lumina_read_text", {"uuids": [p.uuid]}))["photos"][0]["text"] == ""
    assert json.loads(_call("lumina_read_text", {"uuids": ["bad"]}))["error"] == "bad_args"


def test_remember_stores_read_text_display():
    lumina._PENDING.clear()
    _seen()
    lumina._remember("lumina_read_text", json.dumps({"display": D.replace("A", "B")}), session_id="s")
    assert lumina._append("It expires 2034.", session_id="s") == f"It expires 2034.\n\n{D.replace('A', 'B')}"


def test_text_search_ignores_hyphens(library, monkeypatch):
    from lumina import photos

    monkeypatch.setenv("LUMINA_LIBRARY", library)
    monkeypatch.setattr(photos, "ocr_text", lambda q: ["WI-FI password: blue-mango"])
    monkeypatch.setattr(photos, "index_words", lambda p: [])  # old-macOS path: no Apple index, live recognition
    monkeypatch.setattr(photos, "_has_index", lambda db, ps: False)
    assert json.loads(_call("lumina_search", {"text": "wifi"}))["total_matches"] > 0
    assert json.loads(_call("lumina_search", {"text": "zz-no-such-text"}))["total_matches"] == 0


def test_expiry_register_guard_route_display(monkeypatch):
    from datetime import date
    c = Ctx()
    lumina.register(c)
    assert "lumina_expiry" in c.tools
    ctx = lumina._route(user_message="Is anything of mine expiring soon?", session_id="s")["context"]
    assert "lumina_expiry" in ctx
    assert "lumina_expiry" in lumina._route(user_message="when do my documents expire?", session_id="s")["context"]
    assert "lumina_read_text" in lumina._route(user_message="When does my passport expire?", session_id="s")["context"]
    assert lumina._route(user_message="what is the capital of France", session_id="s") is None
    monkeypatch.setattr(lumina.photos, "get_db", lambda: object())
    monkeypatch.setattr(lumina.photos, "expiry", lambda db, within_days=None, today=None: {
        "count": 1, "items": [{"uuid": "A" * 8}], "all_uuids": ["A" * 8], "_t": today.isoformat()})
    out = json.loads(c.tools["lumina_expiry"][1]({"today": "2026-11-08"}))
    assert 'title="Expiry radar"' in out["display"] and "all_uuids" not in out and out["_t"] == "2026-11-08"
    for bad in ({"within_days": 0}, {"within_days": "x"}, {"within_days": 4000}, {"today": "nope"}):
        assert json.loads(c.tools["lumina_expiry"][1](bad))["error"] == "bad_args"
    lumina._remember("lumina_expiry", json.dumps(out), session_id="z")
    assert lumina._PENDING["z"] == out["display"]


# ---- make memory ----
GOOD = "0A1B2C3D-0000-1111-2222-333344445555"


def test_memory_guard_and_route():
    g = lumina._guard
    assert g("lumina_make_memory", {"uuids": ["bad"]})["action"] == "block"
    assert g("lumina_make_memory", {"uuids": []})["action"] == "block"
    assert g("lumina_make_memory", {"uuids": [GOOD] * 201})["action"] == "block"
    assert g("lumina_make_memory", {"uuids": [GOOD]}) is None
    assert g("lumina_make_memory", {"place": "Goa"}) is None
    for m in ("Make a memory of my Goa trip", "make a video of my 2025 photos"):
        assert "lumina_make_memory" in lumina._route(m, session_id="s")["context"]
    assert "lumina_make_memory" not in (lumina._route("what's a good movie tonight", session_id="s") or {}).get("context", "")
    assert lumina._route("make a video call to mom", session_id="s") is None
    assert "lumina_make_memory" not in (lumina._route("show my on this day memories photos", session_id="s") or {}).get("context", "")


def test_memory_media_flow():
    lumina._PENDING_MEDIA.clear()
    M = "MEDIA:/x/a.mp4"
    rem = lambda sid="s": lumina._remember("lumina_make_memory", json.dumps({"media": M}), session_id=sid)  # noqa: E731
    for plat in ("telegram", "desktop"):
        rem()
        assert lumina._append("Enjoy!", session_id="s", platform=plat) == f"Enjoy!\n\n{M}"
        assert lumina._append("Enjoy!", session_id="s", platform=plat) is None
    rem()
    assert lumina._append(f"Hi\n{M}", session_id="s", platform="telegram") is None
    rem()
    lumina._route("thanks", session_id="s")
    assert lumina._append("x", session_id="s") is None
    for i in range(300):
        rem(f"s{i}")
    assert len(lumina._PENDING_MEDIA) == 200


def test_make_memory_render(library, monkeypatch, tmp_path):
    import shutil
    import subprocess

    from lumina import photos

    monkeypatch.setenv("LUMINA_LIBRARY", library)
    db = photos.get_db()
    ps = [p for p in db.photos() if photos._is_local(p) and not p.screenshot][:4]
    if not ps:
        pytest.skip("no local photos in test library")
    u = [p.uuid for p in ps]
    old = tmp_path / "memories" / "old.mp4"
    old.parent.mkdir()
    old.write_bytes(b"x")
    import os
    os.utime(old, (1, 1))
    out = json.loads(_call("lumina_make_memory", {"uuids": u, "title": "Test trip"}))
    assert not old.exists() and not list(tmp_path.glob("mem-*"))
    assert out["media"].startswith("MEDIA:") and Path(out["media"][6:]).exists() and out["photos"] >= 1
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        assert out["format"] == "mp4"
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                            "-of", "json", out["media"][6:]], capture_output=True, text=True)
        j = json.loads(r.stdout)
        assert float(j["format"]["duration"]) > 0 and (j["streams"][0]["width"], j["streams"][0]["height"]) == (1280, 720)
    monkeypatch.setattr(photos.shutil if hasattr(photos, "shutil") else __import__("shutil"), "which", lambda *_: None)
    g = json.loads(_call("lumina_make_memory", {"uuids": u}))
    assert g["format"] == "gif" and Path(g["media"][6:]).exists()
    bad = _call("lumina_make_memory", {"uuids": ["bad"]})
    assert json.loads(bad)["error"] == "bad_args" and str(tmp_path) not in bad
    nf = _call("lumina_make_memory", {"uuids": ["00000000-0000-0000-0000-000000000000"]})
    assert json.loads(nf)["error"] == "not_found" and str(tmp_path) not in nf


def test_read_text_unreadable_photo_skipped(library, monkeypatch):
    from lumina import photos

    monkeypatch.setenv("LUMINA_LIBRARY", library)
    a, b = sorted((p for p in photos.get_db().photos() if not p.hidden and not p.intrash), key=lambda p: p.uuid)[:2]

    def ocr(p):
        if p.uuid == a.uuid:
            raise OSError("boom")
        return ["ok"]
    monkeypatch.setattr(photos, "ocr_text", ocr)
    out = json.loads(_call("lumina_read_text", {"uuids": [a.uuid, b.uuid]}))
    assert [r["uuid"] for r in out["photos"]] == [b.uuid]
    assert out["skipped"] == [{"uuid": a.uuid, "reason": "unreadable"}]


def test_make_memory_failure_removes_partial(library, monkeypatch, tmp_path):
    import shutil

    from lumina import photos

    monkeypatch.setenv("LUMINA_LIBRARY", library)
    u = [p.uuid for p in photos.get_db().photos() if photos._is_local(p) and not p.screenshot][:2]
    if not u:
        pytest.skip("no local photos in test library")

    def enc(ff, frames, out):
        out.write_bytes(b"partial")
        return False
    monkeypatch.setattr(photos, "_encode_mp4", enc)
    monkeypatch.setattr(shutil, "which", lambda *_: "/bin/ffmpeg")
    assert json.loads(_call("lumina_make_memory", {"uuids": u}))["error"] == "render_failed"
    assert not list((tmp_path / "memories").glob("*.mp4"))


def test_safe_send_reply_shows_only_the_safe_copy():
    M = "MEDIA:/h/plugin-data/agent-plugin-lumina-x/redacted/abcdef012345.jpg"
    D = '::lumina-redact{id="abcdef012345"}'
    reply = f'Done.\n::lumina-photos{{ids="A" title="Gym"}}\nHere it is: {M[6:]}\n{M}\n{D}'
    for plat, want, gone in (("desktop", D, ["::lumina-photos", "MEDIA:", "/h/plugin-data"]),
                             ("telegram", M, ["::lumina-photos", "::lumina-redact", "Here it is: /h"])):
        lumina._PENDING.clear()
        lumina._PENDING_MEDIA.clear()
        lumina._remember("lumina_redact", json.dumps({"media": M, "display": D}), session_id="s")
        out = lumina._append(reply, session_id="s", platform=plat)
        assert out.count(want) == 1 and all(g not in out for g in gone), (plat, out)
    lumina._remember("lumina_redact", json.dumps({"media": M, "display": D}), session_id="s")
    assert lumina._append("Safe copy ready.", session_id="s", platform="desktop").endswith(D)


def test_safe_send_never_forwards_another_media_line():
    M = "MEDIA:/h/plugin-data/x/redacted/abcdef012345.jpg"
    lumina._PENDING_MEDIA.clear()
    lumina._remember("lumina_redact", json.dumps({"media": M, "display": '::lumina-redact{id="abcdef012345"}'}), session_id="s")
    out = lumina._append(f"MEDIA:/h/plugin-data/x/exports/ORIG.jpg\n{M}", session_id="s", platform="telegram")
    assert "ORIG" not in out and out.count(M) == 1
