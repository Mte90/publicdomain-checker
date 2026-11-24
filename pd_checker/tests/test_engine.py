from pathlib import Path

from pd_checker.core import rules as engine


def _item(**overrides):
    facts = {
        "file": "x.png",
        "document_type": "photo",
        "vlm_confidence": "high",
        "is_anonymous": True,
        "photo_classification": "simple",
        "reference_year": None,
        "author": None,
        "author_death_year": None,
        "author_role": None,
    }
    facts.update(overrides)
    return facts


def test_simple_photo_old_is_public_domain():
    verdict = engine.evaluate(_item(reference_year=1910), current_year=2026)
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["pd_year"] == 1980
    assert verdict["confidence"] == "high"
    assert "{{PD-Italy}}" in verdict["commons_templates"]


def test_simple_photo_recent_is_not_public_domain():
    verdict = engine.evaluate(_item(reference_year=2015), current_year=2026)
    assert verdict["status"] == engine.NOT_PUBLIC_DOMAIN
    assert verdict["years_until_pd"] == 2015 + 70 - 2026


def test_simple_photo_pre_reform_stays_public_domain():
    # 20-year term expired in 2020, before the reform -> does not revive
    verdict = engine.evaluate(_item(reference_year=2000), current_year=2026)
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "medium"


def test_simple_photo_2005_is_seventy_years():
    verdict = engine.evaluate(_item(reference_year=2005), current_year=2026)
    assert verdict["status"] == engine.NOT_PUBLIC_DOMAIN
    assert verdict["pd_year"] == 2075


def test_simple_photo_missing_date_is_uncertain():
    verdict = engine.evaluate(_item(reference_year=None), current_year=2026)
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["missing_data"]


