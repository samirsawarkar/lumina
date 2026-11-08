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
    # only photos with a local file can yield a thumbnail; fixtures are tiny so this may be None-only
    urls = [photos.thumb_data_url(p, "s") for p in db.photos()]
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
