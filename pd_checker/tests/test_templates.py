"""Template catalog validation tests.

This test suite ensures that every template the engine can emit is present
in the Commons catalog. If someone reintroduces a phantom template like
"{{PD-Italy-photo}}", this test will fail at build time.
"""

import json
import pytest

from pd_checker.core import rules as engine, catalogs

templates = catalogs


def test_normalize_strips_braces():
    """normalize() should strip surrounding braces and whitespace."""
    assert templates.normalize("{{PD-Italy}}") == "PD-Italy"
    assert templates.normalize(" {{PD-1996}} ") == "PD-1996"
    assert templates.normalize("Template:PD-anon-70-EU") == "PD-anon-70-EU"
    assert templates.normalize("{{Template:PD-Italy}}") == "PD-Italy"
    assert templates.normalize("PD-old-70") == "PD-old-70"


def test_normalize_empty_string():
    """normalize() should handle empty strings."""
    assert templates.normalize("") == ""
    assert templates.normalize("   ") == ""
    assert templates.normalize("{}") == ""
    assert templates.normalize("{{}}") == ""


def test_unknown_with_empty_catalog():
    """unknown() should return all names when catalog is empty."""
    unknown = templates.unknown(["{{PD-Italy}}", "{{PD-1996}}"], catalog=set())
    assert unknown == ["PD-Italy", "PD-1996"]


def test_unknown_with_valid_catalog():
    """unknown() should return only invalid names."""
    catalog = {"PD-Italy", "PD-1996", "PD-old-70"}
    
    # All valid
    assert templates.unknown(["{{PD-Italy}}", "{{PD-1996}}"], catalog) == []
    
    # Mix of valid and invalid
    unknown = templates.unknown(["{{PD-Italy}}", "{{PD-Italy-photo}}"], catalog)
    assert unknown == ["PD-Italy-photo"]
    
    # All invalid
    unknown = templates.unknown(["{{PD-Italy-photo}}", "{{PD-Phantom}}"], catalog)
    assert set(unknown) == {"PD-Italy-photo", "PD-Phantom"}


def test_load_returns_set():
    """load() should always return a set, never crash."""
    result = templates.load()
    assert isinstance(result, set)


# ============================================================================
# CRITICAL: Engine template validation tests
# ============================================================================
# These tests iterate through all engine cases and verify that every template
# the engine emits is present in the Commons catalog.
# ============================================================================


def _photo_facts(**overrides):
    """Helper to create photo facts for testing."""
    facts = {
        "file": "test.png",
        "document_type": "photo",
        "vlm_confidence": "high",
        "is_anonymous": False,
        "photo_classification": "simple",
        "reference_year": 1910,
        "author": None,
        "author_death_year": None,
        "rights_holder": None,
    }
    facts.update(overrides)
    return facts


def test_all_engine_templates_for_simple_photo_pd_are_valid():
    """Simple photo PD case: verify templates are in catalog."""
    catalog = templates.load()
    verdict = engine.evaluate(_photo_facts(reference_year=1910), current_year=2026)
    unknown = templates.unknown(verdict["commons_templates"], catalog)
    assert unknown == [], f"Unknown templates in simple photo PD case: {unknown}"


def test_all_engine_templates_for_anonymous_photo_pd_are_valid():
    """Anonymous photo PD case: verify templates are in catalog."""
    catalog = templates.load()
    verdict = engine.evaluate(
        _photo_facts(
            photo_classification="artistic",
            is_anonymous=True,
            rights_holder="company",
            reference_year=1950,
        ),
        current_year=2026,
    )
    unknown = templates.unknown(verdict["commons_templates"], catalog)
    assert unknown == [], f"Unknown templates in anonymous photo PD case: {unknown}"


