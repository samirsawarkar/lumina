import json

import pytest

from lumina import photos


def test_search_requires_a_filter(db):
    with pytest.raises(photos.LuminaError) as e:
        photos.search(db)
    assert e.value.code == "bad_args"


def test_search_bad_input(db):
    for kw in ({"text": "x", "media": "gif"}, {"date_from": "nope"}, {"text": "x", "limit": "abc"}):
        with pytest.raises(photos.LuminaError) as e:
            photos.search(db, **kw)
        assert e.value.code == "bad_args"


def test_search_shape_order_and_no_paths(db):
    p0 = next(p for p in db.photos() if p.labels or p.place)
    term = p0.place.name.split(",")[0] if p0.place else p0.labels[0]
    r = photos.search(db, text=term, limit=3)
    assert r["count"] == len(r["photos"]) <= 3 and r["total_matches"] >= 1
    assert p0.uuid in {x["uuid"] for x in photos.search(db, text=term, limit=200)["photos"]}
    dates = [x["date"] for x in r["photos"]]
    assert dates == sorted(dates, reverse=True)
    assert set(r["photos"][0]) == {"uuid", "date", "title", "place", "persons", "labels", "text_snippet",
                                   "is_local", "is_screenshot", "is_video"}
    assert "path" not in json.dumps(r).lower() or "/" not in json.dumps(r["photos"][0])


def test_search_person_and_date(db):
    named = next(n for n in db.persons_as_dict if n != "_UNKNOWN_")
    assert photos.search(db, person=named)["total_matches"] >= 1
    assert photos.search(db, person=named, date_to="1900-01-01")["total_matches"] == 0


def test_library_info(db):
    i = photos.library_info(db)
    assert i["photos"] > 0 and isinstance(i["people"], list) and i["library_path_present"] is True


def test_create_album_validates():
    for name, ids in (("", ["A"]), ("x", []), ("x", ["A"] * 501), ("x", "A")):
        with pytest.raises(photos.LuminaError) as e:
            photos.create_album(name, ids)
        assert e.value.code == "bad_args"


def test_thumbnail_data_url(db):
    urls = [photos.thumb_data_url(p, "s") for p in db.photos()]
    assert any(u and u.startswith("data:image/jpeg;base64,") for u in urls)
    assert all(u is None or u.startswith("data:image/jpeg;base64,") for u in urls)

def test_errors_never_leak_paths(monkeypatch, tmp_path):
    assert "/Users" not in photos._classify(RuntimeError("bad /Users/x/Photos.sqlite")).message
    lib = tmp_path / "L.photoslibrary"
    lib.mkdir()
    monkeypatch.setenv("LUMINA_LIBRARY", str(lib))  # exists, but no database/Photos.sqlite
    with pytest.raises(photos.LuminaError) as e:
        photos.get_db()
    assert e.value.code == "no_library" and str(tmp_path) not in e.value.message

    import sys
    import types

    class Lib:
        def album_names(self):
            raise RuntimeError("boom /Users/x/secret")
    monkeypatch.setitem(sys.modules, "photoscript", types.SimpleNamespace(PhotosLibrary=Lib))
    with pytest.raises(photos.LuminaError) as e:
        photos.create_album("a", ["U"])
    assert "/Users" not in e.value.message


def test_search_odd_photos_and_detected_text(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, "_cache_dir", tmp_path)
    import datetime as dt
    from types import SimpleNamespace as NS

    def fake(uuid, **kw):
        d = dict(uuid=uuid, date=dt.datetime(2024, 1, 1), title=None, description=None, place=None,
                 persons=None, labels=None, keywords=None, hidden=False, intrash=False, ismovie=False,
                 screenshot=False, path=None, path_derivatives=None, detected_text=lambda: [])
        return NS(**{**d, **kw})

    passport = fake("P", detected_text=lambda: [("PASSPORT No 123", 0.9)])
    odd = fake("O")  # every optional field None
    db = NS(photos=lambda movies=True: [odd, passport])
    r = photos.search(db, text="passport")
    assert [x["uuid"] for x in r["photos"]] == ["P"] and r["photos"][0]["text_snippet"] == "PASSPORT No 123"
    assert photos.search(db, person="x", label="y")["total_matches"] == 0
    assert photos.photo_dict(odd)["labels"] == []


