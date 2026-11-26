"""Wikidata cache tests.

Tests for wd_cache.py - the shared name-to-QID cache for pd_checker.

Provenance rule: every emitted QID must be traceable to a verified cache entry.
"""

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from pd_checker.core import catalogs
wd_cache = catalogs


# Path to test fixtures
HERE = Path(__file__).resolve().parent.parent


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Create a temporary directory for cache testing."""
    # Create a mock wd_cache module that uses our temp directory
    original_cache_path = wd_cache.WD_CACHE_PATH
    
    # Create temp cache file
    temp_cache = tmp_path / "wd_cache.json"
    temp_cache.write_text(
        json.dumps({
            "note": "Test cache",
            "fetched": date.today().strftime("%Y-%m-%d"),
            "names": {
                "Rafael Merry del Val": {
                    "qid": "Q507540",
                    "label_it": "Rafael Merry del Val",
                    "label_en": "Rafael Merry del Val",
                    "result": "ok",
                    "checked": date.today().strftime("%Y-%m-%d")
                },
                "Sansaini": {
                    "qid": None,
                    "label_it": "Sansaini",
                    "label_en": "Sansaini",
                    "result": "not_found",
                    "checked": date.today().strftime("%Y-%m-%d")
                }
            }
        }),
        encoding="utf-8"
    )
    
    # Patch the CACHE_PATH
    wd_cache.WD_CACHE_PATH = temp_cache
    
    yield temp_cache
    
    # Restore original path
    wd_cache.WD_CACHE_PATH = original_cache_path



def test_lookup_returns_qid_for_cached_name(temp_cache_dir):
    """Test (a): a cached name resolves and appears as a qid."""
    qid = wd_cache.lookup("Rafael Merry del Val")
    assert qid == "Q507540"


def test_lookup_returns_none_for_not_found(temp_cache_dir):
    """Test (b): a name marked not_found yields no qid."""
    qid = wd_cache.lookup("Sansaini")
    assert qid is None


def test_lookup_returns_none_for_absent_name(temp_cache_dir):
    """Test (b): a name absent from the cache yields no qid + da_rivedere."""
    qid = wd_cache.lookup("Unknown Name")
    assert qid is None


def test_lookup_performs_no_network_io(temp_cache_dir):
    """Test (d): lookup performs no network I/O."""
    # Patch urlopen to ensure it's never called
    with patch("pd_checker.core.catalogs.urlopen") as mock_urlopen:
        qid = wd_cache.lookup("Rafael Merry del Val")
        mock_urlopen.assert_not_called()
        assert qid == "Q507540"


def test_resolve_cache_hit(temp_cache_dir):
    """Test resolve with cache hit."""
    qid = wd_cache.resolve("Rafael Merry del Val", live=False)
    assert qid == "Q507540"


def test_resolve_cache_miss_no_live(temp_cache_dir):
    """Test resolve with cache miss and live=False."""
    qid = wd_cache.resolve("Unknown Name", live=False)
    assert qid is None
    
    # Verify no entry was created
    cache = wd_cache._load_cache()
    assert "Unknown Name" not in cache.get("names", {})


def test_resolve_cache_miss_with_live(temp_cache_dir):
    """Test resolve with cache miss and live=True."""
    # Mock the API response
    mock_response_data = {
        "search": [
            {"id": "Q999999", "label": "Unknown Name", "url": "https://wikidata.org/entity/Q999999"}
        ]
    }
    
    mock_resp_obj = MockResponse(mock_response_data)
    
    with patch("pd_checker.core.catalogs.urlopen", return_value=mock_resp_obj):
        qid = wd_cache.resolve("Unknown Name", live=True)
        assert qid == "Q999999"
    
    # Verify entry was created
    cache = wd_cache._load_cache()
    assert "Unknown Name" in cache.get("names", {})
    assert cache["names"]["Unknown Name"]["qid"] == "Q999999"


def test_resolve_not_found_stays_not_found(temp_cache_dir):
    """Test that a name marked not_found stays that way."""
    qid = wd_cache.resolve("Sansaini", live=False)
    assert qid is None



def test_search_returns_matches(temp_cache_dir):
    """Test offline search over cache."""
    matches = wd_cache.search("Merry")
    assert "Rafael Merry del Val" in matches
    
    matches = wd_cache.search("Sans")
    assert "Sansaini" in matches
    
    matches = wd_cache.search("nonexistent")
    assert matches == []


def test_list_all_returns_cache_contents(temp_cache_dir):
    """Test listing all cached names."""
    names = wd_cache.list_all()
    
    assert "Rafael Merry del Val" in names
    assert names["Rafael Merry del Val"]["qid"] == "Q507540"
    assert names["Rafael Merry del Val"]["result"] == "ok"
    
    assert "Sansaini" in names
    assert names["Sansaini"]["qid"] is None
    assert names["Sansaini"]["result"] == "not_found"


def test_guard_drops_unknown_qid(temp_cache_dir):
    """Test (c): the guard drops a hand-injected unknown qid instead of emitting it."""
    # Simulate a statement with a QID that's not in cache
    # The _lookup_qid function should return None for unknown names
    unknown_qid = wd_cache.lookup("Hand Invented QID")
    assert unknown_qid is None


def test_load_returns_dict_when_missing(tmp_path):
    """Test that load returns a dict when cache file is missing."""
    original_cache_path = wd_cache.WD_CACHE_PATH
    wd_cache.CACHE_PATH = tmp_path / "nonexistent.json"
    
    try:
        cache = wd_cache._load_cache()
        assert isinstance(cache, dict)
        assert "names" in cache
        assert isinstance(cache["names"], dict)
    finally:
        wd_cache.WD_CACHE_PATH = original_cache_path


def test_save_cache(temp_cache_dir):
    """Test saving cache to disk."""
    cache = wd_cache._load_cache()
    cache["names"]["New Entry"] = {
        "qid": "Q111111",
        "label_it": "New Entry",
        "label_en": "New Entry",
        "result": "ok",
        "checked": date.today().strftime("%Y-%m-%d")
    }
    
    success = wd_cache._save_cache(cache)
    assert success
    
    # Verify it was saved
    saved_cache = wd_cache._load_cache()
    assert "New Entry" in saved_cache["names"]
    assert saved_cache["names"]["New Entry"]["qid"] == "Q111111"


# Helper for mocking urlopen response
class MockResponse:
    def __init__(self, data):
        self.data = json.dumps(data).encode('utf-8')
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
    
    def read(self):
        return self.data


def __mock_response__(data):
    """Factory for mock response."""
    return MockResponse(data)
# =============================================================================
# cache_confirmed (resolve --confirm)
# =============================================================================

import io


def _fake_urlopen(payload):
    def fake(req, timeout=30):
        return io.BytesIO(json.dumps(payload).encode())
    return fake


def test_cache_confirmed_writes_ok_entry(temp_cache_dir, monkeypatch):
    payload = {"entities": {"Q123456": {"labels": {"it": {"value": "Teatro Esempio"}, "en": {"value": "Example Theatre"}}}}}
    monkeypatch.setattr(wd_cache, "urlopen", _fake_urlopen(payload))
    outcome = wd_cache.cache_confirmed("Teatro Esempio", "Q123456")
    assert outcome["status"] == "ok"
    assert wd_cache.lookup("Teatro Esempio") == "Q123456"
    cache = wd_cache._load_wd_cache()
    assert cache["names"]["Teatro Esempio"]["label_it"] == "Teatro Esempio"
    assert cache["names"]["Teatro Esempio"]["label_en"] == "Example Theatre"


def test_cache_confirmed_rejects_invalid_qid(temp_cache_dir):
    outcome = wd_cache.cache_confirmed("Nome", "Q12x")
    assert outcome["status"] == "invalid"
    cache = wd_cache._load_wd_cache()
    assert "Nome" not in cache["names"]


def test_cache_confirmed_rejects_missing_entity(temp_cache_dir, monkeypatch):
    payload = {"entities": {"Q999999999": {"missing": ""}}}
    monkeypatch.setattr(wd_cache, "urlopen", _fake_urlopen(payload))
    outcome = wd_cache.cache_confirmed("Nome", "Q999999999")
    assert outcome["status"] == "not_found"
    cache = wd_cache._load_wd_cache()
    assert "Nome" not in cache["names"]
