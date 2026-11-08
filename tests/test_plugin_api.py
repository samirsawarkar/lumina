import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

API = Path(__file__).resolve().parents[1] / "dashboard" / "plugin_api.py"


def client(monkeypatch, library):
    monkeypatch.setenv("LUMINA_LIBRARY", library)
    spec = importlib.util.spec_from_file_location("lumina_api_under_test", API)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    app = FastAPI()
    app.include_router(m.router, prefix="/api/plugins/lumina")
    return TestClient(app), m


def test_status_and_thumbs(monkeypatch, library):
    c, m = client(monkeypatch, library)
    s = c.get("/api/plugins/lumina/status").json()
    assert s["access"] == "ok" and s["photos"] > 0
    u = m.photos.get_db().photos()[0].uuid
    items = c.get("/api/plugins/lumina/thumbs", params={"uuids": f"{u},00000000-0000-0000-0000-000000000000", "size": "m"}).json()["items"]
    assert [i["uuid"] for i in items][0] == u and items[1]["data_url"] is None
    assert items[0]["data_url"] is None or items[0]["data_url"].startswith("data:image/jpeg;base64,")


def test_thumbs_validation(monkeypatch, library):
    c, _ = client(monkeypatch, library)
    base = "/api/plugins/lumina"
    assert c.get(f"{base}/thumbs", params={"uuids": ""}).status_code == 400
    assert c.get(f"{base}/thumbs", params={"uuids": ",".join(["00000000-0000-0000-0000-000000000000"] * 61)}).status_code == 400
    assert c.get(f"{base}/thumbs", params={"uuids": "--------"}).status_code == 400
    assert c.get(f"{base}/thumbs", params={"uuids": "../x"}).status_code == 400
    assert c.get(f"{base}/thumbs", params={"uuids": "00000000-0000-0000-0000-000000000000", "size": "xl"}).status_code == 400
    assert c.get(f"{base}/open", params={"uuid": "bad id"}).status_code == 400


def test_status_no_library(monkeypatch, tmp_path):
    c, _ = client(monkeypatch, str(tmp_path / "missing.photoslibrary"))
    s = c.get("/api/plugins/lumina/status").json()
    assert s["access"] == "error" and s["error"] == "no_library"


def test_status_no_access(monkeypatch, library):
    c, m = client(monkeypatch, library)

    def boom():
        raise m.photos.LuminaError("no_access", "x")
    monkeypatch.setattr(m.photos, "get_db", boom)
    assert c.get("/api/plugins/lumina/status").json()["access"] == "no_access"


def test_redact_endpoint(monkeypatch, library, tmp_path):
    import json

    c, m = client(monkeypatch, library)
    monkeypatch.setattr(m.photos, "_cache_dir", tmp_path)  # the API loads its own photos module
    d = m.photos._data_dir() / "redacted"
    d.mkdir()
    from PIL import Image

    Image.new("RGB", (2000, 1000), "black").save(d / "abcdef012345.jpg")
    meta = {"id": "abcdef012345", "uuid": "00000000-0000-0000-0000-000000000000", "document": "Passport", "preset": "hotel",
            "found": 3, "remaining": 0, "verified": True, "kinds": ["dob"], "created": "2026-01-01T00:00:00"}
    (d / "abcdef012345.json").write_text(json.dumps(meta))
    monkeypatch.setattr(m.photos, "thumb_data_url", lambda p, s: "data:image/jpeg;base64,AAAA")
    monkeypatch.setattr(m.photos, "get_db", lambda: type("D", (), {"get_photo": lambda s, u: object()})())
    base = "/api/plugins/lumina/redact"
    for bad in ("", "ABCDEF012345", "abc", "../../etc/passwd", "abcdef01234g"):
        assert c.get(base, params={"id": bad}).status_code == 400
    assert c.get(base, params={"id": "000000000000"}).status_code == 404
    assert c.get(base, params={"id": "000000000000"}).json() == {"error": "not_found"}
    r = c.get(base, params={"id": "abcdef012345"})
    assert r.status_code == 200
    j = r.json()
    assert j["before"] == "data:image/jpeg;base64,AAAA" and j["after"].startswith("data:image/jpeg;base64,")
    assert {k: j[k] for k in meta if k != "created"} == {k: v for k, v in meta.items() if k != "created"}
    assert set(j) == {"id", "uuid", "document", "preset", "found", "remaining", "verified", "kinds", "before", "after"}
