import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import lumina
from lumina import photos, privacy, redact

FX = Path(__file__).parent / "fixtures"
vision = pytest.mark.skipif(importlib.util.find_spec("Vision") is None, reason="needs macOS Vision")
PASSPORT = ["PASSPORT", "Type: P Country: UTO", "Surname: SAMPLE", "Given names: ALEX", "Passport No: X1234567",
            "Date of birth: 01 JAN 1990", "Expiry: 01 JAN 2034", "<UTOSAMPLE<<ALEX<<<<<<<<<<<", "<<<<<2"]
WIFI = ["Network: SunnyDays_5G", "Wi-Fi password:", "blue-mango-4821"]
GYM = ["IRONWORKS GYM", "MEMBER: ALEX SAMPLE", "ID: GM-0042-7788", "VALID THRU: 12/2026"]
RECEIPT = ["Eggs x12", "3.49", "TOTAL: $27.68", "Milk 2L"]
CARD = ["BANK OF NOWHERE", "4111 1111 1111 1111", "VALID THRU 12/29"]
BAD_CARD = ["Order 4111 1111 1111 1112"]
LICENCE = ["DRIVING LICENCE", "Licence No: D1234567", "Date of birth: 01 JAN 1990"]


def test_kinds():
    k = privacy.kind_of
    assert k(PASSPORT) == "passport" and k(["PASSPORT", "Surname: SAMPLE", "Nationality: UTOPIAN"]) == "passport"
    assert k(WIFI) == "password" and k(CARD) == "card" and k(LICENCE) == "id"
    assert k(GYM) is None and k(RECEIPT) is None and k([]) is None and k(BAD_CARD) is None
    assert k(["Passport photos 4 for $8"]) is None  # the word alone is not enough
    assert k(["Your passport", "Booking ref: AB12CD", "Passport No: X1234567"]) is None  # travel email
    assert k(["IMEI 356938035643809", "valid thru 12/29"]) is None  # Luhn-valid 15 digits starting 35
    assert k(["SIM 8944 5012 3456 7890 123"]) is None  # 19-20 digit SIM number
    assert k(["4111 1111 1111 1111"]) is None  # no card word or MM/YY
    assert k(["Card 4111 1111 1111 1111", "12/29"]) == "card" and k(["AMEX 3782 822463 10005"]) == "card"
    assert k(["Welcome back", "Password", "Continue"]) is None  # login screen
    assert k(["Password:", "hunter"]) is None and k(["Wi-Fi password:", "blue-mango"]) == "password"
    assert k(["License key", "Licence No: AB-1234-CD-5678"]) is None  # software licence key
    assert k(["PARKING PERMIT", "Permit No: 55120"]) is None
    assert k(["DRIVING LICENCE", "Surname: SAMPLE", "Class: B"]) == "id"


def _p(uuid, toks, days=30, **kw):
    import datetime as dt

    return NS(**{**dict(uuid=uuid, date=dt.datetime(2024, 1, 1), date_added=dt.datetime.now() - dt.timedelta(days=days), hidden=False,
                        intrash=False, ismovie=False, search_info=None if toks is None else NS(detected_text=toks, id_document_types=[])), **kw})


def test_privacy_check_two_stage_no_values(monkeypatch):
    texts = {"P": PASSPORT, "W": WIFI, "C": CARD, "G": GYM, "R": RECEIPT, "N": ["sky"]}
    read = []
    monkeypatch.setattr(photos, "ocr_text", lambda p: read.append(p.uuid) or texts[p.uuid])
    ps = [_p("P", ["passport", "alex"]), _p("W", ["wi", "fi", "blue"]), _p("C", ["credit", "4111"]), _p("G", ["gym", "member"]),
          _p("R", ["total"]), _p("N", []), _p("H", ["passport"], hidden=True), _p("T", ["passport"], intrash=True),
          _p("M", ["passport"], ismovie=True)]
    r = privacy.privacy_check(NS(photos=lambda movies=True: ps))
    assert sorted(read) == ["C", "P", "W"]  # stage 1 only lets hinted, visible photos through
    assert {i["uuid"]: i["kind"] for i in r["items"]} == {"P": "passport", "W": "password", "C": "card"}
    assert r["count"] == 3 and r["scanned"] == 6 and r["partial"] is False and set(r["all_uuids"]) == {"P", "W", "C"}
    assert [g["kind"] for g in r["groups"]] == ["passport", "card", "password"] and all(g["count"] == 1 for g in r["groups"])
    assert set(r["items"][0]) == {"uuid", "kind", "document", "date"}
    blob = json.dumps(r)
    for secret in ("X1234567", "blue-mango", "4821", "4111", "SAMPLE", "ALEX", "1990", "SunnyDays"):
        assert secret not in blob


