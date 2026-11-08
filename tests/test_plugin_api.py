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
    assert c.get(f"{base}/thumbs", params={"uuids": ",".join(["AAAAAAAA"] * 61)}).status_code == 400
    assert c.get(f"{base}/thumbs", params={"uuids": "../x"}).status_code == 400
    assert c.get(f"{base}/thumbs", params={"uuids": "AAAAAAAA", "size": "xl"}).status_code == 400
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