def test_all_engine_templates_for_artistic_photo_with_known_death_are_valid():
    """Artistic photo with known death year: verify templates are in catalog."""
    catalog = templates.load()
    verdict = engine.evaluate(
        _photo_facts(
            photo_classification="artistic",
            is_anonymous=False,
            author="Test Author",
            author_death_year=1950,
        ),
        current_year=2026,
    )
    unknown = templates.unknown(verdict["commons_templates"], catalog)
    assert unknown == [], f"Unknown templates in artistic photo PD case: {unknown}"


def test_all_engine_templates_for_not_pd_have_no_templates():
    """Not-PD case: should have no templates (empty list)."""
    catalog = templates.load()
    verdict = engine.evaluate(
        _photo_facts(reference_year=2015),
        current_year=2026,
    )
    # Not-PD cases should have empty templates
    assert verdict["commons_templates"] == [], "Not-PD case should have no templates"


def test_all_engine_templates_for_uncertain_have_no_templates():
    """Uncertain case: should have no templates (empty list)."""
    catalog = templates.load()
    verdict = engine.evaluate(
        _photo_facts(reference_year=None),
        current_year=2026,
    )
    # Uncertain cases should have empty templates
    assert verdict["commons_templates"] == [], "Uncertain case should have no templates"


def test_all_engine_templates_for_periodical_are_valid():
    """Periodical (newspaper) case: verify templates are in catalog."""
    catalog = templates.load()
    verdict = engine.evaluate(
        _photo_facts(document_type="newspaper", photo_classification=None, reference_year=1950),
        current_year=2026,
    )
    unknown = templates.unknown(verdict["commons_templates"], catalog)
    assert unknown == [], f"Unknown templates in periodical PD case: {unknown}"


def test_all_engine_templates_for_artistic_anonymous_are_valid():
    """Artistic anonymous photo: verify templates are in catalog."""
    catalog = templates.load()
    verdict = engine.evaluate(
        _photo_facts(
            photo_classification="artistic",
            is_anonymous=True,
            reference_year=1950,
        ),
        current_year=2026,
    )
    unknown = templates.unknown(verdict["commons_templates"], catalog)
    assert unknown == [], f"Unknown templates in artistic anonymous PD case: {unknown}"


def test_phantom_template_pd_italy_photo_is_detected():
    """Regression test: PD-Italy-photo should be detected as unknown."""
    catalog = templates.load()
    # This is the phantom template that was previously emitted
    phantom = ["{{PD-Italy-photo}}"]
    unknown = templates.unknown(phantom, catalog)
    assert "PD-Italy-photo" in unknown, "PD-Italy-photo should be detected as unknown"


def test_all_known_templates_from_jurisdictions_are_valid():
    """Verify all templates mentioned in jurisdictions.py are in catalog."""
    catalog = templates.load()
    
    # All templates that jurisdictions.py can emit (verified from grep)
    known_templates = [
        "{{PD-Italy}}",
        "{{PD-1996}}",
        "{{PD-anon-70-EU}}",
        "{{PD-old-70}}",
        "{{PD-anon-70}}",  # UK anonymous
    ]
    
    unknown = templates.unknown(known_templates, catalog)
    assert unknown == [], f"Known templates missing from catalog: {unknown}"


# ============================================================================
# Commons metadata tests (source_template, caveat_templates, information)
# ============================================================================


def test_self_scanned_template_is_emitted_for_pd_photo():
    """PD simple photo case: verify {{Self-scanned}} is emitted as source_template."""
    verdict = engine.evaluate(_photo_facts(reference_year=1910), current_year=2026)
    assert verdict.get("source_template") == "{{Self-scanned}}", "source_template should be {{Self-scanned}} for self-made scans"


def test_personality_rights_caveat_when_people_present():
    """Identifiable people: verify {{Personality rights}} is added to caveat_templates."""
    verdict = engine.evaluate(
        _photo_facts(reference_year=1910, has_identifiable_people=True),
        current_year=2026,
    )
    caveat = verdict.get("caveat_templates", [])
    assert "{{Personality rights}}" in caveat, "{{Personality rights}} should be in caveat_templates when people are present"