def test_artistic_photo_life_plus_70():
    verdict = engine.evaluate(
        _item(
            photo_classification="artistic",
            is_anonymous=False,
            author="Autore Test",
            author_death_year=1955,
        ),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["pd_year"] == 2026
    assert "{{PD-old-70}}" in verdict["commons_templates"]


def test_artistic_photo_recent_death_not_public_domain():
    verdict = engine.evaluate(
        _item(
            photo_classification="artistic",
            is_anonymous=False,
            author="Autore Test",
            author_death_year=1980,
        ),
        current_year=2026,
    )
    assert verdict["status"] == engine.NOT_PUBLIC_DOMAIN
    assert verdict["pd_year"] == 2051


def test_artistic_photo_missing_death_is_uncertain():
    verdict = engine.evaluate(
        _item(photo_classification="artistic", is_anonymous=False, author="Ignoto"),
        current_year=2026,
    )
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["missing_data"]


def test_letter_is_always_uncertain():
    verdict = engine.evaluate(
        _item(
            document_type="letter",
            photo_classification=None,
            is_anonymous=False,
            author="Italo Svevo",
            author_death_year=1928,
        ),
        current_year=2026,
    )
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["human_review_required"] is True
    surface = " ".join(verdict["legal_basis"] + verdict["blockers_non_copyright"])
    assert "Art. 93" in surface


def test_periodical_collective():
    verdict = engine.evaluate(
        _item(document_type="newspaper", photo_classification=None, reference_year=1950),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["pd_year"] == 2021


def test_unknown_type_is_uncertain():
    verdict = engine.evaluate(
        _item(document_type="unknown", photo_classification=None), current_year=2026
    )
    assert verdict["status"] == engine.UNCERTAIN


def test_confidence_is_worst_of_two():
    verdict = engine.evaluate(_item(reference_year=1910, vlm_confidence="low"), current_year=2026)
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "low"


def test_uncertain_photo_unclear():
    verdict = engine.evaluate(_item(photo_classification="unclear", reference_year=1910), current_year=2026)
    assert verdict["status"] == engine.UNCERTAIN


def test_photo_always_flags_portrait_right():
    verdict = engine.evaluate(_item(reference_year=1910), current_year=2026)
    assert any("96-97" in blocker for blocker in verdict["blockers_non_copyright"])


def test_visual_estimate_year_caps_confidence_to_low():
    """When year_source is visual_estimate, confidence is capped at low with a note."""
    verdict = engine.evaluate(
        _item(reference_year=1910, year_source="visual_estimate"),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "low"
    assert any("Anno stimato visivamente" in note for note in verdict["notes"])


def test_visible_text_does_not_cap_confidence():
    """When only visible_text is present (no visual_estimate), confidence is not capped."""
    verdict = engine.evaluate(
        _item(
            reference_year=1910,
            year_source="document",
            photo_classification="artistic",
            is_anonymous=False,
            author="Test Author",
            author_death_year=1950,
        ),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "high"
    assert not any("Anno stimato visivamente" in note for note in verdict["notes"])


def test_upload_flag_ready_for_clean_public_domain_photo():
    """Ready case: public_domain photo with high confidence, no portrait blocker, no missing_data."""
    verdict = engine.evaluate(
        _item(
            reference_year=1910,
            has_identifiable_people=False,  # no portrait blocker
        ),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "high"
    assert verdict["upload"]["flag"] == engine.UPLOAD_READY
    assert verdict["upload"]["reason"] == "In pubblico dominio, nessun vincolo: candidato per l'import manuale."
    assert "blockers_non_copyright" in verdict
    assert verdict["missing_data"] == []


def test_upload_flag_review_for_public_domain_with_blocker():
    """Review case: public_domain photo that has a blocker (identifiable people)."""
    verdict = engine.evaluate(
        _item(
            reference_year=1910,
            has_identifiable_people=True,  # portrait blocker present
        ),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["upload"]["flag"] == engine.UPLOAD_REVIEW
    assert verdict["upload"]["reason"] == "In pubblico dominio ma con vincoli o confidenza bassa: rivedere prima."
    assert any("96-97" in blocker for blocker in verdict["blockers_non_copyright"])


def test_upload_policy_strict_with_blocker():
    """Strict policy: public_domain + high confidence + non-copyright blocker → review."""
    verdict = engine.evaluate(
        _item(
            reference_year=1910,
            has_identifiable_people=True,  # portrait blocker present
        ),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_STRICT,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "high"
    assert verdict["upload"]["flag"] == engine.UPLOAD_REVIEW
    assert verdict["upload_policy"] == engine.UPLOAD_POLICY_STRICT
    assert any("96-97" in blocker for blocker in verdict["blockers_non_copyright"])


def test_upload_policy_pd_only_with_blocker():
    """pd-only policy: public_domain + high confidence + non-copyright blocker → ready."""
    verdict = engine.evaluate(
        _item(
            reference_year=1910,
            has_identifiable_people=True,  # portrait blocker present
        ),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_PD_ONLY,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "high"
    assert verdict["upload"]["flag"] == engine.UPLOAD_READY
    assert verdict["upload_policy"] == engine.UPLOAD_POLICY_PD_ONLY
    assert "blockers_non_copyright" in verdict
    assert any("96-97" in blocker for blocker in verdict["blockers_non_copyright"])


def test_upload_policy_pd_only_missing_data_still_review():
    """pd-only policy: missing data still prevents ready."""
    verdict = engine.evaluate(
        _item(
            reference_year=None,  # missing data
            has_identifiable_people=False,
        ),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_PD_ONLY,
    )
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["upload"]["flag"] == engine.UPLOAD_REVIEW
    assert verdict["missing_data"]


def test_uncertain_verdict_emits_no_pd_template():
    """An uncertain verdict must not assert a public-domain licence tag."""
    verdict = engine.evaluate(
        _item(
            country_of_origin="VA",
            is_vatican_asset=True,
            is_anonymous=False,
            author="X",
            author_death_year=1900,
        ),
        current_year=2026,
    )
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["commons_templates"] == []
    assert any("non emessi" in note for note in verdict["notes"])


# ============================================================================
# NEW TESTS FOR ESTIMATE MARGIN AND PRINTING WORKS DETECTION (§2§ requirements)
# ============================================================================


def test_estimate_year_within_margin_returns_uncertain():
    """When year_source is visual_estimate and expiry is within _ESTIMATE_MARGIN_YEARS (10),
    the verdict must be uncertain, not public_domain.
    
    Boundary case: expiry lands exactly at the margin (pd_year - current_year == 10).
    reference_year 1966 + 70 = pd_year 2036, current_year 2026
    years_until_pd = 2036 - 2026 = 10 (exactly at the margin) -> uncertain
    """
    verdict = engine.evaluate(
        _item(reference_year=1966, year_source="visual_estimate", has_identifiable_people=False),
        current_year=2026,
    )
    assert verdict["status"] == engine.UNCERTAIN, (
        f"Expected uncertain for estimate within margin, got {verdict['status']}"
    )
    assert "stimato visivamente" in verdict["reason"].lower() or "stima" in verdict["reason"].lower()


def test_estimate_year_outside_margin_stays_public_domain():
    """When year_source is visual_estimate but expiry is clearly outside the margin,
    the verdict remains public_domain.
    
    reference_year 1910 + 70 = pd_year 1980, current_year 2026
    pd_year - current_year = 1980 - 2026 = -46 (well outside the 10-year margin)
    """
    verdict = engine.evaluate(
        _item(reference_year=1910, year_source="visual_estimate", has_identifiable_people=False),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["pd_year"] == 1980


def test_estimate_year_shows_stimata_in_reason():
    """When year_source is visual_estimate and the work is public_domain,
    the reason must indicate the year is estimated ('stimata').
    """
    verdict = engine.evaluate(
        _item(reference_year=1910, year_source="visual_estimate", has_identifiable_people=False),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    # Check that the reason mentions the year is estimated
    reason_lower = verdict["reason"].lower()
    assert "stimato" in reason_lower or "stimata" in reason_lower


def test_real_year_returns_public_domain_without_stimata():
    """When year_source is a real date (not visual_estimate), the verdict
    returns public_domain without 'stimata' in the reason.
    """
    verdict = engine.evaluate(
        _item(reference_year=1910, year_source="document", has_identifiable_people=False),
        current_year=2026,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["pd_year"] == 1980
    # Check that the reason does NOT mention estimation
    reason_lower = verdict["reason"].lower()
    assert "stimato" not in reason_lower and "stimata" not in reason_lower


def test_anonymous_contradiction_warns_when_visible_text_has_publisher():
    """When is_anonymous=True but visible_text contains a publisher imprint,
    the verdict must include a warning note.
    """
    for marker in engine._PRINTING_WORK_MARKERS:
        verdict = engine.evaluate(
            _item(
                reference_year=1910,
                is_anonymous=True,
                visible_text=f"Ritratto fotografico. {marker} Roma",
                has_identifiable_people=False,
            ),
            current_year=2026,
        )
        assert verdict["status"] == engine.PUBLIC_DOMAIN
        # Check for the warning note about anonymous contradiction
        assert any("ATTENZIONE" in note and "impronta editoriale" in note for note in verdict["notes"]), (
            f"Expected anonymous contradiction warning for marker '{marker}'"
        )


def test_author_printing_work_not_used_for_art25():
    """When author looks like a printing works (contains markers like 'Stab.', 'Tipografia', etc.),
    it must not be used for Art. 25 (life+70) branch; instead use Art. 27 (collective work).
    """
    for marker in engine._PRINTING_WORK_MARKERS:
        author_name = f"{marker} - Roma"
        verdict = engine.evaluate(
            _item(
                photo_classification="artistic",
                is_anonymous=False,
                author=author_name,
                author_death_year=1950,
                has_identifiable_people=False,
            ),
            current_year=2026,
        )
        # Should use Art. 27 (collective work from publication year), not Art. 25 (life+70)
        assert "Art. 27" in str(verdict["legal_basis"]), (
            f"Expected Art. 27 for printing works author '{author_name}', got {verdict['legal_basis']}"
        )
        # Should have a warning note about printing works
        assert any("stabilimento di stampa" in note for note in verdict["notes"]), (
            f"Expected printing works warning for author '{author_name}'"
        )


def test_estimate_year_expiry_one_year_ago_returns_uncertain():
    """A visual estimate whose expiry was 1 year ago must be uncertain (within margin)."""
    # pd_year = 2025, current_year = 2026, years_until_pd = -1 (within margin of 10)
    facts = _item(
        reference_year=1955,  # 1955 + 70 = 2025
        year_source="visual_estimate",
        is_anonymous=False,
    )
    verdict = engine.evaluate(facts, current_year=2026)
    assert verdict["status"] == engine.UNCERTAIN, (
        f"Expected uncertain for estimate expiring 1 year ago, got {verdict['status']}"
    )
    assert "stimato visivamente" in verdict["reason"], (
        f"Expected 'stimato visivamente' in reason, got: {verdict['reason']}"
    )


def test_estimate_year_expiry_21_years_ago_stays_public_domain():
    """A visual estimate whose expiry was 21 years ago must stay public_domain (outside margin)."""
    # pd_year = 2005, current_year = 2026, years_until_pd = -21 (outside margin of 10)
    facts = _item(
        reference_year=1935,  # 1935 + 70 = 2005
        year_source="visual_estimate",
        is_anonymous=False,
    )
    verdict = engine.evaluate(facts, current_year=2026)
    assert verdict["status"] == engine.PUBLIC_DOMAIN, (
        f"Expected public_domain for estimate expiring 21 years ago, got {verdict['status']}"
    )
    # Should NOT have the uncertain reason
    assert "troppo vicina" not in verdict.get("reason", ""), (
        f"Expected no 'troppo vicina' in reason for expiry 21 years ago, got: {verdict.get('reason')}"
    )


# ============================================================================
# REGRESSION TESTS FOR WIKIDATA DATA EMISSION DEFECTS (§16 requirements)
# ============================================================================


def test_printing_work_author_emitted_as_publisher_not_photographer():
    """Regression test for Defect 1: A printing works (e.g., 'Stab. Sansaini - Roma') 
    must not be emitted as P170 'autore' (photographer). Instead, emit as P123 publisher 
    and mark P170 for review.
    
    This matches the real case in Cardinale-Del-Val/1_fronte.png where author is 
    'Stab. Sansaini - Roma' (a printing works, 'Stab.' = stabilimento tipografico).
    """
    from pd_checker.core import wikidata
    
    # Simulate verdict item with printing works as author
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": "Stab. Sansaini - Roma",
            "year_source": "visible_text",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
        "upload": {"flag": "review"},
    }
    ctx = {
        "depicts": [{"label": "Rafael Merry del Val", "qid": "Q507540"}],
        "provenance": "Collezione privata",
        "categories": [],
    }
    
    result = wikidata.build(verdict, ctx)
    
    # Find P170 and P123 statements
    p170_stmt = next((s for s in result["statements"] if s["property"] == "P170"), None)
    p123_stmt = next((s for s in result["statements"] if s["property"] == "P123"), None)
    
    # P170 should have null value and be marked for review
    assert p170_stmt is not None, "P170 statement must exist"
    assert p170_stmt["value"] is None, (
        f"P170 value should be None for printing works, got '{p170_stmt['value']}'"
    )
    assert p170_stmt["status"] == "da_rivedere", (
        f"P170 status should be 'da_rivedere', got '{p170_stmt['status']}'"
    )
    assert "stabilimento di stampa" in p170_stmt.get("notes", [""])[0].lower(), (
        "P170 should have a note about printing works"
    )
    
    # P123 (publisher) should exist with the printing works name
    assert p123_stmt is not None, "P123 (publisher) statement must exist for printing works"
    assert p123_stmt["value"] == "Stab. Sansaini - Roma", (
        f"P123 value should be 'Stab. Sansaini - Roma', got '{p123_stmt['value']}'"
    )
    assert p123_stmt["status"] == "da_rivedere", (
        f"P123 status should be 'da_rivedere', got '{p123_stmt['status']}'"
    )


def test_self_scanned_only_when_scan_trail_exists():
    """Regression test for Defect 2: {{Self-scanned}} must not be hardcoded for every item.
    Emit it only when the context actually records a self-scan; otherwise leave source unspecified.
    
    This matches the real case in Cardinale-Del-Val where item 8 has no scan trail at all
    - the folder context says provenance is 'da confermare con il detentore'.
    """
    from pd_checker.core import wikidata
    
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": None,
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    
    # Case 1: provenance says "da confermare" - should NOT emit {{Self-scanned}}
    ctx_unconfirmed = {
        "depicts": [],
        "provenance": "Collezione privata (da confermare con il detentore)",
        "categories": [],
    }
    result = wikidata.build(verdict, ctx_unconfirmed)
    assert result["information"]["source"] != "{{Self-scanned}}", (
        f"Source should NOT be {{Self-scanned}} when provenance is unconfirmed, got '{result['information']['source']}'"
    )
    assert "non specificato" in result["information"]["source"].lower(), (
        f"Source should be 'non specificato' or similar, got '{result['information']['source']}'"
    )
    
    # Case 2: no provenance - should default to {{Self-scanned}}
    ctx_empty = {
        "depicts": [],
        "provenance": "",
        "categories": [],
    }
    result = wikidata.build(verdict, ctx_empty)
    assert result["information"]["source"] == "{{Self-scanned}}", (
        f"Source should be {{Self-scanned}} when no provenance is given, got '{result['information']['source']}'"
    )


def test_p180_derives_subjects_from_visible_text_cache_match():
    """Regression test for Defect 3: P180 (raffigura) must not ignore subjects that are 
    already resolvable from the cache via visible_text.
    
    This matches the real case in Cardinale-Del-Val/7 where visible_text contains 
    'RIESE VENETO - IL PAPA PIO X E IL CARDINALE MERRY DEL VAL' and both 
    'Pio X' (Q43739) and 'Riese Veneto' (Q46787) are in the cache.
    """
    from pd_checker.core import wikidata
    
    # The shipped wd_cache.json already contains the required entries
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1910,
            "visible_text": "RIESE VENETO - IL PAPA PIO X E IL CARDINALE MERRY DEL VAL",
            "visible_text_translation": "RIESE VENETO - PAPA PIO X E CARDINALE MERRY DEL VAL",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {
        "depicts": [{"label": "Rafael Merry del Val", "qid": "Q507540"}],
        "provenance": "Collezione privata",
        "categories": [],
    }
    
    result = wikidata.build(verdict, ctx)
    
    # Find all P180 statements
    p180_stmts = [s for s in result["statements"] if s["property"] == "P180"]
    p180_values = [s["value"] for s in p180_stmts]
    
    # Should have context.depicts subject
    assert "Rafael Merry del Val" in p180_values, (
        f"P180 should include context.depicts subject, got {p180_values}"
    )
    
    # Should ALSO have subjects derived from visible_text cache match
    assert "Pio X" in p180_values, (
        f"P180 should include 'Pio X' from visible_text cache match, got {p180_values}"
    )
    assert "Riese Veneto" in p180_values, (
        f"P180 should include 'Riese Veneto' from visible_text cache match, got {p180_values}"
    )
    
    # Verify QIDs are correct
    pio_x_stmt = next(s for s in p180_stmts if s["value"] == "Pio X")
    assert pio_x_stmt["qid"] == "Q43739", (
        f"Pio X should have QID Q43739, got {pio_x_stmt['qid']}"
    )
    
    riese_stmt = next(s for s in p180_stmts if s["value"] == "Riese Veneto")
    assert riese_stmt["qid"] == "Q46787", (
        f"Riese Veneto should have QID Q46787, got {riese_stmt['qid']}"
    )


def test_p180_deduplicates_by_qid_not_by_alias():
    """Regression test for P180 duplicate emission bug.
    
    When multiple aliases of the same entity appear in visible_text,
    only ONE P180 statement should be emitted per QID, using the
    canonical name from the cache.
    
    This matches the real case where wd_cache.json has:
    - "Rafael Merry del Val", "Raffaele Merry del Val", "Merry del Val" → all Q507540
    - "Pio X", "Pius X" → both Q43739
    
    A text mentioning multiple aliases should yield exactly ONE statement per entity.
    """
    from pd_checker.core import wikidata
    
    # The shipped wd_cache.json already contains the required entries
    # Text that mentions multiple aliases of the SAME entity
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1910,
            # Multiple aliases for the same QID (Q507540)
            "visible_text": "RAFAEL MERRY DEL VAL e MERRY DEL VAL e PIO X e PIUS X",
            "visible_text_translation": "",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {
        "depicts": [],
        "provenance": "Collezione privata",
        "categories": [],
    }
    
    result = wikidata.build(verdict, ctx)
    
    # Find all P180 statements
    p180_stmts = [s for s in result["statements"] if s["property"] == "P180"]
    p180_qids = [s["qid"] for s in p180_stmts if s["qid"]]
    
    # Should have exactly 2 distinct QIDs (Q507540 for Merry del Val, Q43739 for Pio X)
    distinct_qids = set(p180_qids)
    assert len(distinct_qids) == 2, (
        f"Expected 2 distinct QIDs, got {len(distinct_qids)}: {p180_qids}"
    )
    
    # Should have exactly 2 statements (one per QID, not one per alias)
    assert len(p180_stmts) == 2, (
        f"Expected exactly 2 P180 statements (one per QID), got {len(p180_stmts)}: "
        f"{[(s['value'], s['qid']) for s in p180_stmts]}"
    )
    
    # Verify the QIDs are correct
    assert "Q507540" in distinct_qids, "Should include Q507540 (Merry del Val)"
    assert "Q43739" in distinct_qids, "Should include Q43739 (Pio X)"
    
    # Verify canonical names are used (first match in cache order)
    qid_to_value = {s["qid"]: s["value"] for s in p180_stmts}
    # At minimum, verify no duplicate QIDs
    assert len(p180_qids) == len(distinct_qids), (
        f"Duplicate QIDs found: {p180_qids}"
    )


def test_p180_two_genuine_entities_yields_two_statements():
    """Regression test: two genuinely different entities should yield two P180 statements.
    
    This ensures the deduplication doesn't over-aggressively merge distinct entities.
    """
    from pd_checker.core import wikidata
    
    # The shipped wd_cache.json already contains the required entries
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1910,
            # Two genuinely different entities
            "visible_text": "PIO X E RIESE VENETO",
            "visible_text_translation": "",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {
        "depicts": [],
        "provenance": "Collezione privata",
        "categories": [],
    }
    
    result = wikidata.build(verdict, ctx)
    
    # Find all P180 statements
    p180_stmts = [s for s in result["statements"] if s["property"] == "P180"]
    p180_qids = [s["qid"] for s in p180_stmts if s["qid"]]
    distinct_qids = set(p180_qids)
    
    # Should have exactly 2 distinct QIDs
    assert len(distinct_qids) == 2, (
        f"Expected 2 distinct QIDs, got {len(distinct_qids)}: {p180_qids}"
    )
    
    # Should have exactly 2 statements
    assert len(p180_stmts) == 2, (
        f"Expected exactly 2 P180 statements, got {len(p180_stmts)}"
    )
    
    # Verify the QIDs are correct
    assert "Q43739" in distinct_qids, "Should include Q43739 (Pio X)"
    assert "Q46787" in distinct_qids, "Should include Q46787 (Riese Veneto)"


# ============================================================================
# NEW TESTS FOR NO-EVIDENCE GUARD (Issue 1: verdict without evidence)
# ============================================================================


def test_no_text_low_confidence_becomes_uncertain():
    """When both visible_text and visible_text_translation are empty AND vlm_confidence is low,
    a visual_estimate year is a pure guess — public_domain must become uncertain.
    
    This is the exact scenario from Cardinale-Del-Val item 8:
    - visible_text: ""
    - visible_text_translation: ""
    - vlm_confidence: "low"
    - reference_year: 1920
    - year_source: "visual_estimate"
    - photo_classification: "simple"
    
    The year 1920+70=1990 is well outside the 10-year margin, but with no textual evidence
    at all, the verdict cannot be public_domain.
    """
    facts = _item(
        reference_year=1920,
        year_source="visual_estimate",
        visible_text="",
        visible_text_translation="",
        vlm_confidence="low",
        has_identifiable_people=False,
    )
    verdict = engine.evaluate(facts, current_year=2026)
    assert verdict["status"] == engine.UNCERTAIN, (
        f"Expected uncertain for no-evidence item, got {verdict['status']}"
    )
    assert "nessuna evidenza testuale" in verdict["reason"].lower() or "stima visiva pura" in verdict["reason"].lower(), (
        f"Expected no-evidence reason, got: {verdict['reason']}"
    )


def test_no_text_high_confidence_stays_public_domain():
    """When both text fields are empty BUT vlm_confidence is high, the no-evidence guard
    should NOT trigger — the year may be from another source (e.g., metadata).
    """
    facts = _item(
        reference_year=1910,
        year_source="visual_estimate",
        visible_text="",
        visible_text_translation="",
        vlm_confidence="high",
        has_identifiable_people=False,
    )
    verdict = engine.evaluate(facts, current_year=2026)
    # With high confidence, the no-evidence guard does not apply
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["pd_year"] == 1980


def test_real_text_low_confidence_can_still_be_pd():
    """When there IS real text but vlm_confidence is low, the item can still reach
    public_domain (the no-evidence guard only applies when BOTH text fields are empty).
    
    This is the boundary case: low confidence alone does not block PD if there is evidence.
    """
    facts = _item(
        reference_year=1910,
        year_source="document",  # Use document source, not visual_estimate
        visible_text="Ritratto fotografico del 1910",
        visible_text_translation="",
        vlm_confidence="low",
        photo_classification="artistic",
        is_anonymous=False,
        author="Test Author",
        author_death_year=1900,  # Death 1900 + 70 = 1970, well outside margin
        has_identifiable_people=False,
    )
    verdict = engine.evaluate(facts, current_year=2026)
    # Has text evidence and year from document, so no-evidence guard does not apply
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    # Confidence is low because of vlm_confidence
    assert verdict["confidence"] == "low"


def test_no_text_artistic_photo_becomes_uncertain():
    """The no-evidence guard also applies to artistic photos (Art. 27 anonymous/collective path)."""
    facts = _item(
        reference_year=1920,
        year_source="visual_estimate",
        visible_text="",
        visible_text_translation="",
        vlm_confidence="low",
        photo_classification="artistic",
        is_anonymous=True,
        has_identifiable_people=False,
    )
    verdict = engine.evaluate(facts, current_year=2026)
    assert verdict["status"] == engine.UNCERTAIN, (
        f"Expected uncertain for no-evidence artistic photo, got {verdict['status']}"
    )
    assert "nessuna evidenza testuale" in verdict["reason"].lower() or "stima visiva pura" in verdict["reason"].lower(), (
        f"Expected no-evidence reason, got: {verdict['reason']}"
    )


# ============================================================================
# NEW TESTS FOR author_role FIELD (§1§ requirements)
# ============================================================================


def test_author_role_printer_emits_p123_not_p170():
    """When author_role is 'printer', emit P123 (publisher) instead of P170 (creator)."""
    from pd_checker.core import wikidata
    
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": "Sansaini - Roma",
            "author_role": "printer",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {"depicts": [], "provenance": "", "categories": []}
    
    result = wikidata.build(verdict, ctx)
    
    p170_stmt = next((s for s in result["statements"] if s["property"] == "P170"), None)
    p123_stmt = next((s for s in result["statements"] if s["property"] == "P123"), None)
    
    # P170 should be null and marked for review
    assert p170_stmt is not None
    assert p170_stmt["value"] is None
    assert p170_stmt["status"] == "da_rivedere"
    
    # P123 should exist with the printer name
    assert p123_stmt is not None, "P123 (publisher) must exist for printer role"
    assert p123_stmt["value"] == "Sansaini - Roma"
    assert p123_stmt["status"] == "da_rivedere"


def test_author_role_publisher_emits_p123_not_p170():
    """When author_role is 'publisher', emit P123 (publisher) instead of P170 (creator)."""
    from pd_checker.core import wikidata
    
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": "Edizioni Test",
            "author_role": "publisher",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {"depicts": [], "provenance": "", "categories": []}
    
    result = wikidata.build(verdict, ctx)
    
    p170_stmt = next((s for s in result["statements"] if s["property"] == "P170"), None)
    p123_stmt = next((s for s in result["statements"] if s["property"] == "P123"), None)
    
    # P170 should be null and marked for review
    assert p170_stmt is not None
    assert p170_stmt["value"] is None
    assert p170_stmt["status"] == "da_rivedere"
    
    # P123 should exist with the publisher name
    assert p123_stmt is not None, "P123 (publisher) must exist for publisher role"
    assert p123_stmt["value"] == "Edizioni Test"


def test_author_role_photographer_emits_p170():
    """When author_role is 'photographer', emit P170 (creator) normally."""
    from pd_checker.core import wikidata
    
    # The shipped wd_cache.json already contains Rafael Merry del Val → Q507540
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": "Rafael Merry del Val",
            "author_role": "photographer",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {"depicts": [], "provenance": "", "categories": []}
    
    result = wikidata.build(verdict, ctx)
    
    p170_stmt = next((s for s in result["statements"] if s["property"] == "P170"), None)
    
    # P170 should have the photographer name and QID
    assert p170_stmt is not None
    assert p170_stmt["value"] == "Rafael Merry del Val"
    assert p170_stmt["qid"] == "Q507540", f"Expected Q507540, got {p170_stmt['qid']}"


def test_author_role_illustrator_emits_neither_p170_nor_p123():
    """When author_role is 'illustrator', emit neither P170 nor P123; mark for review."""
    from pd_checker.core import wikidata
    
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": "Disegnatore Test",
            "author_role": "illustrator",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {"depicts": [], "provenance": "", "categories": []}
    
    result = wikidata.build(verdict, ctx)
    
    p170_stmt = next((s for s in result["statements"] if s["property"] == "P170"), None)
    p123_stmt = next((s for s in result["statements"] if s["property"] == "P123"), None)
    
    # P170 should be null and marked for review (illustrator is not the photographic creator)
    assert p170_stmt is not None
    assert p170_stmt["value"] is None
    assert p170_stmt["status"] == "da_rivedere"
    assert "illustratore" in p170_stmt.get("notes", [""])[0].lower()
    
    # P123 should NOT exist for illustrator
    assert p123_stmt is None, "P123 should not exist for illustrator role"


def test_author_role_absent_backward_compatible():
    """When author_role is absent (null), use marker detection (backward compatible)."""
    from pd_checker.core import wikidata
    
    # Without role, marker detection should kick in
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": "Stab. Sansaini - Roma",  # Contains "Stab." marker
            # author_role is None (absent)
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {"depicts": [], "provenance": "", "categories": []}
    
    result = wikidata.build(verdict, ctx)
    
    p170_stmt = next((s for s in result["statements"] if s["property"] == "P170"), None)
    p123_stmt = next((s for s in result["statements"] if s["property"] == "P123"), None)
    
    # Should behave like today: marker detection finds printing works
    assert p170_stmt is not None
    assert p170_stmt["value"] is None
    assert p170_stmt["status"] == "da_rivedere"
    
    assert p123_stmt is not None
    assert p123_stmt["value"] == "Stab. Sansaini - Roma"


def test_author_role_absent_no_marker_emits_p170():
    """When author_role is absent and no marker is found, emit P170 normally."""
    from pd_checker.core import wikidata
    
    # The shipped wd_cache.json already contains Rafael Merry del Val → Q507540
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "reference_year": 1932,
            "author": "Rafael Merry del Val",
            # author_role is None (absent), no marker in name
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
    }
    ctx = {"depicts": [], "provenance": "", "categories": []}
    
    result = wikidata.build(verdict, ctx)
    
    p170_stmt = next((s for s in result["statements"] if s["property"] == "P170"), None)
    
    # Should emit P170 normally (no marker found, so treat as person)
    assert p170_stmt is not None
    assert p170_stmt["value"] == "Rafael Merry del Val"
    assert p170_stmt["qid"] == "Q507540"


def test_folder_context_year_near_boundary_returns_uncertain():
    """A year backfilled from the folder context cannot decide a boundary:
    when its expiry falls within the estimate margin the verdict is uncertain."""
    facts = {"document_type": "photo", "photo_classification": "simple",
             "reference_year": 1955, "year_source": "folder_context",
             "country_of_origin": "IT", "is_anonymous": True,
             "has_identifiable_people": False,
             "visible_text": "some printed text, evidence present",
             "vlm_confidence": "high"}
    v = engine.evaluate(facts, current_year=2026)
    assert v["status"] == engine.UNCERTAIN
    assert "contesto della cartella" in (v.get("reason") or "")


def test_folder_context_year_far_boundary_stays_public_domain():
    """Folder-context years far outside the margin still allow a verdict."""
    facts = {"document_type": "photo", "photo_classification": "simple",
             "reference_year": 1920, "year_source": "folder_context",
             "country_of_origin": "IT", "is_anonymous": True,
             "has_identifiable_people": False,
             "visible_text": "some printed text, evidence present",
             "vlm_confidence": "high"}
    v = engine.evaluate(facts, current_year=2026)
    assert v["status"] == engine.PUBLIC_DOMAIN


def test_context_period_range_backfills_latest_bound():
    """A period RANGE is backfilled with its latest (most conservative) bound,
    not its earliest: a PD claim must survive the latest plausible year."""
    from pd_checker.core import context as ctx_mod
    items = [{"file": "x/1.png", "document_type": "postcard",
              "reference_year": None, "year_source": "visual_estimate"}]
    ctx_mod.apply(items, {"period": [1920, 1955], "country_of_origin": "IT"})
    assert items[0]["reference_year"] == 1955
    assert items[0]["year_source"] == "folder_context"


# ============================================================================
# TESTS FOR UPLOAD POLICY 'all' (§1§ requirements)
# ============================================================================


def test_upload_policy_all_marks_uncertain_ready():
    """all policy: uncertain items get flag='ready' but reason keeps the caveat."""
    verdict = engine.evaluate(
        _item(reference_year=None, has_identifiable_people=False),  # missing data → uncertain
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_ALL,
    )
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["upload"]["flag"] == engine.UPLOAD_READY
    assert verdict["upload_policy"] == engine.UPLOAD_POLICY_ALL
    # Reason must keep the caveat about uncertainty
    assert "Politica 'all'" in verdict["upload"]["reason"]
    assert "verdetto incerto" in verdict["upload"]["reason"]
    # The invariant: uncertain items still get NO PD templates
    assert verdict["commons_templates"] == []


def test_upload_policy_all_marks_pd_with_blockers_ready():
    """all policy: public_domain items with blockers get flag='ready' but reason lists blockers."""
    verdict = engine.evaluate(
        _item(
            reference_year=1910,
            has_identifiable_people=True,  # portrait blocker present
        ),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_ALL,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["confidence"] == "high"
    assert verdict["upload"]["flag"] == engine.UPLOAD_READY
    assert verdict["upload_policy"] == engine.UPLOAD_POLICY_ALL
    # Reason must list the blockers
    assert "Politica 'all'" in verdict["upload"]["reason"]
    assert "rimangono i blocchi" in verdict["upload"]["reason"]
    assert "non-copyright" in verdict["upload"]["reason"]


def test_upload_policy_all_clean_item_keeps_normal_reason():
    """all policy: clean public_domain items keep their normal reason."""
    verdict = engine.evaluate(
        _item(
            reference_year=1910,
            has_identifiable_people=False,  # no blockers
        ),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_ALL,
    )
    assert verdict["status"] == engine.PUBLIC_DOMAIN
    assert verdict["upload"]["flag"] == engine.UPLOAD_READY
    # Clean items keep the normal reason (no caveat needed)
    assert verdict["upload"]["reason"] == "In pubblico dominio, nessun vincolo: candidato per l'import manuale."


def test_upload_policy_all_uncertain_with_missing_data():
    """all policy: uncertain items with missing data get ready but reason keeps caveat."""
    verdict = engine.evaluate(
        _item(
            reference_year=None,  # missing data
            has_identifiable_people=False,
        ),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_ALL,
    )
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["upload"]["flag"] == engine.UPLOAD_READY
    assert "Politica 'all'" in verdict["upload"]["reason"]
    assert "verdetto incerto" in verdict["upload"]["reason"]
    # The invariant: uncertain items still get NO PD templates
    assert verdict["commons_templates"] == []


def test_upload_policy_all_uncertain_stays_template_less():
    """all policy: uncertain items remain template-less (the invariant)."""
    verdict = engine.evaluate(
        _item(
            country_of_origin="VA",
            is_vatican_asset=True,
            is_anonymous=False,
            author="X",
            author_death_year=1900,
        ),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_ALL,
    )
    assert verdict["status"] == engine.UNCERTAIN
    assert verdict["upload"]["flag"] == engine.UPLOAD_READY
    # The invariant: uncertain items still get NO PD templates even under 'all'
    assert verdict["commons_templates"] == []
    assert any("non emessi" in note for note in verdict["notes"])


def test_upload_policy_strict_unchanged():
    """Regression: strict policy behaviour unchanged."""
    # Clean item → ready
    verdict_clean = engine.evaluate(
        _item(reference_year=1910, has_identifiable_people=False),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_STRICT,
    )
    assert verdict_clean["upload"]["flag"] == engine.UPLOAD_READY
    
    # Item with blocker → review
    verdict_blocker = engine.evaluate(
        _item(reference_year=1910, has_identifiable_people=True),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_STRICT,
    )
    assert verdict_blocker["upload"]["flag"] == engine.UPLOAD_REVIEW


def test_upload_policy_pd_only_unchanged():
    """Regression: pd-only policy behaviour unchanged."""
    # Item with non-copyright blocker → ready (blockers ignored)
    verdict_blocker = engine.evaluate(
        _item(reference_year=1910, has_identifiable_people=True),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_PD_ONLY,
    )
    assert verdict_blocker["upload"]["flag"] == engine.UPLOAD_READY
    
    # Item with missing data → review (missing data still blocks)
    verdict_missing = engine.evaluate(
        _item(reference_year=None, has_identifiable_people=False),
        current_year=2026,
        upload_policy=engine.UPLOAD_POLICY_PD_ONLY,
    )
    assert verdict_missing["upload"]["flag"] == engine.UPLOAD_REVIEW