def test_ocr_cache(tmp_path):
    import datetime as dt
    from types import SimpleNamespace as NS

    photos.set_cache_dir(tmp_path)
    calls = []

    def ocr():
        calls.append(1)
        return [("PASSPORT", 0.9)]

    p = NS(uuid="A", ismovie=False, date_modified=dt.datetime(2024, 1, 1), fingerprint="f", detected_text=ocr)
    try:
        assert photos.ocr_text(p) == ["PASSPORT"] and photos.ocr_text(p) == ["PASSPORT"]
        assert len(calls) == 1  # second call served from sqlite
        p.date_modified = dt.datetime(2024, 2, 1)  # edited: cache invalidated
        photos.ocr_text(p)
        assert len(calls) == 2
        v = NS(uuid="V", ismovie=True, detected_text=ocr)
        assert photos.ocr_text(v) == [] and len(calls) == 2  # videos skipped
        (tmp_path / "ocr.sqlite").write_bytes(b"garbage")  # corrupt cache: log, still answer
        assert photos.ocr_text(p) == ["PASSPORT"]
    finally:
        photos.set_cache_dir(None)


class _P:
    def __init__(self, y, m, d):
        import datetime as dt
        self.uuid = f"{y}{m:02}{d:02}"
        self.date = dt.datetime(y, m, d, 12)
        self.hidden = self.intrash = self.ismovie = self.screenshot = False
        self.title = self.description = None
        self.persons = self.labels = self.keywords = []
        self.place = None


class _DB:
    def __init__(self, *ps):
        self.ps = ps

    def photos(self, movies=False):
        return list(self.ps)


def _otd(db, day):
    import datetime as dt
    from lumina import photos

    r = photos.search(db, on_day=dt.date.fromisoformat(day))
    return [p["uuid"] for p in r["photos"]], r["by_year"]


def test_on_this_day_matching(monkeypatch):
    from lumina import photos

    monkeypatch.setattr(photos, "photo_dict", lambda p, *a: {"uuid": p.uuid})
    db = _DB(_P(2020, 11, 8), _P(2024, 11, 8), _P(2025, 11, 8), _P(2023, 11, 9), _P(2022, 12, 31), _P(2023, 1, 1), _P(2024, 2, 29))
    ids, by = _otd(db, "2025-11-08")  # same-year and other days excluded, newest year first
    assert ids == ["20241108", "20201108"] and by == {"2024": 1, "2020": 1}
    assert _otd(db, "2024-01-01")[0] == ["20230101"]  # year boundary: Dec 31 2022 is not Jan 1
    assert _otd(db, "2023-12-31")[0] == ["20221231"]
    assert _otd(db, "2028-02-29")[0] == ["20240229"]  # leap day matches itself
    assert _otd(db, "2025-02-28")[0] == ["20240229"]  # common year: leap-day photos show on Feb 28
    assert _otd(db, "2028-02-28")[0] == []  # leap year: they wait for the 29th


# ---- expiry radar ----
from datetime import date


