import importlib.util
import json
import shutil
from pathlib import Path

import pytest

import lumina
from lumina import photos, redact

FX = Path(__file__).parent / "fixtures"
UU = "CADFE55A-D7D7-4E4D-998E-D9B4C9F3D68F"
vision = pytest.mark.skipif(importlib.util.find_spec("Vision") is None, reason="needs macOS Vision")

PASSPORT = ["PASSPORT", "Type: P Country: UTO", "Surname: SAMPLE", "Given names: ALEX", "Passport No: X1234567",
            "Nationality: UTOPIAN", "Date of birth: 01 JAN 1990", "Expiry: 01 JAN 2034", "<UTOSAMPLE<<ALEX<<<<<<<<<<<", "<<<<<2"]


def kv(hits):
    return {(h.kind, h.value) for h in hits}


def test_classifier_passport():
    k = kv(redact.find_sensitive(PASSPORT, "all"))
    assert ("number", "X1234567") in k and ("dob", "01 JAN 1990") in k and ("name", "SAMPLE") in k
    assert {h.kind for h in redact.find_sensitive(PASSPORT, "hotel")} == {"number", "dob", "mrz"}
    assert {h.kind for h in redact.find_sensitive(PASSPORT, "airline")} == {"dob", "mrz"}


def test_classifier_cards_and_password():
    assert ("number", "GM-0042-7788") in kv(redact.find_sensitive(["MEMBER: ALEX SAMPLE", "ID: GM-0042-7788"], "hotel"))
    wifi = ["Network: SunnyDays_5G", "Wi-Fi password:", "blue-mango-4821"]
    assert ("password", "blue-mango-4821") in kv(redact.find_sensitive(wifi, "all"))
    assert not [h for h in redact.find_sensitive(wifi, "hotel")]
    assert ("contact", "a.b@x.com") in kv(redact.find_sensitive(["mail a.b@x.com"], "all"))


def test_classifier_ignores_receipts_and_flights():
    assert not redact.find_sensitive(["Eggs x12", "3.49", "25.63", "TOTAL: $27.68", "SUBTOTAL", "Milk 2L ..........."], "all")
    assert not redact.find_sensitive(["DATE: 22 JUN 2024 09:30", "PRICE: GBP 89.50", "+1 555 010 0199"], "hotel")
    k = {h.kind for h in redact.find_sensitive(["FLIGHT: LM 482", "SEAT: 14A", "BOARDING: 10:45", "DATE: 12 MAR 2025"], "hotel")}
    assert "number" not in k


@vision
@pytest.mark.parametrize("preset", ["hotel", "all"])
def test_end_to_end_passport(tmp_path, preset):
    from PIL import Image

    out = tmp_path / "o.jpg"
    r = redact._make(FX / "passport.jpg", out, preset)
    assert r["verified"] and r["remaining"] == 0 and r["found"] >= 3
    assert {"number", "dob", "mrz"} <= set(r["kinds"])
    im = Image.open(out)
    assert not im.getexif() and "exif" not in im.info
    src = Image.open(FX / "passport.jpg")
    assert im.height > min(src.height, 2048 * src.height / max(src.size))  # footer strip added
    text = " ".join(l.text for l in redact.ocr_boxes(str(out)))
    assert "Lumina verified" in text
    assert ("SAMPLE" in text.upper()) == (preset == "hotel")
    assert "X1234567" not in text and "1990" not in text
    assert "X1234567" not in json.dumps({k: v for k, v in r.items() if k != "lines"})


@vision
def test_redact_tool_outputs_no_values(tmp_path, monkeypatch):
    class P:
        uuid, intrash, ismovie = UU, False, False

    class DB:
        def get_photo(self, u):
            return P() if u == UU else None

    monkeypatch.setattr(photos, "_export_one", lambda p, out: shutil.copy(FX / "passport.jpg", out) and True)
    res = redact.redact(DB(), UU, "hotel")
    assert res["verified"] and res["media"].startswith("MEDIA:") and res["display"] == f'::lumina-redact{{id="{res["id"]}"}}'
    meta = (photos._data_dir() / "redacted" / f"{res['id']}.json").read_text()
    for v in ("X1234567", "1990", "SAMPLE<<"):
        assert v not in meta and v not in json.dumps(res)
    assert json.loads(meta)["uuid"] == UU and Path(res["media"][6:]).exists()
    assert [f.name for f in (photos._data_dir()).iterdir() if f.name.startswith("red-")] == []  # temp dirs cleaned
    with pytest.raises(photos.LuminaError) as e:
        redact.redact(DB(), "00000000-0000-0000-0000-000000000000", "hotel")
    assert e.value.code == "not_found"


@vision
def test_unverified_never_leaves_file(tmp_path, monkeypatch):
    monkeypatch.setattr(redact, "_blackout", lambda im, boxes: None)
    out = tmp_path / "o.jpg"
    with pytest.raises(photos.LuminaError) as e:
        redact._make(FX / "passport.jpg", out, "hotel")
    assert e.value.code == "redact_unverified" and not out.exists()
    assert not list(tmp_path.glob("*.jpg"))


