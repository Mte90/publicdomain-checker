"""{{Information}} description tests: full transcription, OCR preferred."""

from pd_checker.core import wikidata


def test_description_is_full_text_not_truncated():
    long_text = "prima riga|" + ("testo abbastanza lungo da superare i duecento caratteri del vecchio troncamento " * 5) + "fine completa"
    verdict = {
        "extracted_facts": {
            "document_type": "letter",
            "visible_text": long_text,
            "images": ["cartella/1_fronte.png"],
            "reference_year": 1921,
        }
    }
    info = wikidata._build_information(verdict, {})
    assert info["description"].startswith("prima riga / ")
    assert info["description"].endswith("fine completa")
    assert len(info["description"]) > 200


def test_ocr_transcription_preferred_over_vision():
    verdict = {
        "extracted_facts": {
            "document_type": "unknown",
            "visible_text": "trascrizione visione incompleta",
            "images": ["cartella/1_fronte.png"],
            "ocr_texts": {"1_fronte.png": "trascrizione OCR completa"},
            "reference_year": None,
        }
    }
    info = wikidata._build_information(verdict, {})
    assert info["description"] == "trascrizione OCR completa"
