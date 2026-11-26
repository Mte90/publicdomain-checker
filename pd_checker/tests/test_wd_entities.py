"""Wikidata entity catalog validation tests.

This test suite ensures that every P#### and Q#### ID referenced in the
package source is present in the wikidata_entities.json catalog. If someone
introduces a phantom entity like "P99999" or "Q99999", this test will fail
at build time.
"""

import json
import re
from pathlib import Path

import pytest

from pd_checker.core import catalogs
wd_entities = catalogs


# Path to the package source directory
HERE = Path(__file__).resolve().parent.parent / "data"
PACKAGE_SOURCE = HERE


def _extract_ids_from_file(file_path: Path) -> list[str]:
    """Extract all P#### and Q#### IDs from a file."""
    content = file_path.read_text(encoding="utf-8")
    pattern = r'\b(P\d+|Q\d+)\b'
    return re.findall(pattern, content)


def _all_ids_in_package() -> list[str]:
    """Extract all entity IDs from all .py files in the package."""
    all_ids = set()
    for py_file in PACKAGE_SOURCE.glob("*.py"):
        # Skip test files
        if py_file.name.startswith("test_"):
            continue
        ids = _extract_ids_from_file(py_file)
        all_ids.update(ids)
    return sorted(all_ids)


def test_load_returns_dict():
    """load() should always return a dict with properties and items keys."""
    result = wd_entities.wd_entities_load()
    assert isinstance(result, dict)
    assert "properties" in result
    assert "items" in result
    assert isinstance(result["properties"], dict)
    assert isinstance(result["items"], dict)


def test_unknown_with_empty_catalog():
    """unknown() should return all IDs when catalog is empty."""
    unknown = wd_entities.wd_entities_unknown(["P31", "Q192425"], catalog={"properties": {}, "items": {}})
    assert unknown == ["P31", "Q192425"]


def test_unknown_with_valid_catalog():
    """unknown() should return only invalid IDs."""
    catalog = {
        "properties": {"P31": {}, "P170": {}},
        "items": {"Q192425": {}, "Q125191": {}}
    }
    
    # All valid
    assert wd_entities.wd_entities_unknown(["P31", "Q192425"], catalog) == []
    
    # Mix of valid and invalid
    unknown = wd_entities.wd_entities_unknown(["P31", "P99999", "Q192425", "Q88888"], catalog)
    assert set(unknown) == {"P99999", "Q88888"}
    
    # All invalid
    unknown = wd_entities.wd_entities_unknown(["P99999", "Q88888"], catalog)
    assert set(unknown) == {"P99999", "Q88888"}


def test_extract_entity_ids():
    """extract_entity_ids() should find all P/Q IDs in a string."""
    text = "Use P31 for instance of and Q192425 for postcard. Also P170 and Q125191."
    ids = wd_entities.extract_entity_ids(text)
    assert set(ids) == {"P31", "Q192425", "P170", "Q125191"}


def test_extract_entity_ids_no_duplicates():
    """extract_entity_ids() should return unique IDs."""
    text = "P31 and P31 and Q192425 and P31"
    ids = wd_entities.extract_entity_ids(text)
    assert ids == ["P31", "Q192425"]  # Order preserved, no duplicates


def test_phantom_property_is_detected():
    """Regression test: P99999 should be detected as unknown."""
    catalog = wd_entities.wd_entities_load()
    phantom = ["P99999"]
    unknown = wd_entities.wd_entities_unknown(phantom, catalog)
    assert "P99999" in unknown, "P99999 should be detected as unknown"


def test_phantom_item_is_detected():
    """Regression test: Q99999 should be detected as unknown."""
    catalog = wd_entities.wd_entities_load()
    phantom = ["Q99999"]
    unknown = wd_entities.wd_entities_unknown(phantom, catalog)
    assert "Q99999" in unknown, "Q99999 should be detected as unknown"


