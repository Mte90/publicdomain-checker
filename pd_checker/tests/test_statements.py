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
