from pd_checker.core import rules as engine
from pd_checker.core import catalogs

templates = catalogs


def _facts(**overrides):
    facts = {
        "file": "x.png",
        "document_type": "postcard",
        "country_of_origin": "IT",
        "vlm_confidence": "high",
        "is_anonymous": True,
        "photo_classification": "simple",
        "reference_year": None,
        "author": None,
        "author_death_year": None,
    }
    facts.update(overrides)
    return facts


def test_gb_anonymous_photo_1910_is_public_domain():
    """UK CDPA 1988 s.12(3): unknown authorship PD 70y from creation.
    
    Pre-1957 photographs' 50-year term (1911 Act s.21, preserved by Sch.1 
    para 12(2)(c)) expired 31 Dec 1960. Therefore a UK photo taken in 1910 
    with unknown author IS public domain in 2026, without needing death date.
    
    URAA: The 1956 Act 50y term expired 31/12/1960. The 1995 Regs reg.16(d) 
    did NOT revive because the work was already EEA-PD on 1/7/1995.
    Therefore {{PD-1996}} IS eligible.
    """
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1910),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    assert origin["pd_year"] == 1961  # 1910 + 50 + 1 (year-end)
    assert "{{PD-anon-70}}" in origin["commons_templates"]
    assert "{{PD-1996}}" in origin["commons_templates"]
    assert origin["missing_data"] == []
    # Verify all emitted templates are catalog-valid
    unknown = templates.unknown(origin["commons_templates"])
    assert unknown == [], f"Unknown templates: {unknown}"
    # us_status should NOT be present (never restored)
    assert "us_status" not in verdict


def test_italy_anonymous_artistic_photo_uses_anon_70_eu_not_pd_italy():
    """Italian anonymous ARTISTIC photo: PD-anon-70-EU (not PD-Italy simple-photo tag).
    URAA: {{PD-1996}} only if pd_year <= 1996. For 1950 work, pd_year=2020 > 1996,
    so PD-1996 is NOT attached and URAA note is added."""
    verdict = engine.evaluate(
        _facts(photo_classification="artistic", is_anonymous=True, reference_year=1950),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    assert "{{PD-anon-70-EU}}" in origin["commons_templates"]
    # URAA: pd_year=2020 > 1996, so PD-1996 should NOT be present
    assert "{{PD-1996}}" not in origin["commons_templates"]
    # URAA note should be present in the notes
    notes = origin.get("notes", [])
    assert any("URAA" in note for note in notes), f"Expected URAA note in {notes}"
    # PD-Italy is the simple-photo tag, should NOT be present for artistic works
    assert "{{PD-Italy}}" not in origin["commons_templates"]
    # Verify all emitted templates are catalog-valid
    unknown = templates.unknown(origin["commons_templates"])
    assert unknown == [], f"Unknown templates: {unknown}"


def test_italy_anonymous_simple_photo_keeps_pd_italy():
    """Italian anonymous SIMPLE photo: PD-Italy + PD-1996 is correct (unchanged)."""
    verdict = engine.evaluate(
        _facts(photo_classification="simple", is_anonymous=True, reference_year=1910),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    assert "{{PD-Italy}}" in origin["commons_templates"]
    assert "{{PD-1996}}" in origin["commons_templates"]
    # Verify all emitted templates are catalog-valid
    unknown = templates.unknown(origin["commons_templates"])
    assert unknown == [], f"Unknown templates: {unknown}"


def test_vatican_known_author_death_uses_pd_old_70():
    """Vatican with known author death: PD-old-70 + PD-1996 (not anonymous fallback)."""
    verdict = engine.evaluate(
        _facts(country_of_origin="VA", is_vatican_asset=True, is_anonymous=False,
               photo_classification="artistic", author="X", author_death_year=1900,
               reference_year=1910),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    assert "{{PD-old-70}}" in origin["commons_templates"]
    assert "{{PD-1996}}" in origin["commons_templates"]
    # Verify all emitted templates are catalog-valid
    unknown = templates.unknown(origin["commons_templates"])
    assert unknown == [], f"Unknown templates: {unknown}"


def test_gb_photo_life_plus_70():
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic", is_anonymous=False,
               author="John Smith", author_death_year=1950),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    assert origin["pd_year"] == 2021  # 1950 + 70 + 1 (UK year-end)
    assert verdict["country_of_origin"] == "GB"
    assert verdict["shorter_term_applied"] is True


def test_gb_unknown_author_counts_from_making():
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1960),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["pd_year"] == 2031  # 1960 + 70 + 1
    assert origin["status"] == engine.NOT_PUBLIC_DOMAIN


def test_gb_pre_1957_anonymous_is_public_domain():
    """UK pre-1957 anonymous photo: 50-year rule expired 31 Dec 1960, so PD in 2026.
    
    For a 1950 photo, the 1956 Act 50y term from publication would expire 31/12/2000.
    However, the 1995 Regs reg.16(d) revived copyright for works still EEA-protected
    on 1/7/1995. The Directive term (70y from making) means a 1950 work was EEA-protected
    on 1/7/1995 (1950+70=2020 > 1995), so it was revived to 1950+70+1=2021.
    At the URAA date (1/1/1996), the work was still UK-protected, so PD-1996 is NOT eligible.
    """
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1950),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    # The 50-year rule from publication still applies for pd_year computation
    assert origin["pd_year"] == 2001  # 1950 + 50 + 1
    # But PD-1996 should NOT be present (still UK-protected at URAA date)
    assert "{{PD-1996}}" not in origin["commons_templates"]
    # us_status should be present: 1950 + 96 = 2046
    assert "us_status" in verdict
    assert verdict["us_status"]["restored_until"] == 2046