# ============================================================================
# CRITICAL: Build-time verification - all IDs in source must be in catalog
# ============================================================================
# These tests ensure that every P#### and Q#### literal hardcoded in the
# package's source code is present in the wikidata_entities.json catalog.
# A non-existent ID becomes a build failure.
# ============================================================================


def test_all_property_ids_in_source_are_in_catalog():
    """Every P#### literal in package source must be in the catalog."""
    catalog = wd_entities.wd_entities_load()
    all_ids = _all_ids_in_package()
    property_ids = [id for id in all_ids if id.startswith("P")]
    
    missing = []
    for prop_id in property_ids:
        if prop_id not in catalog.get("properties", {}):
            missing.append(prop_id)
    
    assert missing == [], f"Property IDs in source but missing from catalog: {missing}"


def test_all_item_ids_in_source_are_in_catalog():
    """Every Q#### literal in package source must be in the catalog."""
    catalog = wd_entities.wd_entities_load()
    all_ids = _all_ids_in_package()
    item_ids = [id for id in all_ids if id.startswith("Q")]
    
    missing = []
    for item_id in item_ids:
        if item_id not in catalog.get("items", {}):
            missing.append(item_id)
    
    assert missing == [], f"Item IDs in source but missing from catalog: {missing}"


def test_all_source_ids_pass_unknown_check():
    """All IDs found in source should return empty from unknown()."""
    all_ids = _all_ids_in_package()
    unknown = wd_entities.wd_entities_unknown(all_ids)
    assert unknown == [], f"Source IDs not in catalog: {unknown}"


def test_catalog_has_required_properties():
    """The catalog must have all properties used by wikidata.py."""
    catalog = wd_entities.wd_entities_load()
    required_properties = ["P31", "P170", "P180", "P275", "P571", "P6216", 
                          "P7482", "P1480", "P1319", "P1326"]
    
    missing = []
    for prop_id in required_properties:
        if prop_id not in catalog.get("properties", {}):
            missing.append(prop_id)
    
    assert missing == [], f"Required properties missing from catalog: {missing}"


def test_catalog_has_required_items():
    """The catalog must have all items used by wikidata.py and the QID cache."""
    catalog = wd_entities.wd_entities_load()
    required_items = ["Q192425", "Q125191", "Q19652", "Q50423863", 
                     "Q507540", "Q43739", "Q3437", "Q46787", "Q5727902", "Q1985727"]
    
    missing = []
    for item_id in required_items:
        if item_id not in catalog.get("items", {}):
            missing.append(item_id)
    
    assert missing == [], f"Required items missing from catalog: {missing}"


