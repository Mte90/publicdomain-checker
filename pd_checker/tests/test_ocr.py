"""OCR subcommand tests — network mocked, no real API calls."""

import json

import pytest

from pd_checker.core import ocr


def _fake_response(text):
    return {"choices": [{"message": {"content": text}}]}


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "pd_data").mkdir()
    (tmp_path / "a.png").write_bytes(b"\x89PNG fake image bytes")
    facts = [
        {
            "file": tmp_path.name + "/a.png",
            "images": [tmp_path.name + "/a.png"],
        }
    ]
    (tmp_path / "pd_data" / "facts.json").write_text(json.dumps(facts), encoding="utf-8")
    return tmp_path


def test_missing_api_key_exits_2(folder, monkeypatch):
    monkeypatch.delenv("PD_OCR_API_KEY", raising=False)
    monkeypatch.delenv("REGOLOAI_OPENCODE_API_KEY", raising=False)
    assert ocr.main([str(folder)]) == 2


def test_apply_writes_ocr_texts(folder, monkeypatch):
    monkeypatch.setenv("PD_OCR_API_KEY", "test-key")

    def fake_post(payload, api_key, base_url, timeout):
        assert payload["model"] == ocr.DEFAULT_MODEL
        assert "data:image/png;base64," in payload["messages"][0]["content"][0]["image_url"]["url"]
        return _fake_response("riga uno|riga due")

    monkeypatch.setattr(ocr, "_post", fake_post)
    assert ocr.main([str(folder), "--apply"]) == 0
    facts = json.loads((folder / "pd_data" / "facts.json").read_text(encoding="utf-8"))
    assert facts[0]["ocr_texts"] == {"a.png": "riga uno|riga due"}


def test_unclaimed_file_refused(folder, monkeypatch):
    monkeypatch.setenv("PD_OCR_API_KEY", "test-key")
    assert ocr.main([str(folder), "--file", "ghost.png"]) == 1


def test_failure_reported_and_nothing_applied(folder, monkeypatch):
    monkeypatch.setenv("PD_OCR_API_KEY", "test-key")

    def fake_post(payload, api_key, base_url, timeout):
        raise RuntimeError("HTTP 413 da test: payload troppo grande")

    monkeypatch.setattr(ocr, "_post", fake_post)
    assert ocr.main([str(folder), "--apply"]) == 1
    facts = json.loads((folder / "pd_data" / "facts.json").read_text(encoding="utf-8"))
    assert "ocr_texts" not in facts[0]