def test_no_caveat_when_no_people():
    """No identifiable people: caveat_templates should be empty."""
    verdict = engine.evaluate(
        _photo_facts(reference_year=1910, has_identifiable_people=False),
        current_year=2026,
    )
    assert verdict.get("caveat_templates", []) == [], "caveat_templates should be empty when no people"


def test_no_pd_art_or_pd_scan_templates():
    """Verify {{PD-Art}} and {{PD-scan}} are NOT used for self-made scans."""
    catalog = templates.load()
    verdict = engine.evaluate(_photo_facts(reference_year=1910), current_year=2026)
    
    all_templates = verdict.get("commons_templates", []) + [verdict.get("source_template", "")]
    all_templates_str = " ".join(all_templates)
    
    assert "PD-Art" not in all_templates_str, "{{PD-Art}} should NOT be used for self-made scans"
    assert "PD-scan" not in all_templates_str, "{{PD-scan}} should NOT be used for self-made scans"


def test_information_block_in_wikidata():
    """Verify the information block is present in wikidata statements."""
    from pd_checker.core import wikidata
    
    item = _photo_facts(reference_year=1910)
    verdict = engine.evaluate(item, current_year=2026)
    wikidata_block = wikidata.build(verdict, ctx={})  # Empty context for testing
    
    # Check that information block exists
    assert "information" in wikidata_block, "information block should be in wikidata"
    
    info = wikidata_block["information"]
    # Check required fields
    assert "description" in info, "description field required"
    assert "date" in info, "date field required"
    assert "source" in info, "source field required"
    assert "author" in info, "author field required"
    assert "permission" in info, "permission field required"
    
    # Check source is {{Self-scanned}}
    assert info["source"] == "{{Self-scanned}}", "source should be {{Self-scanned}}"


def test_manifest_row_contains_information():
    """Verify upload_manifest.json rows contain the information block."""
    from pd_checker.core import wikidata
    
    items = [_photo_facts(reference_year=1910)]
    verdicts = {"items": [engine.evaluate(item, current_year=2026) for item in items]}
    manifest = wikidata.build_manifest(verdicts, ctx={})  # Empty context for testing
    
    assert len(manifest) > 0, "manifest should have at least one row"
    row = manifest[0]
    assert "information" in row, "manifest row should contain information block"
    info = row["information"]
    assert all(k in info for k in ["description", "date", "source", "author", "permission"]), "all info fields required"


# ============================================================================
# suggest_templates() tests - country filter and fallbacks
# ============================================================================


def test_country_filter_returns_all_matches(tmp_path):
    """Country filter should return ALL templates with that country's category."""
    # Create a fake enriched catalog with 3 Italian templates
    fake_catalog = {
        "templates": ["PD-Italy", "PD-Italy-EdictGov", "PD-Italy-audio"],
        "meta": {
            "PD-Italy": {"categories": ["PD Italy license tags", "PD expiration templates by country"]},
            "PD-Italy-EdictGov": {"categories": ["Category:PD-EUGov license tags", "PD Italy license tags"]},
            "PD-Italy-audio": {"categories": ["License tags for audio files", "PD Italy license tags"]},
        }
    }
    
    catalog_file = tmp_path / "test_catalog.json"
    catalog_file.write_text(json.dumps(fake_catalog))
    
    # Temporarily override _CATALOG_PATH
    from pd_checker.core import catalogs
    templates = catalogs
    original_path = templates.TEMPLATE_CATALOG_PATH
    templates.TEMPLATE_CATALOG_PATH = catalog_file
    
    try:
        # Call suggest_templates with country IT
        result = templates.suggest_templates(country="IT", anonymous=False, restrictions=False)
        
        # Should return 0 (success) and find all 3 Italian templates
        assert result == 0, f"Expected exit code 0, got {result}"
    finally:
        # Restore original path
        templates.TEMPLATE_CATALOG_PATH = original_path