def test_gb_company_holder_does_not_change_term():
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic", is_anonymous=False,
               rights_holder="company", author="Jane Roe", author_death_year=1950),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["pd_year"] == 2021
    assert any("s.11" in n for n in origin["notes"])


def test_italy_corporate_collective_counts_from_publication():
    verdict = engine.evaluate(
        _facts(photo_classification="artistic", is_anonymous=True, reference_year=1950),
        current_year=2026,
    )
    assert verdict["jurisdictions"]["italy"]["pd_year"] == 2021
    assert verdict["status"] == engine.PUBLIC_DOMAIN


def test_vatican_holy_see_counts_from_publication():
    verdict = engine.evaluate(
        _facts(country_of_origin="VA", photo_classification="artistic",
               rights_holder="holy_see", reference_year=1950),
        current_year=2026,
    )
    assert verdict["country_of_origin"] == "VA"
    assert verdict["jurisdictions"]["origin"]["pd_year"] == 2021


def test_vatican_falls_back_to_italian_law():
    verdict = engine.evaluate(
        _facts(country_of_origin="VA", photo_classification="simple", reference_year=1910),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    # Simple photo <=2004: operative 20-year no-revival term (1910 + 20), not the
    # post-reform 70-year term (1910 + 70).
    assert origin["pd_year"] == 1930
    assert any("CXCVII" in basis for basis in origin["legal_basis"])


def test_shorter_term_uses_origin_when_shorter():
    # GB: 50-year pre-1957 rule would give 2001; Italy alone would give 2021 for
    # an anonymous work. The shorter-term cap must keep Italy at the origin term.
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1950),
        current_year=2026,
    )
    italy = verdict["jurisdictions"]["italy"]
    assert italy["pd_year"] == min(verdict["jurisdictions"]["origin"].get("pd_year") or 9999,
                                   italy["pd_year"])


def test_gb_anonymous_photo_1933_uraa_restored_us_copyright():
    """GB anonymous photo taken 1933: still UK-protected on 1/1/1996 (1926-1956 trap).
    
    The 1956 Act 50y term from first publication was still running (or revived to
    making+70 by the 1995 Regs). Therefore {{PD-1996}} is NOT eligible and US
    copyright is restored until 31/12/2028 (1933+95), PD USA from 1/1/2029.
    """
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1933),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    # UK pd_year is still 1984 (1933+50+1) under the 50-year rule
    assert origin["pd_year"] == 1984
    # PD-1996 should NOT be present (still UK-protected at URAA date)
    assert "{{PD-1996}}" not in origin["commons_templates"]
    # us_status should be present with restored_until = 1933 + 96 = 2029
    assert "us_status" in verdict
    assert verdict["us_status"]["restored_until"] == 2029
    # URAA note should mention US restoration
    notes = origin.get("notes", [])
    assert any("URAA" in note and "ripristinato" in note for note in notes)


def test_gb_anonymous_photo_1910_pd1996_eligible():
    """GB anonymous photo taken 1910: UK-PD at URAA date (expired 31/12/1960, not revived).
    
    The 1956 Act 50y term expired 31/12/1960. The 1995 Regs reg.16(d) did NOT revive
    because the work was already EEA-PD on 1/7/1995. Therefore {{PD-1996}} IS eligible.
    """
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1910),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    assert origin["pd_year"] == 1961  # 1910 + 50 + 1
    # PD-1996 should be present
    assert "{{PD-1996}}" in origin["commons_templates"]
    # us_status should NOT be present (never restored)
    assert "us_status" not in verdict


def test_gb_anonymous_photo_1925_boundary_pd1996_present():
    """GB anonymous photo taken 1925: boundary case — PD-1996 eligible (made <= 1925)."""
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1925),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    # PD-1996 should be present (1925 <= 1925 boundary)
    assert "{{PD-1996}}" in origin["commons_templates"]
    assert "us_status" not in verdict


def test_gb_anonymous_photo_1926_boundary_pd1996_absent():
    """GB anonymous photo taken 1926: boundary case — PD-1996 NOT eligible (made > 1925)."""
    verdict = engine.evaluate(
        _facts(country_of_origin="GB", photo_classification="artistic",
               is_anonymous=True, reference_year=1926),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    # PD-1996 should NOT be present (1926 > 1925 boundary)
    assert "{{PD-1996}}" not in origin["commons_templates"]
    # us_status should be present: 1926 + 96 = 2022
    assert "us_status" in verdict
    assert verdict["us_status"]["restored_until"] == 2022


def test_italy_simple_photo_1932_regression_pd1996_kept():
    """Italian simple photo 1932: regression test — PD-1996 still present (IT rule unchanged)."""
    verdict = engine.evaluate(
        _facts(country_of_origin="IT", photo_classification="simple",
               is_anonymous=True, reference_year=1932),
        current_year=2026,
    )
    origin = verdict["jurisdictions"]["origin"]
    assert origin["status"] == engine.PUBLIC_DOMAIN
    # PD-1996 should be present (IT simple photo 20y term: 1932+20=1952 <= 1996)
    assert "{{PD-1996}}" in origin["commons_templates"]
    assert "us_status" not in verdict
