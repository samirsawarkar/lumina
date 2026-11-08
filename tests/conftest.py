import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))  # import the plugin as package `lumina`
# osxphotos' MIT test libraries live outside the repo (sparse clone). Override with LUMINA_TEST_LIBRARY.
_CACHE = ROOT.parent / ".cache" / "osxphotos-repo" / "tests"


@pytest.fixture(scope="session")
def library():
    p = os.environ.get("LUMINA_TEST_LIBRARY") or str(_CACHE / "Test-13.0.0.photoslibrary")
    if not p or not Path(p).exists():
        pytest.skip("no osxphotos test library; set LUMINA_TEST_LIBRARY")
    return p


@pytest.fixture(scope="session")
def db(library):
    import osxphotos

    return osxphotos.PhotosDB(dbfile=library)


@pytest.fixture(autouse=True)
def _ocr_cache_dir(tmp_path, monkeypatch):
    from lumina import photos

    monkeypatch.setattr(photos, "_cache_dir", tmp_path)  # never touch the real plugin-data


@pytest.fixture(autouse=True)
def _no_personal_library(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMINA_LIBRARY", str(tmp_path / "none.photoslibrary"))  # never open the user's real Photos library