def test_empty_country_shows_fallbacks(tmp_path, capsys):
    """Country with no specific tag should show generic fallbacks (PD-old-70, PD-anon-70-EU)."""
    # Create a fake enriched catalog with no Vatican templates, but with generic fallbacks
    fake_catalog = {
        "templates": ["PD-old-70", "PD-anon-70-EU", "PD-Italy"],
        "meta": {
            "PD-old-70": {"categories": ["PD-old license tags"]},
            "PD-anon-70-EU": {"categories": ["PD-anon license tags", "PD European Union license tags"]},
            "PD-Italy": {"categories": ["PD Italy license tags"]},
        }
    }
    
    catalog_file = tmp_path / "test_catalog.json"
    catalog_file.write_text(json.dumps(fake_catalog))
    
    # Temporarily override _CATALOG_PATH
    from pd_checker.core import catalogs
    templates = catalogs
    original_path = templates.TEMPLATE_CATALOG_PATH
    templates.TEMPLATE_CATALOG_PATH = catalog_file
    
    try:
        # Call suggest_templates with country VA (Vatican - no templates)
        result = templates.suggest_templates(country="VA", anonymous=False, restrictions=False)
        
        # Should return 0 (success, not an error)
        assert result == 0, f"Expected exit code 0 for fallback case, got {result}"
        
        # Check output contains fallback message
        captured = capsys.readouterr()
        assert "No country-specific template found" in captured.out, "Should mention no country-specific template"
        assert "PD-old-70" in captured.out, "Should list PD-old-70 as fallback"
        assert "PD-anon-70-EU" in captured.out, "Should list PD-anon-70-EU as fallback"
    finally:
        # Restore original path
        templates.TEMPLATE_CATALOG_PATH = original_path


# ============================================================================
# Bug A3 regression tests - catalog loading must raise errors, not return empty sets
# ============================================================================


def test_load_templates_raises_on_missing_file(tmp_path):
    """Bug A3: Missing template catalog should raise FileNotFoundError, not return empty set."""
    from pd_checker.core import catalogs
    templates = catalogs
    
    # Temporarily point to a non-existent file
    original_path = templates.TEMPLATE_CATALOG_PATH
    templates.TEMPLATE_CATALOG_PATH = tmp_path / "nonexistent.json"
    
    try:
        with pytest.raises(FileNotFoundError) as exc_info:
            templates.load_templates()
        
        # Error message should mention the path
        assert str(tmp_path / "nonexistent.json") in str(exc_info.value)
    finally:
        templates.TEMPLATE_CATALOG_PATH = original_path


def test_load_templates_raises_on_malformed_json(tmp_path):
    """Bug A3: Malformed JSON in template catalog should raise JSONDecodeError."""
    from pd_checker.core import catalogs
    templates = catalogs
    
    # Create a malformed JSON file
    catalog_file = tmp_path / "bad_catalog.json"
    catalog_file.write_text("{ invalid json }")
    
    original_path = templates.TEMPLATE_CATALOG_PATH
    templates.TEMPLATE_CATALOG_PATH = catalog_file
    
    try:
        with pytest.raises(json.JSONDecodeError):
            templates.load_templates()
    finally:
        templates.TEMPLATE_CATALOG_PATH = original_path


def test_load_templates_raises_on_invalid_structure(tmp_path):
    """Bug A3: Invalid catalog structure should raise ValueError."""
    from pd_checker.core import catalogs
    templates = catalogs
    
    # Create a catalog with wrong structure (not a list)
    catalog_file = tmp_path / "bad_structure.json"
    catalog_file.write_text(json.dumps({"templates": "not a list"}))
    
    original_path = templates.TEMPLATE_CATALOG_PATH
    templates.TEMPLATE_CATALOG_PATH = catalog_file
    
    try:
        with pytest.raises(ValueError):
            templates.load_templates()
    finally:
        templates.TEMPLATE_CATALOG_PATH = original_path