@pytest.mark.parametrize("lines,want", [
    (["Expiry: 01 JAN 2034"], date(2034, 1, 1)),
    (["Date of birth: 01 JAN 1990", "Expiry: 01 JAN 2034"], date(2034, 1, 1)),
    (["IRONWORKS GYM", "VALID THRU: 12/2026"], date(2026, 12, 31)),
    (["IRONWORKS GYM", "VALID THRU:", "12/2026"], date(2026, 12, 31)),
    (["LIBRARY CARD", "EXPIRES: 09/2027"], date(2027, 9, 30)),
    (["exp. date 1/34"], date(2034, 1, 31)),
    (["Expires Jan 1, 2034"], date(2034, 1, 1)),
    (["Expires 1 Jan 2034"], date(2034, 1, 1)),
    (["Expiration: 2034-01-01"], date(2034, 1, 1)),
    (["Expires January 2034"], date(2034, 1, 31)),
    (["Valid until 03/04/2030"], date(2030, 4, 3)),   # ambiguous -> day-first
    (["Valid until 04/13/2030"], date(2030, 4, 13)),  # 13 can only be the day -> MM/DD
    (["Valid thru 12/2026 or 01/2028"], date(2028, 1, 31)),
    (["Date of birth: 01 JAN 1990"], None),
    (["DATE: 12 MAR 2025"], None),
    (["Pay within 14 days"], None),
    (["Violation: Expired meter"], None),
    (["Vaccination", "Date: 15 FEB 2023"], None),
    ([], None),
    (["EXPIRY", "DATE OF BIRTH 01 JAN 1990"], None),
    (["EXPIRY DATES", "ISSUED 01/2020"], None),
    (["VALID THRU:", "12/2026"], date(2026, 12, 31)),
])
def test_parse_expiry(lines, want):
    assert photos.parse_expiry(lines) == want


def test_document_name():
    assert photos.document_name(["", "12", "IRONWORKS GYM", "x"]) == "Ironworks Gym"
    assert photos.document_name(["PASSPORT"]) == "Passport"
    assert len(photos.document_name(["A" * 90])) <= 40
    assert photos.document_name(["123"]) == "Document"


class _ExpP:
    def __init__(self, uuid, d, hidden=False, trash=False, movie=False):
        self.uuid, self.date, self.hidden, self.intrash, self.ismovie = uuid, d, hidden, trash, movie


class _ExpDB:
    def __init__(self, ps):
        self.ps = ps

    def photos(self, movies=True):
        return self.ps


def test_expiry_scan(monkeypatch):
    texts = {"A": ["PASSPORT", "Expiry: 01 JAN 2034"], "B": ["IRONWORKS GYM", "VALID THRU: 12/2026"],
             "C": ["Old card", "EXPIRES: 01/2026"], "D": ["Receipt", "Total 5"], "H": ["X", "Expiry: 01 JAN 2030"],
             "T": ["X", "Expiry: 01 JAN 2030"], "V": ["X", "Expiry: 01 JAN 2030"]}

    def fake(p):
        if p.uuid == "E":
            raise RuntimeError("ocr failed")
        return texts[p.uuid]
    monkeypatch.setattr(photos, "ocr_text", fake)
    db = _ExpDB([_ExpP("A", None), _ExpP("B", None), _ExpP("C", None), _ExpP("D", None), _ExpP("E", None),
              _ExpP("H", None, hidden=True), _ExpP("T", None, trash=True), _ExpP("V", None, movie=True)])
    r = photos.expiry(db, today=date(2026, 11, 8))
    assert [i["uuid"] for i in r["items"]] == ["C", "B", "A"] and r["all_uuids"] == ["C", "B", "A"] and r["count"] == 3
    assert [i["status"] for i in r["items"]] == ["expired", "soon", "ok"]
    assert r["items"][1] == {"uuid": "B", "document": "Ironworks Gym", "expires": "2026-12-31", "days_left": 53, "status": "soon"}
    assert r["items"][0]["days_left"] < 0
    r = photos.expiry(db, within_days=100, today=date(2026, 11, 8))
    assert [i["uuid"] for i in r["items"]] == ["C", "B"]
    assert "/" not in json.dumps(r["items"]).replace("\\/", "")


