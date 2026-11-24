"""Tests for country suggestion — language/place → candidate-jurisdiction suggestions."""

import pytest

from pd_checker.core import context as country_suggest


class TestLanguageHints:
    """Test language-based country suggestions."""

    def test_italian_language(self):
        """it → IT (medium confidence)."""
        result = country_suggest.suggest(language="it")
        assert len(result) == 1
        assert result[0]["country"] == "IT"
        assert result[0]["confidence"] == "medium"
        assert "lingua italiana" in result[0]["reason"]

    def test_english_language_two_candidates(self):
        """en → GB and US (both listed, low confidence)."""
        result = country_suggest.suggest(language="en")
        assert len(result) == 2
        countries = {r["country"] for r in result}
        assert countries == {"GB", "US"}
        for r in result:
            assert r["confidence"] == "low"
            assert "lingua inglese" in r["reason"]

    def test_latin_language(self):
        """la → VA and IT (low confidence)."""
        result = country_suggest.suggest(language="la")
        assert len(result) == 2
        countries = {r["country"] for r in result}
        assert countries == {"VA", "IT"}
        for r in result:
            assert r["confidence"] == "low"

    def test_french_language(self):
        """fr → FR (medium confidence)."""
        result = country_suggest.suggest(language="fr")
        assert len(result) == 1
        assert result[0]["country"] == "FR"
        assert result[0]["confidence"] == "medium"

    def test_german_language(self):
        """de → DE (medium confidence)."""
        result = country_suggest.suggest(language="de")
        assert len(result) == 1
        assert result[0]["country"] == "DE"
        assert result[0]["confidence"] == "medium"

    def test_spanish_language(self):
        """es → ES (medium confidence)."""
        result = country_suggest.suggest(language="es")
        assert len(result) == 1
        assert result[0]["country"] == "ES"
        assert result[0]["confidence"] == "medium"

    def test_portuguese_language(self):
        """pt → PT (medium confidence)."""
        result = country_suggest.suggest(language="pt")
        assert len(result) == 1
        assert result[0]["country"] == "PT"
        assert result[0]["confidence"] == "medium"

    def test_dutch_language(self):
        """nl → NL (medium confidence)."""
        result = country_suggest.suggest(language="nl")
        assert len(result) == 1
        assert result[0]["country"] == "NL"
        assert result[0]["confidence"] == "medium"


class TestPlaceNames:
    """Test place name detection in text, postmark, and publisher."""

    def test_postmark_londra(self):
        """postmark 'Londra' → GB, high confidence."""
        result = country_suggest.suggest(postmark="Londra 24 X/XI")
        assert len(result) == 1
        assert result[0]["country"] == "GB"
        assert result[0]["confidence"] == "medium"
        assert "londra" in result[0]["reason"]

    def test_text_vatican(self):
        """text 'CITTÀ DEL VATICANO' → VA."""
        result = country_suggest.suggest(text="CITTÀ DEL VATICANO")
        assert len(result) == 1
        assert result[0]["country"] == "VA"
        assert "citta del vaticano" in result[0]["reason"]

    def test_publisher_roma(self):
        """publisher 'Stab. Sansaini - Roma' → IT."""
        result = country_suggest.suggest(publisher="Stab. Sansaini - Roma")
        assert len(result) == 1
        assert result[0]["country"] == "IT"
        assert "roma" in result[0]["reason"]

    def test_text_london(self):
        """text 'London' → GB."""
        result = country_suggest.suggest(text="London")
        assert len(result) == 1
        assert result[0]["country"] == "GB"

    def test_text_new_york(self):
        """text 'New York' → US."""
        result = country_suggest.suggest(text="New York")
        assert len(result) == 1
        assert result[0]["country"] == "US"

    def test_text_berlin(self):
        """text 'Berlin' → DE."""
        result = country_suggest.suggest(text="Berlin")
        assert len(result) == 1
        assert result[0]["country"] == "DE"

    def test_text_paris(self):
        """text 'Paris' → FR."""
        result = country_suggest.suggest(text="Paris")
        assert len(result) == 1
        assert result[0]["country"] == "FR"

    def test_case_insensitive(self):
        """Place detection is case-insensitive."""
        result1 = country_suggest.suggest(text="ROMA")
        result2 = country_suggest.suggest(text="roma")
        assert result1[0]["country"] == result2[0]["country"] == "IT"

    def test_accent_insensitive(self):
        """Place detection is accent-insensitive."""
        result1 = country_suggest.suggest(text="Citta del Vaticano")
        result2 = country_suggest.suggest(text="Città del Vaticano")
        assert result1[0]["country"] == result2[0]["country"] == "VA"


class TestCombinedEvidence:
    """Test language + place agreement for high confidence."""

    def test_language_en_postmark_londra_high_confidence(self):
        """en + postmark 'Londra' → GB high confidence, US low confidence."""
        result = country_suggest.suggest(language="en", postmark="Londra 24 X/XI")
        assert len(result) == 2
        
        gb = next(r for r in result if r["country"] == "GB")
        us = next(r for r in result if r["country"] == "US")
        
        assert gb["confidence"] == "high"
        assert us["confidence"] == "low"
        assert "lingua inglese" in gb["reason"]
        assert "londra" in gb["reason"]

    def test_language_it_text_milano_high_confidence(self):
        """it + text 'Milano' → IT high confidence."""
        result = country_suggest.suggest(language="it", text="Milano")
        assert len(result) == 1
        assert result[0]["country"] == "IT"
        assert result[0]["confidence"] == "high"

    def test_language_la_text_vatican_high_confidence(self):
        """la + text 'Vatican' → VA high confidence."""
        result = country_suggest.suggest(language="la", text="Vatican")
        va = next((r for r in result if r["country"] == "VA"), None)
        assert va is not None
        assert va["confidence"] == "high"


