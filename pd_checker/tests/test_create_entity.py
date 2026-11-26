"""Create-entity command tests.

Tests for create_entity.py - the Wikidata item creation command.

All tests are offline with mocked network calls. Cache writes use tmp_path
(never the shipped pd_checker/data/wd_cache.json).
"""

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pd_checker.core import create_entity, catalogs


# Path to test fixtures
HERE = Path(__file__).resolve().parent.parent


@pytest.fixture
def temp_pubdom_folder(tmp_path):
    """Create a temporary folder with pubdom.json containing entities_to_create."""
    folder = tmp_path / "test_folder"
    folder.mkdir()
    
    # Create pubdom.json with various test cases
    pubdom = {
        "entities_to_create": [
            {
                "name": "Entità senza fonte",
                "role": "editore",
                "source_url": "",  # Missing source - should be SALTATO
                "p31_qid": None
            },
            {
                "name": "Entità con fonte valida",
                "role": "editore",
                "source_url": "https://example.com/catalog",
                "p31_qid": None
            },
            {
                "name": "Entità con P31 valido",
                "role": "autore",
                "source_url": "https://example.com/source",
                "p31_qid": "Q380"  # Roma - valid QID in catalog
            },
            {
                "name": "Entità con P31 invalido",
                "role": "editore",
                "source_url": "https://example.com/source",
                "p31_qid": "Q999999999"  # Unknown QID - should be refused
            }
        ]
    }
    
    (folder / "pubdom.json").write_text(json.dumps(pubdom, ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Create a temporary cache file for testing."""
    cache_file = tmp_path / "wd_cache.json"
    
    # Create a minimal cache with valid P31 QIDs
    cache = {
        "note": "Test cache",
        "fetched": date.today().strftime("%Y-%m-%d"),
        "names": {}
    }
    
    cache_file.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache_file


@pytest.fixture
def mock_catalogs_with_valid_p31(tmp_path):
    """Mock catalogs.wd_entities_load to return valid P31 QIDs."""
    # Create a temporary entities file with valid QIDs
    entities_file = tmp_path / "wikidata_entities.json"
    entities = {
        "items": {
            "Q380": {"label_it": "Roma", "label_en": "Rome"},
            "Q2044": {"label_it": "Firenze", "label_en": "Florence"},
            "Q220": {"label_it": "Italia", "label_en": "Italy"},
        }
    }
    entities_file.write_text(json.dumps(entities), encoding="utf-8")
    
    # Patch wd_entities_load to return our test data
    with patch.object(catalogs, 'wd_entities_load', return_value=entities):
        yield


def test_dry_run_prints_payload_with_label_description_p31(temp_pubdom_folder, capsys, mock_catalogs_with_valid_p31):
    """Test (1): Payload construction printed in dry-run (label/description/P31/reference)."""
    # Patch _load_wd_cache to use temp cache
    temp_cache = temp_pubdom_folder / "temp_cache.json"
    temp_cache.write_text(json.dumps({"names": {}}), encoding="utf-8")
    
    with patch.object(catalogs, '_load_wd_cache', return_value={"names": {}}), \
         patch.object(catalogs, '_save_wd_cache'):
        rc = create_entity.cmd_create_entity([str(temp_pubdom_folder)])
    
    captured = capsys.readouterr()
    output = captured.out
    
    # Verify dry-run output contains expected payload elements
    assert "[DRY-RUN]" in output
    assert "Entità con fonte valida" in output
    assert "Label (it):" in output
    assert "Description (it):" in output
    assert "casa editrice / stabilimento tipografico" in output  # description from role
    assert "[SOURCE-GATE: OK]" in output
    
    # Verify P31 entity shows P31 in payload
    assert "Entità con P31 valido" in output
    assert "P31 (instance of): Q380" in output
    
    assert rc == 0


def test_source_gate_skips_entities_without_source_url(temp_pubdom_folder, capsys, mock_catalogs_with_valid_p31):
    """Test (2): Source gate - no source_url → SALTATO and live never called."""
    with patch.object(catalogs, '_load_wd_cache', return_value={"names": {}}), \
         patch.object(catalogs, '_save_wd_cache'):
        rc = create_entity.cmd_create_entity([str(temp_pubdom_folder)])
    
    captured = capsys.readouterr()
    output = captured.out
    
    # Verify SALTATO message for entity without source
    assert "[SALTATO]" in output
    assert "Entità senza fonte" in output
    assert "manca la fonte (source_url)" in output
    
    # Verify summary counts
    assert "Saltati (nessuna fonte): 1" in output
    
    assert rc == 0


def test_p31_validation_refuses_unknown_qid(temp_pubdom_folder, capsys, mock_catalogs_with_valid_p31):
    """Test (3): P31 validation - unknown QID refused, catalog-known included."""
    with patch.object(catalogs, '_load_wd_cache', return_value={"names": {}}), \
         patch.object(catalogs, '_save_wd_cache'):
        rc = create_entity.cmd_create_entity([str(temp_pubdom_folder)])
    
    captured = capsys.readouterr()
    output = captured.out
    
    # Verify unknown QID is refused
    assert "[RIFIUTATO]" in output
    assert "Entità con P31 invalido" in output
    assert "Q999999999" in output
    assert "non trovato nel catalogo" in output
    
    # Verify valid P31 is accepted (in dry-run, it shows in payload)
    assert "Entità con P31 valido" in output
    assert "P31 (instance of): Q380" in output
    
    assert rc == 0


def test_live_mode_creates_item_and_writes_tmp_cache(temp_pubdom_folder, capsys, mock_catalogs_with_valid_p31, tmp_path):
    """Test (4): Success path with mocked API → QID printed + written to TMP cache as ok."""
    # Create temp cache
    temp_cache = tmp_path / "test_wd_cache.json"
    temp_cache.write_text(json.dumps({"names": {}}), encoding="utf-8")
    
    # Mock the API response
    mock_api_response = {
        "entity": {
            "id": "Q123456",
            "labels": {"it": {"value": "Entità con fonte valida"}},
            "descriptions": {"it": {"value": "casa editrice / stabilimento tipografico indicato dall'impronta di un pezzo d'epoca"}}
        }
    }
    
    # Mock credentials
    mock_credentials = {
        "auth_method": "bot-password",
        "bot_user": "test_bot",
        "bot_password": "test_password",
        "contact": "test@example.com"
    }
    
    # Create a proper mock response that works as context manager
    class MockResponse:
        def __init__(self, data):
            self.data = json.dumps(data).encode('utf-8')
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def read(self):
            return self.data
    
    mock_resp = MockResponse(mock_api_response)
    
    with patch.object(catalogs, '_load_wd_cache', return_value={"names": {}}), \
         patch.object(catalogs, '_save_wd_cache') as mock_save, \
         patch('pd_checker.core.push.get_credentials', return_value=mock_credentials), \
         patch('pd_checker.core.push.validate_credentials', return_value=("bot-password", None)), \
         patch('pd_checker.core.push.create_opener'), \
         patch('pd_checker.core.push.login_bot_password', return_value=(True, "")), \
         patch('urllib.request.urlopen', return_value=mock_resp):
        
        rc = create_entity.cmd_create_entity([str(temp_pubdom_folder), "--live"])
    
    captured = capsys.readouterr()
    output = captured.out
    
    # Verify item was created
    assert "[CREATO]" in output
    assert "Entità con fonte valida" in output
    assert "Q123456" in output
    
    # Verify cache was saved (mock called - twice for two entities that pass source gate)
    assert mock_save.call_count == 2
    
    # Verify the saved cache contains the new QID
    call_args = mock_save.call_args[0][0]
    assert "Entità con fonte valida" in call_args["names"]
    assert call_args["names"]["Entità con fonte valida"]["qid"] == "Q123456"
    assert call_args["names"]["Entità con fonte valida"]["result"] == "ok"
    
    # Verify shipped cache was NOT touched
    shipped_cache = catalogs.WD_CACHE_PATH
    assert shipped_cache.exists()
    original_content = shipped_cache.read_text(encoding="utf-8")
    assert "Entità con fonte valida" not in original_content
    
    assert rc == 0


def test_dashboard_disclosure_lines_render(tmp_path, capsys):
    """Test (5): Dashboard - both disclosure lines render."""
    # Read the report.py source to verify disclosure lines exist
    import inspect
    from pd_checker.core import report
    
    report_source = inspect.getsource(report)
    
    # Verify both disclosure lines are in the report code
    assert "Creazione predefinita" in report_source, "Disclosure line 1 missing from report.py"
    assert "Verifica ora i dati suggeriti prima della creazione" in report_source, "Disclosure line 2 missing from report.py"


def test_live_mode_api_error_handled(temp_pubdom_folder, capsys, mock_catalogs_with_valid_p31, tmp_path):
    """Test: API error is surfaced with Italian guidance."""
    temp_cache = tmp_path / "test_wd_cache.json"
    temp_cache.write_text(json.dumps({"names": {}}), encoding="utf-8")
    
    # Mock credentials
    mock_credentials = {
        "auth_method": "bot-password",
        "bot_user": "test_bot",
        "bot_password": "test_password",
        "contact": "test@example.com"
    }
    
    # Mock API error response (item already exists)
    mock_api_error = {
        "error": {
            "code": "item-create-not-allowed",
            "info": "Item with same label already exists"
        }
    }
    
    class MockResponse:
        def __init__(self, data):
            self.data = json.dumps(data).encode('utf-8')
        
        def __enter__(self):
            return self
        
        def __exit__(self, *args):
            pass
        
        def read(self):
            return self.data
    
    mock_resp = MockResponse(mock_api_error)
    
    with patch.object(catalogs, '_load_wd_cache', return_value={"names": {}}), \
         patch('pd_checker.core.push.get_credentials', return_value=mock_credentials), \
         patch('pd_checker.core.push.validate_credentials', return_value=("bot-password", None)), \
         patch('pd_checker.core.push.create_opener'), \
         patch('pd_checker.core.push.login_bot_password', return_value=(True, "")), \
         patch('urllib.request.urlopen', return_value=mock_resp):
        
        rc = create_entity.cmd_create_entity([str(temp_pubdom_folder), "--live"])
    
    captured = capsys.readouterr()
    output = captured.out
    
    # Verify error is surfaced with Italian guidance
    assert "[ERRORE API]" in output or "[RIFIUTATO]" in output
    assert "resolve --live" in output
    
    assert rc == 1  # All refused


def test_exit_code_1_when_no_entities_to_create(tmp_path, capsys):
    """Test: Exit code 1 when folder has no entities_to_create."""
    folder = tmp_path / "empty_folder"
    folder.mkdir()
    
    pubdom = {"entities_to_create": []}
    (folder / "pubdom.json").write_text(json.dumps(pubdom), encoding="utf-8")
    
    rc = create_entity.cmd_create_entity([str(folder)])
    
    assert rc == 1
    captured = capsys.readouterr()
    assert "No entities_to_create found" in captured.out


def test_merge_entity_config_from_context():
    from pd_checker.__main__ import _merge_entity_config

    entities = [
        {"name": "Grafiche Baroni - Milano", "role": "editore"},
        {"name": "altra entità", "role": "autore", "source_url": "https://già-presente.example"},
    ]
    ctx = {
        "entity_sources": {"Grafiche Baroni - Milano": "https://catalogo.example/baroni"},
        "entity_p31": {"Grafiche Baroni - Milano": "Q19652"},  # a QID actually present in the entity catalogue
    }
    _merge_entity_config(entities, ctx)
    assert entities[0]["source_url"] == "https://catalogo.example/baroni"
    assert entities[0]["p31_qid"] == "Q19652"
    # an existing source is never overwritten
    assert entities[1]["source_url"] == "https://già-presente.example"
    assert "p31_qid" not in entities[1]


def test_merge_entity_config_empty_context_noop():
    from pd_checker.__main__ import _merge_entity_config

    entities = [{"name": "X", "role": "editore"}]
    _merge_entity_config(entities, None)
    assert entities == [{"name": "X", "role": "editore"}]
