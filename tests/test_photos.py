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

    monkeypatch.setattr(photos, "photo_dict", lambda p: {"uuid": p.uuid})
    db = _DB(_P(2020, 11, 8), _P(2024, 11, 8), _P(2025, 11, 8), _P(2023, 11, 9), _P(2022, 12, 31), _P(2023, 1, 1), _P(2024, 2, 29))
    ids, by = _otd(db, "2025-11-08")  # same-year and other days excluded, newest year first
    assert ids == ["20241108", "20201108"] and by == {"2024": 1, "2020": 1}
    assert _otd(db, "2024-01-01")[0] == ["20230101"]  # year boundary: Dec 31 2022 is not Jan 1
    assert _otd(db, "2023-12-31")[0] == ["20221231"]
    assert _otd(db, "2028-02-29")[0] == ["20240229"]  # leap day matches itself
    assert _otd(db, "2025-02-28")[0] == ["20240229"]  # common year: leap-day photos show on Feb 28
    assert _otd(db, "2028-02-28")[0] == []  # leap year: they wait for the 29th
