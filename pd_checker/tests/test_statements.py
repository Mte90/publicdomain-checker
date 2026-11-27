"""Wikidata statement building — the P180 mention miner."""

from pd_checker.core import catalogs, wikidata


def test_p180_miner_skips_stopwords(monkeypatch):
    """A stale cache entry for an ordinary word must never become a 'depicts' statement."""
    def fake_cache():
        return {
            "note": "test",
            "names": {
                "circa": {"qid": "Q5727902", "label_it": "circa", "label_en": "circa", "result": "ok", "checked": "2026-11-20"},
                "Rieti": {"qid": "Q13396", "label_it": "Rieti", "label_en": "Rieti", "result": "ok", "checked": "2026-11-20"},
            },
        }

    monkeypatch.setattr(catalogs, "_load_wd_cache", fake_cache)
    verdict = {
        "file": "varie/2_fronte.png",
        "status": "public_domain",
        "extracted_facts": {
            "document_type": "photo",
            "visible_text": "fotografia con circa 100 ritratti a Rieti",
            "images": ["varie/2_fronte.png"],
            "reference_year": 1936,
        },
    }
    block = wikidata.build(verdict, {"depicts": []})
    p180_values = [s["value"] for s in block["statements"] if s["property"] == "P180"]
    assert "circa" not in p180_values
    assert "Rieti" in p180_values


def test_p571_manuscript_date_is_documentary():
    """A date WRITTEN on the document (manuscript) is THE date: status ok, no circa, no review ask."""
    from pd_checker.core import wikidata

    verdict = {
        "extracted_facts": {
            "document_type": "manuscript",
            "reference_year": 1918,
            "year_source": "manuscript",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy-EdictGov}}"],
        "upload": {"flag": "ready"},
    }
    result = wikidata.build(verdict, {"categories": ["Category:Identity documents of Italy"]})
    p571 = next(s for s in result["statements"] if s["property"] == "P571")
    assert p571["status"] == "ok"
    assert p571["time"] == "+1918-00-00T00:00:00Z"
    assert p571["precision"] == 9
    assert "qualifiers" not in p571
    assert "reason" not in p571


def test_p180_miner_honors_depicts_exclude(monkeypatch):
    """Mentions on the address side are not subjects: depicts_exclude suppresses them."""
    def fake_cache():
        return {
            "note": "test",
            "names": {
                "Rieti": {"qid": "Q13396", "label_it": "Rieti", "label_en": "Rieti", "result": "ok", "checked": "2026-11-27"},
                "Perugia": {"qid": "Q3437", "label_it": "Perugia", "label_en": "Perugia", "result": "ok", "checked": "2026-11-27"},
            },
        }

    monkeypatch.setattr(catalogs, "_load_wd_cache", fake_cache)
    verdict = {
        "file": "varie/7_fronte.png",
        "status": "public_domain",
        "extracted_facts": {
            "document_type": "postcard",
            "visible_text": "cartolina patriottica, indirizzo: Gent. Sig. Marchese Vecchiarelli, Rieti (Perugia)",
            "images": ["varie/7_fronte.png"],
            "reference_year": 1914,
            "depicts_exclude": ["Rieti", "Perugia"],
        },
    }
    block = wikidata.build(verdict, {"depicts": []})
    p180_values = [s["value"] for s in block["statements"] if s["property"] == "P180"]
    assert p180_values == []


def test_description_note_prepended_to_information():
    """Facts description_note is editorial framing prepended to the verbatim transcription."""
    from pd_checker.core import wikidata

    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "visible_text": "SALT | PEPPER",
            "images": ["Varie/x.png"],
            "description_note": "Nota editoriale dell'archivio",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
        "upload": {"flag": "ready"},
    }
    block = wikidata.build(verdict, {"categories": [], "provenance": "Archivio di Daniele Scasciafratte"})
    desc = block["information"]["description"]
    assert desc == "Nota editoriale dell'archivio. SALT  /  PEPPER"


def test_item_depicts_before_context_and_dedup(monkeypatch):
    """Facts per-item depicts (image-identified subjects) come first and dedup with ctx by QID."""
    from pd_checker.core import catalogs, wikidata

    monkeypatch.setattr(catalogs, "lookup", lambda name: "Q3077776" if "Basilica" in name else ("Q20494" if name == "Norcia" else None))
    verdict = {
        "extracted_facts": {
            "document_type": "photo",
            "visible_text": "[nessun testo]",
            "images": ["Varie/1_fronte.png"],
            "depicts": [{"label": "Basilica di San Benedetto (Norcia)", "qid": "Q3077776"}],
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
        "upload": {"flag": "ready"},
    }
    ctx = {"depicts": [{"label": "Norcia"}], "categories": []}
    block = wikidata.build(verdict, ctx)
    p180 = [s for s in block["statements"] if s["property"] == "P180"]
    values = [(s["value"], s["source"]) for s in p180]
    assert values[0] == ("Basilica di San Benedetto (Norcia)", "extracted_facts.depicts")
    assert any(v == ("Norcia", "context.depicts") for v in values)
    assert len({s["qid"] for s in p180}) == len(p180)


def test_language_emission_p407(monkeypatch):
    """P407 (language of the text) fires for Italian items and stays absent otherwise."""
    from pd_checker.core import catalogs, wikidata

    monkeypatch.setattr(catalogs, "lookup", lambda name: None)
    verdict = {
        "extracted_facts": {
            "document_type": "postcard",
            "visible_text": "",
            "images": ["Varie/x.png"],
            "visible_text_lang": "it",
        },
        "status": "public_domain",
        "commons_templates": ["{{PD-Italy}}"],
        "upload": {"flag": "ready"},
    }
    block = wikidata.build(verdict, {"categories": []})
    p407 = [s for s in block["statements"] if s["property"] == "P407"]
    assert p407 and p407[0]["qid"] == "Q652"

    verdict["extracted_facts"]["visible_text_lang"] = None
    block2 = wikidata.build(verdict, {"categories": []})
    assert not [s for s in block2["statements"] if s["property"] == "P407"]