def test_catalog_metadata_present():
    """The catalog must have note, source, and fetched fields."""
    with open(HERE / "wikidata_entities.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    assert "note" in data, "Catalog must have 'note' field"
    assert "source" in data, "Catalog must have 'source' field"
    assert "fetched" in data, "Catalog must have 'fetched' field"
    assert data["source"] == "https://www.wikidata.org/w/api.php", "Source must be Wikidata API"


def test_catalog_properties_have_labels():
    """All properties in catalog must have label_it and label_en."""
    with open(HERE / "wikidata_entities.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    for prop_id, prop_data in data.get("properties", {}).items():
        assert "label_it" in prop_data, f"Property {prop_id} missing label_it"
        assert "label_en" in prop_data, f"Property {prop_id} missing label_en"


def test_catalog_items_have_labels():
    """All items in catalog must have label_it and label_en."""
    with open(HERE / "wikidata_entities.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    
    for item_id, item_data in data.get("items", {}).items():
        assert "label_it" in item_data, f"Item {item_id} missing label_it"
        assert "label_en" in item_data, f"Item {item_id} missing label_en"


# ============================================================================
# Bug A2 regression tests - manifest license must derive from verdict, not hardcoded
# ============================================================================


def test_manifest_license_derived_from_verdict_templates():
    """Bug A2: license_template should be the first template from verdict, not hardcoded."""
    from pd_checker.core import wikidata
    
    # Case 1: public_domain with templates -> first template
    verdict_pd = {
        "status": "public_domain",
        "commons_templates": ["{{PD-anon-70-EU}}", "{{PD-1996}}"],
        "extracted_facts": {"images": []},
        "upload": {"flag": "ready"}
    }
    verdicts = {"items": [verdict_pd]}
    manifest = wikidata.build_manifest(verdicts, ctx={})
    
    assert len(manifest) == 1
    assert manifest[0]["license_template"] == "{{PD-anon-70-EU}}"
    assert manifest[0]["license_templates"] == ["{{PD-anon-70-EU}}", "{{PD-1996}}"]
    # Should NOT be hardcoded to "PD-Italy"
    assert manifest[0]["license_template"] != "PD-Italy"


def test_manifest_license_none_when_no_templates():
    """Bug A2: uncertain status with no templates -> both license fields empty."""
    from pd_checker.core import wikidata
    
    # Case 2: uncertain with no templates -> None and []
    verdict_uncertain = {
        "status": "uncertain",
        "commons_templates": [],
        "extracted_facts": {"images": []},
        "upload": {"flag": "ready"}
    }
    verdicts = {"items": [verdict_uncertain]}
    manifest = wikidata.build_manifest(verdicts, ctx={})
    
    assert len(manifest) == 1
    assert manifest[0]["license_template"] is None
    assert manifest[0]["license_templates"] == []
    # Should NOT be "Copyrighted"
    assert manifest[0]["license_template"] != "Copyrighted"


def test_manifest_license_not_public_domain_no_templates():
    """Bug A2: not_public_domain with no templates -> both license fields empty."""
    from pd_checker.core import wikidata
    
    verdict_not_pd = {
        "status": "not_public_domain",
        "commons_templates": [],
        "extracted_facts": {"images": []},
        "upload": {"flag": "ready"}
    }
    verdicts = {"items": [verdict_not_pd]}
    manifest = wikidata.build_manifest(verdicts, ctx={})
    
    assert len(manifest) == 1
    assert manifest[0]["license_template"] is None
    assert manifest[0]["license_templates"] == []


def test_cmd_resolve_live_found_prints_qid_and_exits_zero(monkeypatch, capsys):
    from pd_checker.__main__ import cmd_resolve

    monkeypatch.setattr(catalogs, "lookup", lambda name: None, raising=True)
    monkeypatch.setattr(
        catalogs, "resolve_live", lambda name: {"status": "found", "qid": "Q999999", "candidates": []}, raising=True
    )
    rc = cmd_resolve(["Nome Di Prova", "--live"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Q999999" in out


def test_cmd_resolve_live_ambiguous_prints_candidates_and_exits_2(monkeypatch, capsys):
    from pd_checker.__main__ import cmd_resolve

    monkeypatch.setattr(catalogs, "lookup", lambda name: None, raising=True)
    outcome = {
        "status": "ambiguous",
        "qid": None,
        "candidates": [{"qid": "Q1", "label": "Baroni", "description": "cognome italiano"}],
    }
    monkeypatch.setattr(catalogs, "resolve_live", lambda name: outcome, raising=True)
    rc = cmd_resolve(["Grafiche Baroni - Milano", "--live"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "Ricerca ambigua" in out
    assert "Q1" in out
    assert "--confirm" in out


def test_cmd_resolve_live_not_found_prints_guidance_and_exits_1(monkeypatch, capsys):
    from pd_checker.__main__ import cmd_resolve

    monkeypatch.setattr(catalogs, "lookup", lambda name: None, raising=True)
    monkeypatch.setattr(
        catalogs, "resolve_live", lambda name: {"status": "not_found", "qid": None, "candidates": []}, raising=True
    )
    rc = cmd_resolve(["Nome Di Prova 2", "--live"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "Non trovato su Wikidata" in out
    assert "--live" in out