def test_pick_no_duplicate_favorite():
    from types import SimpleNamespace as NS

    ps = [NS(i=i, favorite=(i == 1)) for i in range(24)]
    got = photos._pick(ps, k=12)
    assert len({p.i for p in got}) == len(got) == 12
    ps = [NS(i=i, favorite=(i == 1)) for i in range(5)]
    got = photos._pick(ps, k=3)  # idx [0,2,4]: 1 is neighbour of both 0 and 2
    assert len({p.i for p in got}) == 3


# ---- Apple text index, bounded live fallback ----
def _ix(uuid, toks=None, days_old=30, **kw):
    import datetime as dt
    from types import SimpleNamespace as NS

    d = dict(uuid=uuid, date=dt.datetime(2024, 1, 1), date_added=dt.datetime.now() - dt.timedelta(days=days_old),
             title=None, description=None, place=None, persons=None, labels=None, keywords=None, hidden=False,
             intrash=False, ismovie=False, screenshot=False, path=None, path_derivatives=None, date_modified=None,
             fingerprint=None, search_info=NS(detected_text=toks) if toks is not None else None)
    return NS(**{**d, **kw})


def _live(monkeypatch, texts=None, snippets=False):
    calls = []
    if not snippets:  # result snippets also call ocr_text; keep them out of the match-time counts
        monkeypatch.setattr(photos, "_snippet", lambda p, indexed=False: None)

    def fake(p):
        calls.append(p.uuid)
        return (texts or {}).get(p.uuid, [])
    monkeypatch.setattr(photos, "ocr_text", fake)
    return calls


def _uu(db, **kw):
    return [x["uuid"] for x in photos.search(db, **kw)["photos"]]


def test_index_search_split_and_multiword(monkeypatch):
    from types import SimpleNamespace as NS

    calls = _live(monkeypatch)
    wifi = _ix("W", ["4821", "5g", "blue", "fi", "mango", "password", "wi"])
    pp = _ix("P", ["alex", "passport", "x1234567"])
    none = _ix("N", [])
    db = NS(photos=lambda movies=True: [wifi, pp, none])
    assert _uu(db, text="wifi") == ["W"] and _uu(db, text="Wi-Fi") == ["W"]
    assert _uu(db, text="blue password") == ["W"] and _uu(db, text="blue passport") == []
    assert set(_uu(db, text="pass")) == {"W", "P"}
    assert calls == []  # indexed library, none of the misses is recent: no live recognition
    assert photos.search(db, text="wifi").get("text_scan") is None


def test_search_budget_partial_and_cache_resumes(monkeypatch):
    from types import SimpleNamespace as NS

    # no Apple index at all: live, newest first, until the budget runs out
    old = _ix("O", [], date=__import__("datetime").datetime(2020, 1, 1))
    new = _ix("A", [], date=__import__("datetime").datetime(2024, 1, 1))
    db = NS(photos=lambda movies=True: [old, new])
    calls = _live(monkeypatch, {"A": ["PASSPORT"], "O": ["PASSPORT"]})
    monkeypatch.setattr(photos, "SEARCH_BUDGET", -1)
    r = photos.search(db, text="passport")
    assert r["text_scan"] == "partial" and r["total_matches"] == 0 and "part of the library" in r["note"] and calls == []
    monkeypatch.setattr(photos, "SEARCH_BUDGET", 6.0)
    r = photos.search(db, text="passport")
    assert calls == ["A", "O"] and "text_scan" not in r and r["total_matches"] == 2


def test_search_cache_used_without_recognition(monkeypatch, tmp_path):
    import json as j
    import sqlite3
    from types import SimpleNamespace as NS

    c = sqlite3.connect(tmp_path / "ocr.sqlite")
    c.execute("CREATE TABLE ocr (uuid TEXT PRIMARY KEY, sig TEXT, text TEXT)")
    c.execute("INSERT INTO ocr VALUES ('C', '|', ?)", (j.dumps(["Gate 12"]),))
    c.commit()
    c.close()
    calls = _live(monkeypatch)
    p = _ix("C", ["x"])
    assert _uu(NS(photos=lambda movies=True: [p]), text="gate") == ["C"] and calls == []