@vision
def test_nothing_sensitive_is_not_sent(tmp_path):
    out = tmp_path / "o.jpg"
    with pytest.raises(photos.LuminaError) as e:
        redact._make(FX / "grocery-receipt.jpg", out, "all")
    assert e.value.code == "not_found" and not out.exists()


@vision
def test_other_fixtures_verify(tmp_path):
    for name, preset in (("gym-membership", "hotel"), ("vaccination-card", "hotel"), ("wifi-password", "all"), ("boarding-pass", "all")):
        r = redact._make(FX / f"{name}.jpg", tmp_path / f"{name}.jpg", preset)
        assert r["verified"] and r["remaining"] == 0


# ---- Guardian ----
class _P:
    def __init__(self, u):
        self.uuid = u


def _guard_env(monkeypatch, texts):
    monkeypatch.setattr(photos, "get_db", lambda: type("D", (), {"get_photo": lambda s, u: _P(u)})())
    monkeypatch.setattr(photos, "ocr_text", lambda p: texts[p.uuid] if not isinstance(texts[p.uuid], Exception) else (_ for _ in ()).throw(texts[p.uuid]))


def test_guardian(monkeypatch):
    R, L = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"
    _guard_env(monkeypatch, {UU: PASSPORT, R: ["FRESH MART GROCERY", "Eggs x12", "3.49", "TOTAL: $27.68"], L: ["Sunset", "Goa beach"]})
    b = lumina._guard("lumina_export", {"uuids": [R, UU]})
    assert b["action"] == "block" and f"uuid={UU}" in b["message"] and "Passport" in b["message"] and "X1234567" not in b["message"]
    assert lumina._guard("lumina_export", {"uuids": [R, L]}) is None
    assert lumina._guard("lumina_export", {"uuids": [UU], "allow_original": True}) is None


def test_guardian_fails_open(monkeypatch):
    _guard_env(monkeypatch, {UU: RuntimeError("vision down")})
    assert lumina._guard("lumina_export", {"uuids": [UU]}) is None


# ---- delivery ----
def test_delivery_per_platform():
    res = json.dumps({"media": "MEDIA:/x/a.jpg", "display": '::lumina-redact{id="abc"}'})
    lumina._remember("lumina_redact", res, session_id="s")
    out = lumina._append("Done.", session_id="s", platform="telegram")
    assert "MEDIA:/x/a.jpg" in out and "lumina-redact" not in out
    lumina._remember("lumina_redact", res, session_id="s")
    out = lumina._append("Done.", session_id="s", platform="desktop")
    assert '::lumina-redact{id="abc"}' in out and "MEDIA:" not in out
    lumina._remember("lumina_redact", res, session_id="s")
    assert lumina._append("Done.\n::lumina-redact{id=\"abc\"}", session_id="s", platform="cli") is None


def test_route_and_register():
    for q in ("send my passport to the hotel", "send my id but hide my number", "redact my licence", "blur the numbers on my card photo"):
        assert "lumina_redact" in lumina._route(user_message=q, session_id="s", platform="telegram")["context"], q
    assert "lumina_redact" not in lumina._route(user_message="Find my passport photo", session_id="s")["context"]
    c = type("C", (), {"tools": {}, "register_tool": lambda s, name, **k: s.tools.__setitem__(name, k), "register_hook": lambda *a, **k: None,
                       "register_skill": lambda *a, **k: None})()
    lumina.register(c)
    props = c.tools["lumina_redact"]["schema"]["parameters"]
    assert "required" not in props and "path" in props["properties"] and props["properties"]["preset"]["enum"] == ["hotel", "airline", "all"]
    assert "allow_original" in c.tools["lumina_export"]["schema"]["parameters"]["properties"]


def test_safe_name_never_uses_a_person_name():
    from lumina import redact
    assert redact.safe_name(["ALEX SAMPLE", "DOB: 01/02/1990", "ID: D1234-5678"]) == "Document"
    assert redact.safe_name(["IRONWORKS GYM", "MEMBER: ALEX SAMPLE"]) == "Gym"
    assert redact.safe_name(["PASSPORT", "Surname: SAMPLE"]) == "Passport"


def test_real_photo_layouts():
    """All-caps titles are not MRZ, dd-mm-yyyy is a date, DOB may precede its label, card numbers may be split."""
    from lumina import privacy
    lic = ["UTOPIA STATE MOTOR DRIVING LICENCE", "DOI: 01-02-2020", "DL No : XX00 20200000000", ": 01-01-1990", "DOB", "Address :"]
    hits = {(k, lic[i][a:b]) for i, k, a, b in redact.classify(lic)}
    assert not any(k == "mrz" for k, _ in hits)
    assert ("dob", "01-01-1990") in hits and ("number", "XX00 20200000000") in hits
    assert ("number", "01-02-2020") not in hits
    card = ["HDFC BANK", "4242", "4242", "4242", "4242", "YARU 08/29", "VISA"]
    assert sum(k == "number" for _, k, _, _ in redact.classify(card)) == 4
    assert privacy.kind_of(card) == "card"