def test_privacy_check_limits(monkeypatch):
    monkeypatch.setattr(photos, "ocr_text", lambda p: WIFI)
    monkeypatch.setattr(privacy, "MAX_CANDIDATES", 2)
    ps = [_p(f"U{i}", ["password"]) for i in range(5)]
    r = privacy.privacy_check(NS(photos=lambda movies=True: ps))
    assert r["partial"] is True and r["count"] == 2 and "2 of 5" in r["note"]
    monkeypatch.setattr(privacy, "MAX_CANDIDATES", 150)
    monkeypatch.setattr(privacy, "BUDGET", -1)
    assert privacy.privacy_check(NS(photos=lambda movies=True: ps))["count"] == 0
    # no Apple index at all: newest photos are read, bounded, and marked partial
    monkeypatch.setattr(privacy, "BUDGET", 20.0)
    r = privacy.privacy_check(NS(photos=lambda movies=True: [_p("A", None), _p("B", None)]))
    assert r["count"] == 2 and r["scanned"] == 2 and r["partial"] is False


def test_progress_across_runs(monkeypatch):
    monkeypatch.setattr(photos, "_recognise", lambda p: WIFI)
    monkeypatch.setattr(privacy, "MAX_CANDIDATES", 120)
    ps = [_p(f"U{i}", ["password"], date_modified=None, fingerprint=None) for i in range(200)]
    db = NS(photos=lambda movies=True: ps)
    r1 = privacy.privacy_check(db)
    assert r1["partial"] is True and r1["checked"] == 120 and r1["candidates"] == 200 and "120 of 200" in r1["note"]
    r2 = privacy.privacy_check(db)  # the first 120 come from the cache for free
    assert r2["partial"] is False and r2["checked"] == 200 and r2["count"] == 200 and "note" not in r2


@vision
@pytest.mark.parametrize("name,want", [("passport", "passport"), ("wifi-password", "password"), ("gym-membership", None),
                                       ("grocery-receipt", None), ("boarding-pass", None), ("vaccination-card", None)])
def test_fixtures_on_real_ocr(name, want):
    assert privacy.kind_of([l.text for l in redact.ocr_boxes(FX / f"{name}.jpg")]) == want


class Ctx:
    def __init__(self):
        self.tools = {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (schema, handler)

    def register_hook(self, *a, **kw):
        pass

    def register_skill(self, *a, **kw):
        pass


def test_tool_display_and_route(monkeypatch):
    c = Ctx()
    lumina.register(c)
    assert c.tools["lumina_privacy_check"][0]["parameters"].get("required") is None
    monkeypatch.setattr(lumina.photos, "get_db", lambda: object())
    u = "A" * 8
    monkeypatch.setattr(privacy, "privacy_check", lambda db: {"scanned": 1, "count": 1, "groups": [], "partial": False,
                                                             "items": [{"uuid": u}], "all_uuids": [u]})
    out = json.loads(c.tools["lumina_privacy_check"][1]({}))
    assert 'title="Sensitive documents"' in out["display"] and out["all_uuids"] == [u]
    lumina._remember("lumina_privacy_check", json.dumps(out), session_id="z")
    assert lumina._PENDING["z"] == out["display"]
    for q in ("Do a privacy check on my photos", "what sensitive documents are in my photos?", "Are my private documents exposed?",
              "give me a photo check-up", "which photos are at risk", "anything that could leak?"):
        assert "lumina_privacy_check" in lumina._route(user_message=q, session_id="s")["context"], q
    h = lumina._route(user_message="privacy check", session_id="s")["context"]
    assert "lumina_redact" in h and "lumina_create_album" in h and "all_uuids" in h and "never print" in h
    # existing hints keep their messages
    assert "lumina_redact" in lumina._route(user_message="hide my passport number before I send it", session_id="s")["context"]
    assert "lumina_expiry" in lumina._route(user_message="is anything of mine expiring?", session_id="s")["context"]
    assert "lumina_privacy_check" not in lumina._route(user_message="Find my passport photo", session_id="s")["context"]