def test_recent_untokenised_photo_read_live_in_indexed_library(monkeypatch):
    from types import SimpleNamespace as NS

    calls = _live(monkeypatch, {"R": ["gate 12"]})
    db = NS(photos=lambda movies=True: [_ix("I", ["a"]), _ix("R", [], days_old=1), _ix("S", [], days_old=40)])
    assert _uu(db, text="gate") == ["R"] and calls == ["R"]


def test_snippet_skips_recognition_for_untokenised_in_indexed_library(monkeypatch):
    from types import SimpleNamespace as NS

    calls = _live(monkeypatch, snippets=True)
    ps = [_ix(f"U{i}", [] if i else ["z"]) for i in range(5)]
    db = NS(photos=lambda movies=True: ps)
    photos.search(db, date_from="2020-01-01")
    assert calls == ["U0"]  # only the photo with Apple tokens


def test_derivative_fallback(monkeypatch, tmp_path):
    from types import SimpleNamespace as NS

    from lumina import redact

    small, big = tmp_path / "s.jpg", tmp_path / "b.jpg"
    small.write_bytes(b"1")
    big.write_bytes(b"12345")
    seen = []
    monkeypatch.setattr(redact, "ocr_boxes", lambda path: seen.append(path) or [NS(text="Hello")])
    p = _ix("D", None, path=None, path_derivatives=[str(small), str(big), str(tmp_path / "gone.jpg")],
            detected_text=lambda: [("nope", 1)])
    assert photos.ocr_text(p) == ["Hello"] and seen == [str(big)]
    p.path = str(big)  # original on disk: Apple's own call
    p.uuid = "D2"
    assert photos.ocr_text(p) == ["nope"]


def test_expiry_prefilter_and_unindexed_budget(monkeypatch):
    texts = {"A": ["PASSPORT", "Expiry: 01 JAN 2034"], "B": ["GYM", "VALID THRU: 12/2026"], "C": ["Card", "EXPIRES: 01/2026"],
             "D": ["Receipt", "Total 5"], "U": ["X", "Use by: 01 JAN 2030"]}
    calls = _live(monkeypatch, texts)
    from types import SimpleNamespace as NS

    ps = [_ix("A", ["expiry", "passport"]), _ix("B", ["thru", "valid"]), _ix("C", ["expires"]),
          _ix("D", ["total"]), _ix("U", ["by", "use"]), _ix("X", [], days_old=90)]
    db = NS(photos=lambda movies=True: ps)
    r = photos.expiry(db, today=date(2026, 11, 8))
    assert [i["uuid"] for i in r["items"]] == ["C", "B", "U", "A"] and sorted(calls) == ["A", "B", "C", "U"]  # D, X never read
    assert "partial" not in r
    calls.clear()
    monkeypatch.setattr(photos, "index_words", lambda p: [])  # no Apple index: everything read, budget applies
    assert len(photos.expiry(db, today=date(2026, 11, 8))["items"]) == 4 and len(calls) == 6
    monkeypatch.setattr(photos, "EXPIRY_BUDGET", -1)
    r = photos.expiry(db, today=date(2026, 11, 8))
    assert r["partial"] is True and r["count"] == 0


def test_in_index_ignores_single_letter_tokens():
    assert photos._in_index(["wifi"], ["w", "i", "f", "i", "x"]) is False
    assert photos._in_index(["wifi"], ["wi", "fi"]) is True


def test_expiry_hint_is_tight():
    h = photos._exp_hint
    for ok in (["expiry"], ["expires"], ["expiration"], ["exp."], ["exp"], ["valid", "thru"], ["valid", "to"], ["use", "by"],
               ["renew", "by"], ["good", "thru"]):
        assert h(ok), ok
    for no in (["experience"], ["express"], ["explore"], ["export"], ["valid"], ["use"], ["good"], ["by"], ["thru"], ["use", "good"]):
        assert not h(no), no
