"""OCR transcription of material folder images via a chat-completions OCR model.

The endpoint is OpenAI-compatible and configured via environment only
(credentials are never argv and never printed):

- PD_OCR_API_KEY              required (falls back to REGOLOAI_OPENCODE_API_KEY)
- PD_OCR_BASE_URL             default "https://api.regolo.ai/v1"
- PD_OCR_MODEL                default "deepseek-ocr-2"

``ocr <folder> [--file NAME]... [--apply]`` transcribes every image claimed in
facts.json (or only the named, claimed ones). With ``--apply`` each side's
transcription is written into the owning item's ``ocr_texts`` map in
facts.json, keyed by image basename; re-runs overwrite per file (idempotent).

Transcriptions are review evidence: the deterministic engine never treats them
as legal facts. A year that only OCR surfaces is promoted by the human via
reference_year / date_evidence (see SKILL.md).
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.regolo.ai/v1"
DEFAULT_MODEL = "deepseek-ocr-2"
_MAX_ATTEMPTS = 3

_PROMPT = (
    "Trascrivi integralmente tutto il testo visibile in questo documento, "
    "nell'ordine di lettura dall'alto al basso e da sinistra a destra, "
    "nella lingua originale. Separa le righe con il carattere '|'. "
    "Segna i passaggi illeggibili come [illegibile]. "
    "Non tradurre e non commentare: restituisci solo la trascrizione."
)


def api_key_from_env() -> str | None:
    return os.environ.get("PD_OCR_API_KEY") or os.environ.get("REGOLOAI_OPENCODE_API_KEY")


def _post(payload: dict, api_key: str, base_url: str, timeout: int) -> dict:
    """One POST to {base_url}/chat/completions, retrying 429s honoring Retry-After."""
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < _MAX_ATTEMPTS:
                retry_after = (error.headers or {}).get("Retry-After", "")
                wait = int(retry_after) if retry_after.lstrip("+").isdigit() else 10 * attempt
                time.sleep(min(wait, 60))
                continue
            detail = error.read().decode("utf-8", "replace")[:400]
            raise RuntimeError(f"HTTP {error.code} da {url}: {detail}") from None
        except urllib.error.URLError as error:
            raise RuntimeError(f"errore di rete verso {url}: {error.reason}") from None
    raise RuntimeError(f"richiesta OCR fallita dopo {_MAX_ATTEMPTS} tentativi")


def _payload(image_path: Path, model: str) -> dict:
    ext = image_path.suffix.lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}.get(ext, "image/png")
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                    {"type": "text", "text": _PROMPT},
                ],
            }
        ],
    }


def _transcription(response: dict) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("risposta OCR senza 'choices'")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("risposta OCR senza testo")
    return content.strip()


def _claims(folder: Path) -> tuple[list, Path]:
    facts_path = next(
        (p for p in (folder / "facts.json", folder / "pd_data" / "facts.json") if p.exists()),
        None,
    )
    if facts_path is None:
        raise SystemExit(f"[ocr] manca facts.json in {folder} o {folder / 'pd_data'}")
    items = json.loads(facts_path.read_text(encoding="utf-8"))
    return items, facts_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="OCR transcription of the folder's scans (default deepseek-ocr-2 via regolo, env-configured)."
    )
    parser.add_argument("folder", help="Material folder (needs facts.json)")
    parser.add_argument(
        "--file",
        dest="files",
        action="append",
        metavar="NAME",
        help="Transcribe only this image basename (repeatable; must be claimed by an item)",
    )
    parser.add_argument("--apply", action="store_true", help="Write the transcriptions into facts.json (ocr_texts per item)")
    parser.add_argument("--timeout", type=int, default=600, help="Per-request timeout in seconds (default 600)")
    args = parser.parse_args(argv)

    api_key = api_key_from_env()
    if not api_key:
        print("[ocr] manca la chiave API: imposta PD_OCR_API_KEY (o REGOLOAI_OPENCODE_API_KEY)", file=sys.stderr)
        return 2
    base_url = os.environ.get("PD_OCR_BASE_URL", DEFAULT_BASE_URL)
    model = os.environ.get("PD_OCR_MODEL", DEFAULT_MODEL)

    folder = Path(args.folder)
    items, facts_path = _claims(folder)

    claimed: dict[str, int] = {}
    for index, item in enumerate(items):
        for img in item.get("images", []):
            claimed[img.split("/")[-1]] = index
    if not claimed:
        print("[ocr] nessuna immagine reclamata in facts.json", file=sys.stderr)
        return 1

    if args.files:
        unknown = [name for name in args.files if name not in claimed]
        if unknown:
            print(f"[ocr] file non reclamati dal pacchetto: {', '.join(unknown)}", file=sys.stderr)
            return 1
        ordered: list[str] = []
        seen: set[str] = set()
        for name in args.files:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    else:
        ordered = sorted(claimed)

    print(f"== OCR {folder.name}: {len(ordered)} immagine/i (modello {model}) ==")
    collected: dict[str, str] = {}
    failures: list[str] = []
    for name in ordered:
        image_path = folder / name
        if not image_path.exists():
            print(f"[ERRORE] {name}: file assente in {folder}")
            failures.append(name)
            continue
        print(f"[OCR] {name} …", flush=True)
        try:
            response = _post(_payload(image_path, model), api_key, base_url, args.timeout)
            text = _transcription(response)
        except RuntimeError as error:
            print(f"[ERRORE] {name}: {error}")
            failures.append(name)
            continue
        collected[name] = text
        print(f"[OK] {name}: {len(text)} caratteri")

    if args.apply and collected:
        by_item: dict[int, dict[str, str]] = {}
        for name, text in collected.items():
            by_item.setdefault(claimed[name], {})[name] = text
        for index, per_file in by_item.items():
            merged = dict(items[index].get("ocr_texts") or {})
            merged.update(per_file)
            items[index]["ocr_texts"] = merged
        facts_path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"[apply] ocr_texts scritto in {facts_path} ({len(collected)} lato/i)")
    elif collected:
        print("\n".join(f"--- {name} ---\n{text}" for name, text in collected.items()))

    if failures:
        print(f"[ocr] {len(failures)} trascrizione/i fallite: {', '.join(failures)}", file=sys.stderr)
        return 1
    return 0