class TestEngineIntegration:
    """Test that engine.py sets country_candidates correctly."""

    def test_engine_sets_candidates_when_country_absent(self):
        """When country_of_origin is absent, engine sets country_candidates."""
        from pd_checker.core import rules as engine
        
        item = {
            "file": "test.jpg",
            "translation_lang": "en",
            "document_type": "photo",
        }
        
        verdict = engine.evaluate(item, current_year=2026)
        
        assert "country_candidates" in verdict
        assert len(verdict["country_candidates"]) > 0
        
        # Should have GB and US for English
        countries = {c["country"] for c in verdict["country_candidates"]}
        assert countries == {"GB", "US"}

    def test_engine_leaves_declared_country_untouched(self):
        """When country_of_origin is declared, country_candidates is empty."""
        from pd_checker.core import rules as engine
        
        item = {
            "file": "test.jpg",
            "country_of_origin": "IT",
            "document_type": "photo",
        }
        
        verdict = engine.evaluate(item, current_year=2026)
        
        assert verdict["country_of_origin"] == "IT"
        assert verdict.get("country_candidates", []) == []

    def test_engine_empty_country_triggers_candidates(self):
        """When country_of_origin is empty string, engine sets country_candidates."""
        from pd_checker.core import rules as engine
        
        item = {
            "file": "test.jpg",
            "country_of_origin": "",
            "translation_lang": "it",
            "document_type": "photo",
        }
        
        verdict = engine.evaluate(item, current_year=2026)
        
        assert verdict["country_of_origin"] == "IT"  # Falls back to IT
        assert "country_candidates" in verdict
        assert len(verdict["country_candidates"]) > 0


class TestClassificationConsistency:
    """Test classification consistency warning within series."""

    def test_same_series_different_classification_warns(self):
        """Items in the same series with different photo_classification should trigger a warning."""
        items = [
            {
                "extracted_facts": {
                    "series_id": "pezzini-001",
                    "photo_classification": "artistic",
                },
                "status": "public_domain",
            },
            {
                "extracted_facts": {
                    "series_id": "pezzini-001",
                    "photo_classification": "artistic",
                },
                "status": "public_domain",
            },
            {
                "extracted_facts": {
                    "series_id": "pezzini-001",
                    "photo_classification": "simple",
                },
                "status": "public_domain",
            },
        ]
        
        warnings = country_suggest._detect_contradictions(items)
        
        # Should have a warning about classification inconsistency
        classification_warnings = [w for w in warnings if "classificazione fotografica" in w]
        assert len(classification_warnings) == 1, f"Expected 1 classification warning, got {len(classification_warnings)}"
        assert "pezzini-001" in classification_warnings[0]
        assert "artistic" in classification_warnings[0]
        assert "simple" in classification_warnings[0]

    def test_same_series_same_classification_no_warning(self):
        """Items in the same series with the same photo_classification should NOT trigger a warning."""
        items = [
            {
                "extracted_facts": {
                    "series_id": "pezzini-001",
                    "photo_classification": "artistic",
                },
                "status": "public_domain",
            },
            {
                "extracted_facts": {
                    "series_id": "pezzini-001",
                    "photo_classification": "artistic",
                },
                "status": "public_domain",
            },
        ]
        
        warnings = country_suggest._detect_contradictions(items)
        
        # Should NOT have a classification warning
        classification_warnings = [w for w in warnings if "classificazione fotografica" in w]
        assert len(classification_warnings) == 0

    def test_different_series_no_warning(self):
        """Items in different series with different classifications should NOT trigger a warning."""
        items = [
            {
                "extracted_facts": {
                    "series_id": "series-a",
                    "photo_classification": "artistic",
                },
                "status": "public_domain",
            },
            {
                "extracted_facts": {
                    "series_id": "series-b",
                    "photo_classification": "simple",
                },
                "status": "public_domain",
            },
        ]
        
        warnings = country_suggest._detect_contradictions(items)
        
        # Should NOT have a classification warning (different series)
        classification_warnings = [w for w in warnings if "classificazione fotografica" in w]
        assert len(classification_warnings) == 0

    def test_missing_series_id_no_warning(self):
        """Items without series_id should be ignored for classification consistency check."""
        items = [
            {
                "extracted_facts": {
                    "photo_classification": "artistic",
                },
                "status": "public_domain",
            },
            {
                "extracted_facts": {
                    "photo_classification": "simple",
                },
                "status": "public_domain",
            },
        ]
        
        warnings = country_suggest._detect_contradictions(items)
        
        # Should NOT have a classification warning (no series_id)
        classification_warnings = [w for w in warnings if "classificazione fotografica" in w]
        assert len(classification_warnings) == 0

    def test_missing_classification_no_warning(self):
        """Items without photo_classification should be ignored for classification consistency check."""
        items = [
            {
                "extracted_facts": {
                    "series_id": "pezzini-001",
                    "photo_classification": "artistic",
                },
                "status": "public_domain",
            },
            {
                "extracted_facts": {
                    "series_id": "pezzini-001",
                    # No photo_classification
                },
                "status": "public_domain",
            },
        ]
        
        warnings = country_suggest._detect_contradictions(items)
        
        # Should NOT have a classification warning (only one item has classification)
        classification_warnings = [w for w in warnings if "classificazione fotografica" in w]
        assert len(classification_warnings) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
