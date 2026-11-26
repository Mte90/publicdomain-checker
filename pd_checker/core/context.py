#!/usr/bin/env python3
"""Folder-level context support for PD checker.

Loads per-folder context (provenance, rights-holder, country, period) and
applies it to facts/verdicts, emitting folder-level suggestions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path



def load(folder: str) -> dict | None:
    """Read context from <folder>/pubdom.json key 'context', falling back to <folder>/context.json or <folder>/context.md.

    Recognised keys:
      - folder, collection, provenance, rights_holder, country_of_origin, upload_prefix
      - period: either [from, to] or a single year
      - notes
      - known_authors: map name -> death year

    Returns None if no context file found or malformed.
    """
    folder_path = Path(folder)

    # Try pubdom.json first (consolidated portable artifact)
    pubdom_path = folder_path / "pubdom.json"
    if pubdom_path.exists():
        try:
            pubdom = json.loads(pubdom_path.read_text(encoding="utf-8"))
            if isinstance(pubdom, dict) and "context" in pubdom:
                ctx = pubdom["context"]
                if isinstance(ctx, dict):
                    return ctx
        except (json.JSONDecodeError, OSError):
            pass

    # Try pd_data/context.json (preferred location)
    json_path = folder_path / "pd_data" / "context.json"
    if json_path.exists():
        try:
            ctx = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(ctx, dict):
                return ctx
        except (json.JSONDecodeError, OSError):
            pass

    # Try context.json (legacy fallback in folder root)
    json_path = folder_path / "context.json"
    if json_path.exists():
        try:
            ctx = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(ctx, dict):
                return ctx
        except (json.JSONDecodeError, OSError):
            pass

    # Try context.md (legacy fallback)
    md_path = folder_path / "context.md"
    if md_path.exists():
        try:
            ctx: dict[str, object] = {}
            content = md_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip().lower()
                    value = value.strip()
                    # Parse known_authors as JSON if it looks like a dict
                    if key == "known_authors" and value.startswith("{"):
                        try:
                            ctx[key] = json.loads(value)
                        except json.JSONDecodeError:
                            ctx[key] = value
                    # Parse period as [from, to] or single year
                    elif key == "period":
                        if value.startswith("[") and value.endswith("]"):
                            try:
                                ctx[key] = json.loads(value)
                            except json.JSONDecodeError:
                                ctx[key] = value
                        else:
                            try:
                                ctx[key] = int(value)
                            except ValueError:
                                ctx[key] = value
                    else:
                        ctx[key] = value
            if ctx:
                return ctx
        except OSError:
            pass

    return None


def apply(items: list[dict], ctx: dict) -> list[dict]:
    """Apply context to items, filling missing fields.

    For each fact:
      - Fill missing country_of_origin from ctx
      - Fill missing rights_holder from ctx
      - If reference_year is null and ctx period is a single year, set it
        and set year_source to "folder_context"
      - If the period is a range, backfill with its LATEST bound: with no
        item-level date the public-domain claim must survive the most
        conservative production year, not the earliest one
      - If author is present in ctx known_authors and author_death_year is null,
        fill it from ctx

    Returns the same list (mutated).
    """
    for item in items:
        if not isinstance(item, dict):
            continue

        # Fill country_of_origin
        if not item.get("country_of_origin") and ctx.get("country_of_origin"):
            item["country_of_origin"] = ctx["country_of_origin"]

        # Fill rights_holder
        if not item.get("rights_holder") and ctx.get("rights_holder"):
            item["rights_holder"] = ctx["rights_holder"]

        # Fill reference_year from context period: a single year is used as-is;
        # a range is backfilled with its LATEST bound (most conservative year).
        if item.get("reference_year") is None and ctx.get("period"):
            period = ctx["period"]
            if isinstance(period, int):
                item["reference_year"] = period
                item["year_source"] = "folder_context"
            elif isinstance(period, list) and len(period) == 2:
                try:
                    item["reference_year"] = int(period[1])
                    item["year_source"] = "folder_context"
                except (ValueError, TypeError):
                    pass

        # Fill author_death_year from known_authors
        author = item.get("author")
        if author and item.get("author_death_year") is None:
            known_authors = ctx.get("known_authors", {})
            if isinstance(known_authors, dict) and author in known_authors:
                death_year = known_authors[author]
                if death_year is not None:
                    try:
                        item["author_death_year"] = int(death_year)
                    except (ValueError, TypeError):
                        pass

    return items


def suggestions(ctx: dict, verdicts: dict) -> list[str]:
    """Generate folder-level suggestions in Italian.

    Suggests:
      - Rights-holder guidance when context has a rights_holder
      - Period mismatches (items whose reference_year falls outside ctx period)
      - Unknown-author items
      - Nudge when context is missing key fields
    """
    suggestions_list: list[str] = []

    if not ctx:
        suggestions_list.append("Nessun contesto caricato: aggiungi context.json o context.md alla cartella")
        return suggestions_list

    # Rights-holder guidance
    if ctx.get("rights_holder"):
        holder = ctx["rights_holder"]
        suggestions_list.append(f"Cartella attribuita a {holder}: verifica chi detiene i diritti")

    # Period mismatches
    period = ctx.get("period")
    if period:
        items = verdicts.get("items", [])
        if isinstance(period, int):
            # Single year - check for mismatches
            for item in items:
                # Look in extracted_facts first, then top-level
                ref_year = item.get("extracted_facts", {}).get("reference_year") or item.get("reference_year")
                if ref_year is not None and ref_year != period:
                    suggestions_list.append(
                        f"Disallineamento periodo: {item.get('file', 'unknown')} ha anno {ref_year}, "
                        f"ma il contesto indica {period}"
                    )
        elif isinstance(period, list) and len(period) == 2:
            # Range [from, to]
            try:
                from_year = int(period[0])
                to_year = int(period[1])
                for item in items:
                    # Look in extracted_facts first, then top-level
                    ref_year = item.get("extracted_facts", {}).get("reference_year") or item.get("reference_year")
                    if ref_year is not None and (ref_year < from_year or ref_year > to_year):
                        suggestions_list.append(
                            f"Disallineamento periodo: {item.get('file', 'unknown')} ha anno {ref_year}, "
                            f"fuori dall'intervallo {from_year}-{to_year}"
                        )
            except (ValueError, TypeError):
                pass

    # Unknown-author items
    known_authors = ctx.get("known_authors", {})
    if isinstance(known_authors, dict) and known_authors:
        items = verdicts.get("items", [])
        for item in items:
            # Look in extracted_facts first, then top-level
            author = item.get("extracted_facts", {}).get("author") or item.get("author")
            if author and author not in known_authors:
                suggestions_list.append(
                    f"Autore sconosciuto: {author} non presente in known_authors del contesto"
                )

    # Missing data nudges
    if not ctx.get("provenance"):
        suggestions_list.append("Mancano informazioni sulla provenienza nel contesto")
    if not ctx.get("country_of_origin"):
        suggestions_list.append("Mancano informazioni sul paese di origine nel contesto")
    if not ctx.get("period"):
        suggestions_list.append("Mancano informazioni sul periodo nel contesto")
    # upload_prefix suggestion (optional but recommended for Commons uploads)
    if not ctx.get("upload_prefix"):
        suggestions_list.append("Prefisso nomi file non impostato: aggiungi 'upload_prefix' al contesto per nomi Commons coerenti (es. 'Concorso-Grano-1933')")

    # Verdict-derived nudges: what a reviewer should look at next.
    items = verdicts.get("items", [])
    uncertain = [i for i in items if i.get("status") == "uncertain"]
    if uncertain:
        suggestions_list.append(
            f"{len(uncertain)} item incerti: risolvere i dati mancanti prima di pubblicare "
            "(es. data di morte dell'autore)."
        )
    blocked = [i for i in items if i.get("blockers_non_copyright")]
    if blocked:
        suggestions_list.append(
            f"{len(blocked)} item con vincoli non-copyright (diritto all'immagine Artt. 96-97 / "
            "privacy): revisione umana necessaria anche se l'opera e' in pubblico dominio."
        )
    # Check for simple photo templates that need review
    if any(
        any("PD-Italy" in (t or "") for t in i.get("commons_templates", []))
        and any("PD-1996" in (t or "") for t in i.get("commons_templates", []))
        for i in items
    ):
        suggestions_list.append(
            "Confermare che le foto semplici italiane siano effettivamente PD (70 anni dalla produzione, L. 182/2025)."
        )

    return suggestions_list


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Apply folder context to facts or generate suggestions."
    )
    parser.add_argument("folder", help="Folder containing context.json or context.md")
    parser.add_argument(
        "--apply",
        metavar="FACTS",
        help="Apply context to facts JSON and write enriched output",
    )
    parser.add_argument(
        "--suggestions",
        metavar="VERDICTS",
        help="Generate suggestions from context and verdicts",
    )
    parser.add_argument("-o", "--output", help="Output file path (optional for suggestions)")

    args = parser.parse_args(argv)

    # Load context
    ctx = load(args.folder)

    # Apply mode
    if args.apply:
        facts_path = Path(args.apply)
        if not facts_path.exists():
            print(f"Error: facts file not found: {args.apply}", file=sys.stderr)
            return 1

        try:
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON in {args.apply}: {e}", file=sys.stderr)
            return 1

        if not isinstance(facts, list):
            print(f"Error: facts file must contain a JSON array", file=sys.stderr)
            return 1

        enriched = apply(facts, ctx or {})

        # Write output
        if args.output:
            output_path = Path(args.output)
            output_path.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            print(json.dumps(enriched, indent=2, ensure_ascii=False))

        return 0

    # Suggestions mode
    if args.suggestions:
        verdicts_path = Path(args.suggestions)
        if not verdicts_path.exists():
            print(f"Error: verdicts file not found: {args.suggestions}", file=sys.stderr)
            return 1

        try:
            verdicts = json.loads(verdicts_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"Error: invalid JSON in {args.suggestions}: {e}", file=sys.stderr)
            return 1

        sugg = suggestions(ctx or {}, verdicts)

        if args.output:
            output_path = Path(args.output)
            output_path.write_text(json.dumps(sugg, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            for s in sugg:
                print(s)

        return 0

    # No mode specified
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())


# =============================================================================
# COUNTRY SUGGESTION MODULE (merged from country_suggest.py)
# =============================================================================
# Public functions: suggest, suggest_main (aliased as main for CLI)
# Importers: rules.py calls country_suggest.suggest()
# Tests: pd_checker/tests/test_country_suggest.py

import re
from typing import TypedDict


class Candidate(TypedDict):
    country: str
    reason: str
    confidence: str


# Language → list of (country, reason)
_LANGUAGE_MAP: dict[str, list[tuple[str, str]]] = {
    "it": [("IT", "lingua italiana")],
    "en": [("GB", "lingua inglese"), ("US", "lingua inglese")],
    "la": [("VA", "lingua latina"), ("IT", "lingua latina")],
    "fr": [("FR", "lingua francese")],
    "de": [("DE", "lingua tedesca")],
    "es": [("ES", "lingua spagnola")],
    "pt": [("PT", "lingua portoghese")],
    "nl": [("NL", "lingua olandese")],
}

# Place name → country (case-insensitive, accent-insensitive matching)
_PLACE_MAP: dict[str, str] = {
    # Italy
    "roma": "IT",
    "perugia": "IT",
    "milano": "IT",
    "napoli": "IT",
    "firenze": "IT",
    "torino": "IT",
    "bologna": "IT",
    "venezia": "IT",
    "palermo": "IT",
    "genova": "IT",
    "italia": "IT",
    "italy": "IT",
    # UK
    "londra": "GB",
    "london": "GB",
    "edinburgh": "GB",
    "manchester": "GB",
    "birmingham": "GB",
    "united kingdom": "GB",
    "regno unito": "GB",
    # Vatican
    "città del vaticano": "VA",
    "citta del vaticano": "VA",
    "vatican": "VA",
    "vaticano": "VA",
    "santa sede": "VA",
    "holy see": "VA",
    # US
    "new york": "US",
    "chicago": "US",
    "boston": "US",
    "washington": "US",
    "los angeles": "US",
    "united states": "US",
    "usa": "US",
    "americhe": "US",
    # France
    "paris": "FR",
    "parigi": "FR",
    # Germany
    "münchen": "DE",
    "munich": "DE",
    "berlin": "DE",
    "germania": "DE",
    "germany": "DE",
    # Spain
    "madrid": "ES",
    "barcelona": "ES",
    "spagna": "ES",
    "spain": "ES",
    # Portugal
    "lisbon": "PT",
    "lisbona": "PT",
    "portogallo": "PT",
    "portugal": "PT",
    # Netherlands
    "amsterdam": "NL",
    "oland": "NL",
    "netherlands": "NL",
}


def _normalize_text(text: str) -> str:
    """Normalize text for case-insensitive, accent-insensitive matching."""
    # Lowercase
    text = text.lower()
    # Remove accents (basic normalization)
    text = re.sub(r"[àáâãäå]", "a", text)
    text = re.sub(r"[èéêë]", "e", text)
    text = re.sub(r"[ìíîï]", "i", text)
    text = re.sub(r"[òóôõö]", "o", text)
    text = re.sub(r"[ùúûü]", "u", text)
    text = re.sub(r"[ñ]", "n", text)
    text = re.sub(r"[ç]", "c", text)
    return text


def _find_places(text: str) -> list[tuple[str, str]]:
    """Find place names in text, return list of (place, country)."""
    normalized = _normalize_text(text)
    found = []
    for place, country in _PLACE_MAP.items():
        if place in normalized:
            found.append((place, country))
    return found


def suggest(
    language: str | None = None,
    text: str | None = None,
    postmark: str | None = None,
    publisher: str | None = None,
) -> list[Candidate]:
    """Suggest candidate jurisdictions.

    Args:
        language: ISO 639-1 language code of the material's text.
        text: Visible text from the material.
        postmark: Postmark string (city/date).
        publisher: Publisher string.

    Returns:
        List of candidates sorted by confidence (high > medium > low),
        then by country code.
    """
    candidates: dict[str, list[str]] = {}  # country -> list of reasons
    lang_countries: set[str] = set()

    # Step 1: language hint
    if language:
        lang = language.lower()[:2]
        if lang in _LANGUAGE_MAP:
            for country, reason in _LANGUAGE_MAP[lang]:
                lang_countries.add(country)
                candidates.setdefault(country, []).append(reason)

    # Step 2: place evidence from text, postmark, publisher
    all_text = " ".join(filter(None, [text, postmark, publisher]))
    places_found = _find_places(all_text)

    place_countries: set[str] = set()
    for place, country in places_found:
        place_countries.add(country)
        # Build a specific reason
        source = "testo" if text and place in _normalize_text(text) else (
            "timbro postale" if postmark and place in _normalize_text(postmark) else "editore"
        )
        reason = f"luogo rilevato ({place}) in {source}"
        if country not in candidates:
            candidates[country] = []
        # Avoid duplicate reasons
        if reason not in candidates[country]:
            candidates[country].append(reason)

    # Step 3: determine confidence
    # - language → single country + place agrees → high
    # - language → multiple countries, but place agrees with one → high for that one
    # - language → single country (no place) → medium
    # - language → multiple countries (no place) → low
    # - place only (no language) → medium
    result: list[Candidate] = []

    for country in sorted(candidates.keys()):
        reasons = candidates[country]
        # Determine confidence
        if lang_countries:
            if country in place_countries:
                # Place evidence agrees with this country → high
                confidence = "high"
            elif len(lang_countries) == 1:
                # Language maps to single country, no place → medium
                confidence = "medium"
            else:
                # Language maps to multiple countries, no place → low
                confidence = "low"
        else:
            confidence = "medium"

        result.append({
            "country": country,
            "reason": "; ".join(reasons),
            "confidence": confidence,
        })

    # Sort by confidence (high > medium > low), then country
    conf_order = {"high": 0, "medium": 1, "low": 2}
    result.sort(key=lambda c: (conf_order.get(c["confidence"], 3), c["country"]))

    return result


def suggest_main(argv: list[str] | None = None) -> int:
    """CLI entry point for country suggestion."""
    parser = argparse.ArgumentParser(
        description="Suggest candidate jurisdictions from language and place evidence."
    )
    parser.add_argument("--language", help="ISO 639-1 language code (e.g., 'en', 'it')")
    parser.add_argument("--text", help="Visible text from the material")
    parser.add_argument("--postmark", help="Postmark string")
    parser.add_argument("--publisher", help="Publisher string")
    args = parser.parse_args(argv)

    candidates = suggest(
        language=args.language,
        text=args.text,
        postmark=args.postmark,
        publisher=args.publisher,
    )

    if not candidates:
        print("Nessun candidato suggerito.")
        return 0

    print("Candidati (Paese, Motivazione, Affidabilità):")
    for c in candidates:
        print(f"  {c['country']}: {c['reason']} [{c['confidence']}]")

    return 0



# =============================================================================
# SERIES CORRELATION MODULE (merged from correlate.py)
# =============================================================================
# Public functions: group, main
# Importers: report.py (via import correlate)


def correlate_group(verdicts: dict) -> dict:
    """Group verdict items by set_id (or file) and detect contradictions.

    Returns:
        {"groups": [{set_id, series_id, files:[...], status_summary:{...}, warnings:[...], trail:[...]}, ...]}
    """
    items = verdicts.get("items", [])

    # Build groups by set_id, falling back to file
    groups_dict: dict[str, list[dict]] = {}
    for item in items:
        extracted_facts = item.get("extracted_facts", {})
        # Group by series first (related items), then by physical object, then by file.
        set_id = extracted_facts.get("series_id") or extracted_facts.get("set_id")
        if set_id is None:
            set_id = item.get("file", "unknown")

        if set_id not in groups_dict:
            groups_dict[set_id] = []
        groups_dict[set_id].append(item)

    result_groups = []
    for set_id, group_items in groups_dict.items():
        # Collect series_id (should be consistent within group)
        series_ids = set()
        for item in group_items:
            ef = item.get("extracted_facts", {})
            sid = ef.get("series_id")
            if sid:
                series_ids.add(sid)
        series_id = next(iter(series_ids), None) if len(series_ids) == 1 else None

        # Collect files
        files = [item.get("file", "unknown") for item in group_items]

        # Status summary
        status_summary = {"public_domain": 0, "not_public_domain": 0, "uncertain": 0}
        for item in group_items:
            status = item.get("status", "uncertain")
            if status in status_summary:
                status_summary[status] += 1

        # Contradictions are meaningful only among parts of the SAME physical
        # object; items that merely share a series may legitimately differ.
        warnings = []
        by_object: dict[str, list[dict]] = {}
        for item in group_items:
            object_id = item.get("extracted_facts", {}).get("set_id") or item.get("file", "unknown")
            by_object.setdefault(object_id, []).append(item)
        for object_id, parts in by_object.items():
            for warning in _detect_contradictions(parts):
                warnings.append(f"[{object_id}] {warning}")

        # Build trail
        trail = []
        for item in group_items:
            ef = item.get("extracted_facts", {})
            trail.append({
                "file": item.get("file", "unknown"),
                "reference_year": ef.get("reference_year"),
                "year_source": ef.get("year_source"),
                "vlm_confidence": ef.get("vlm_confidence"),
                "status": item.get("status", "uncertain"),
                "rule_table_version": item.get("rule_table_version", verdicts.get("rule_table_version", "unknown")),
            })

        result_groups.append({
            "set_id": set_id,
            "series_id": series_id,
            "files": files,
            "status_summary": status_summary,
            "warnings": warnings,
            "trail": trail,
        })

    return {"groups": result_groups}


def _detect_contradictions(items: list[dict]) -> list[str]:
    """Detect contradictions across items in a group."""
    warnings = []

    # Collect reference_years
    reference_years = set()
    for item in items:
        ef = item.get("extracted_facts", {})
        ry = ef.get("reference_year")
        if ry is not None:
            reference_years.add(ry)

    if len(reference_years) > 1:
        warnings.append(
            f"Contraddizione: anni di riferimento diversi {sorted(reference_years)} nello stesso set"
        )

    # Collect country_of_origin
    countries = set()
    for item in items:
        ef = item.get("extracted_facts", {})
        coo = ef.get("country_of_origin")
        if coo:
            countries.add(coo)

    if len(countries) > 1:
        warnings.append(
            f"Contraddizione: paesi di origine diversi {sorted(countries)} nello stesso set"
        )

    # Check for status conflicts (some PD, some not)
    statuses = set()
    for item in items:
        status = item.get("status")
        if status:
            statuses.add(status)

    if len(statuses) > 1:
        warnings.append(
            f"Incongruenza di stato: {sorted(statuses)} nello stesso set"
        )

    # Check for photo_classification inconsistencies within the same series
    # (items that share a series_id but have different photo_classification values)
    # This is a warning only - the verdicts stay as they are
    classifications = {}
    for item in items:
        ef = item.get("extracted_facts", {})
        series_id = ef.get("series_id")
        photo_class = ef.get("photo_classification")
        if series_id and photo_class:
            if series_id not in classifications:
                classifications[series_id] = set()
            classifications[series_id].add(photo_class)
    
    for series_id, classes in classifications.items():
        if len(classes) > 1:
            warnings.append(
                f"Incongruenza di classificazione fotografica nella serie '{series_id}': {sorted(classes)}"
            )

    return warnings


def correlate_main(argv: list[str] | None = None) -> int:
    """CLI entry point for correlation."""
    import argparse
    import sys
    from pathlib import Path
    
    parser = argparse.ArgumentParser(
        description="Correlate verdicts into sets and detect contradictions."
    )
    parser.add_argument("verdicts", help="Path to the verdicts JSON file.")
    parser.add_argument("-o", "--output", help="Output JSON file path (default: stdout).")
    args = parser.parse_args(argv)

    # Load verdicts
    verdicts_path = Path(args.verdicts)
    if not verdicts_path.exists():
        print(f"Error: verdicts file not found: {args.verdicts}", file=sys.stderr)
        return 1

    try:
        verdicts = json.loads(verdicts_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: invalid JSON in {args.verdicts}: {e}", file=sys.stderr)
        return 1

    # Group
    result = correlate_group(verdicts)

    # Output
    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
    else:
        print(output_json)

    return 0
