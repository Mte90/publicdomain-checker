#!/usr/bin/env python3
"""Wikidata statement builder for pd_checker verdicts.

Builds structured Wikidata statements from verdicts and context,
deriving QIDs from the shared wd_cache and marking unresolved items for review.

Provenance rule: every emitted QID must be traceable to a verified cache entry.
Never emit a QID that is not in the cache — mark such statements da_rivedere.

CLI: python3 wikidata.py <verdicts.json> --context <folder> -o <out.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .context import load as load_context
from .catalogs import lookup as cache_lookup

# Property IDs (Italian labels)
P31 = "P31"  # istanza di
P6216 = "P6216"  # stato copyright
P275 = "P275"  # licenza
P571 = "P571"  # data di creazione
P170 = "P170"  # autore
P180 = "P180"  # raffigura
# Ordinary words that can appear in an Italian transcription and in the cache
# (e.g. the circa qualifier item) but must never be emitted as a depicted entity.
_P180_STOPWORDS = {"circa"}
P7482 = "P7482"  # origine del file
P1480 = "P1480"  # circostanze della fonte

# Class QIDs
Q_POSTCARD = "Q192425"
Q_PHOTO = "Q125191"
Q_PUBLIC_DOMAIN = "Q19652"
Q_COPYRIGHTED = "Q50423863"
Q_CIRCA = "Q5727902"


def _lookup_qid(name: str) -> tuple[str | None, str, str]:
    """Look up a name in the cache. Returns (qid, status, cache_state).
    
    Uses cache_lookup for OFFLINE, cache-only resolution.
    
    Args:
        name: The name to look up
        
    Returns:
        Tuple of (qid, status, cache_state) where:
        - qid: The QID if found, None otherwise
        - status: "ok" if QID found, "da_rivedere" otherwise
        - cache_state: "found_ok" (QID in cache), "found_not_found" (searched, no QID), "found_ambiguous" (searched, top hits did not coincide — human must confirm), "not_in_cache" (never searched)
    
    This function performs NO network I/O.
    """
    if not name:
        return None, "da_rivedere", "not_in_cache"
    
    # Load cache to check if name was ever searched
    from .catalogs import _load_wd_cache
    cache = _load_wd_cache()
    entry = cache.get("names", {}).get(name)
    
    if entry:
        # Name was searched before
        if entry.get("result") == "ok" and entry.get("qid"):
            return entry["qid"], "ok", "found_ok"
        if entry.get("result") == "ambiguous":
            # Searched, top hits did not coincide — human must confirm
            return None, "da_rivedere", "found_ambiguous"
        # Searched but not found
        return None, "da_rivedere", "found_not_found"
    else:
        # Never searched
        return None, "da_rivedere", "not_in_cache"


def _build_statements(verdict: dict, ctx: dict) -> list[dict]:
    """Build Wikidata statements from verdict and context."""
    statements = []
    extracted = verdict.get("extracted_facts", {})
    status = verdict.get("status", "uncertain")
    
    # P31: istanza di (document type)
    doc_type = (extracted.get("document_type") or "").lower()
    if doc_type == "postcard":
        statements.append({
            "property": P31,
            "label": "istanza di",
            "value": "postcard",
            "qid": Q_POSTCARD,
            "source": "extracted_facts.document_type",
            "status": "ok",
        })
    elif doc_type == "photo":
        statements.append({
            "property": P31,
            "label": "istanza di",
            "value": "photo",
            "qid": Q_PHOTO,
            "source": "extracted_facts.document_type",
            "status": "ok",
        })
    # else: no statement for unknown types
    
    # P6216: stato copyright
    if status == "public_domain":
        statements.append({
            "property": P6216,
            "label": "stato copyright",
            "value": "public domain",
            "qid": Q_PUBLIC_DOMAIN,
            "source": "verdict.status",
            "status": "ok",
        })
    elif status == "not_public_domain":
        statements.append({
            "property": P6216,
            "label": "stato copyright",
            "value": "copyrighted",
            "qid": Q_COPYRIGHTED,
            "source": "verdict.status",
            "status": "ok",
        })
    # else: uncertain -> no statement
    
    # P275: licenza
    if status == "public_domain":
        statements.append({
            "property": P275,
            "label": "licenza",
            "value": "public domain",
            "qid": Q_PUBLIC_DOMAIN,
            "source": "verdict.status",
            "status": "ok",
        })
    
    # P571: data di creazione
    ref_year = extracted.get("reference_year")
    year_source = extracted.get("year_source")
    if ref_year is not None:
        # Documentary dates are the date: printed, postmarked or WRITTEN on the
        # document (manuscript) — none of them needs review. Only non-documentary
        # sources remain estimates: visual guesses, folder knowledge, none.
        is_estimated = year_source == "visual_estimate" or (
            year_source is None or year_source not in ("visible_text", "postmark", "manuscript")
        )
        
        # Build ISO time string with year precision (month/day = 00)
        time_str = f"+{ref_year:04d}-00-00T00:00:00Z"
        
        stmt = {
            "property": P571,
            "label": "data di creazione",
            "value": str(ref_year),
            "time": time_str,
            "precision": 9,
            "qid": None,
            "source": "extracted_facts.reference_year",
            "status": "da_rivedere" if is_estimated else "ok",
            "state": "date_estimate" if is_estimated else "ok",
        }
        
        # Add qualifiers and reason for estimates
        if is_estimated:
            # Get circa QID from cache
            circa_qid, _, _ = _lookup_qid("circa")
            stmt["qualifiers"] = [
                {
                    "property": P1480,
                    "label": "circostanze della fonte",
                    "qid": circa_qid,
                }
            ]
            # Add reason for reviewer
            stmt["reason"] = f"Data stimata (fonte: {year_source}): confermala leggendo la data stampata o il timbro sul pezzo"
        
        statements.append(stmt)
    else:
        statements.append({
            "property": P571,
            "label": "data di creazione",
            "value": None,
            "qid": None,
            "source": "extracted_facts.reference_year",
            "status": "da_rivedere",
        })
    
    # P170: autore
    author = extracted.get("author")
    author_role = extracted.get("author_role")
    
    # Determine author handling based on author_role
    # - photographer: emit P170 (creator)
    # - printer/publisher: emit P123 (publisher) instead, mark P170 for review
    # - illustrator: emit neither P170 nor P123, mark for review
    # - absent/unknown: current behaviour (marker detection)
    if author:
        author_is_printing_work = False
        author_is_illustrator = False
        
        if author_role == "printer" or author_role == "publisher":
            author_is_printing_work = True
        elif author_role == "illustrator":
            author_is_illustrator = True
        elif author_role is None or author_role == "photographer":
            # No role or photographer: use marker detection for backward compatibility
            if author_role is None:
                _PRINTING_WORK_MARKERS = ["Stab.", "Tipografia", "Arti Grafiche", "Litografia", "Editore"]
                for marker in _PRINTING_WORK_MARKERS:
                    if marker in author:
                        author_is_printing_work = True
                        break
                
                # Also check against known printing works from the wd_cache not_found list
                if not author_is_printing_work:
                    try:
                        from .catalogs import _load_wd_cache
                        cache = _load_wd_cache()
                        not_found_names = [name for name, entry in cache.get("names", {}).items() 
                                           if entry.get("result") == "not_found"]
                        if author in not_found_names:
                            for marker in _PRINTING_WORK_MARKERS:
                                if marker in author or author in not_found_names:
                                    author_is_printing_work = True
                                    break
                    except Exception:
                        pass
        
        if author_is_printing_work:
            # Printing works: emit as P123 only, do NOT emit P170
            # The {{unknown|author}} in wikitext covers the missing creator
            # Add a note to the verdict for reviewer awareness
            verdict.setdefault("notes", []).append(
                f"Autore ignoto: nessuna dichiarazione SDC per P170 (l'autore '{author}' è uno stabilimento di stampa)"
            )
            # Emit publisher info
            qid, pub_status, pub_cache_state = _lookup_qid(author)
            stmt = {
                "property": "P123",
                "label": "editore",
                "value": author,
                "qid": qid,
                "source": "extracted_facts.author (printing works)",
                "status": pub_status,
            }
            if qid is None and pub_status == "da_rivedere":
                if pub_cache_state == "found_not_found":
                    stmt["state"] = "absent"
                    stmt["reason"] = f"Item «{author}» cercato su Wikidata: inesistente (o non ancora creato). Se crei l'item (occorre una fonte), rilancia «resolve «{author}» --live»; altrimenti la dichiarazione resta senza QID: il nome compare comunque nel wikitext della pagina file."
                elif pub_cache_state == "found_ambiguous":
                    stmt["state"] = "ambiguous"
                    stmt["reason"] = f"Ricerca «{author}» su Wikidata ambigua: nessun risultato con nome coincidente. Controlla i candidati («resolve «{author}» --live» li ristampa) e conferma il QID giusto a mano nella cache."
                else:
                    stmt["state"] = "unsearched"
                    stmt["reason"] = f"Nome «{author}» non ancora cercato: chiedi all'agente «resolve «{author}» --live» — se l'item esiste, il QID entra in cache e al prossimo run la dichiarazione diventa ok."
            statements.append(stmt)
        elif author_is_illustrator:
            # Illustrator: emit neither P170 nor P123
            # Add a note to the verdict for reviewer awareness
            verdict.setdefault("notes", []).append(
                f"Autore ignoto: nessuna dichiarazione SDC per P170 (l'autore '{author}' è un illustratore)"
            )
        else:
            # Photographer or unknown (no marker detected): emit P170 normally
            qid, author_status, author_cache_state = _lookup_qid(author)
            stmt = {
                "property": P170,
                "label": "autore",
                "value": author,
                "qid": qid,
                "source": "extracted_facts.author",
                "status": author_status,
            }
            # Add reason if QID not found
            if qid is None and author_status == "da_rivedere":
                if author_cache_state == "found_ambiguous":
                    stmt["state"] = "ambiguous"
                    stmt["reason"] = f"Ricerca «{author}» su Wikidata ambigua: nessun risultato con nome coincidente. Controlla i candidati («resolve «{author}» --live» li ristampa) e conferma il QID giusto a mano nella cache."
                elif author_cache_state == "found_not_found":
                    # Searched but item doesn't exist
                    stmt["state"] = "absent"
                    stmt["reason"] = f"Item «{author}» cercato su Wikidata: inesistente (o non ancora creato). Se crei l'item (occorre una fonte), rilancia «resolve «{author}» --live»; altrimenti la dichiarazione resta senza QID: il nome compare comunque nel wikitext della pagina file."
                else:
                    # Never searched
                    stmt["state"] = "unsearched"
                    stmt["reason"] = f"Nome «{author}» non ancora cercato: chiedi all'agente «resolve «{author}» --live» — se l'item esiste, il QID entra in cache e al prossimo run la dichiarazione diventa ok."
            statements.append(stmt)
    else:
        # FIX: Do NOT emit P170 when author is None/empty - {{unknown|author}} covers it
        pass  # No statement emitted
    
    # P180: raffigura (from ctx depicts AND from item's visible_text)
    # Track QIDs we've already emitted to avoid duplicates
    emitted_p180_qids = {}
    
    # First, add subjects from context.depicts
    depicts = ctx.get("depicts", [])
    if depicts:
        for dep in depicts:
            label = dep.get("label", "")
            # Resolve through cache — never use a qid from context that isn't in cache
            qid, _, _ = _lookup_qid(label)
            if qid and qid not in emitted_p180_qids:
                emitted_p180_qids[qid] = label
                statements.append({
                    "property": P180,
                    "label": "raffigura",
                    "value": label,
                    "qid": qid,
                    "source": "context.depicts",
                    "status": "ok",
                })
    
    # Second, derive candidate subjects from item's visible_text by matching against EXISTING cache
    visible_text = extracted.get("visible_text") or ""
    visible_text_translation = extracted.get("visible_text_translation") or ""
    text_to_search = f"{visible_text} {visible_text_translation}".lower()
    
    if text_to_search:
        # Load cache and look for known entity names in the visible text
        try:
            from .catalogs import _load_wd_cache
            cache = _load_wd_cache()
            cache_names = [
                name for name in cache.get("names", {}).keys()
                if name.lower() not in _P180_STOPWORDS
            ]
            
            # First pass: build QID → canonical name mapping from visible_text matches
            qid_to_canonical = {}
            for name in cache_names:
                # Case-insensitive search for the name in visible text
                if name.lower() in text_to_search:
                    qid, _, _ = _lookup_qid(name)
                    if qid:
                        # Keep the first name we find for each QID as canonical
                        if qid not in qid_to_canonical:
                            qid_to_canonical[qid] = name
            
            # Second pass: emit one statement per QID, using canonical name
            for qid, canonical_name in qid_to_canonical.items():
                if qid not in emitted_p180_qids:
                    emitted_p180_qids[qid] = canonical_name
                    statements.append({
                        "property": P180,
                        "label": "raffigura",
                        "value": canonical_name,
                        "qid": qid,
                        "source": "extracted_facts.visible_text (cache match)",
                        "status": "ok",
                    })
        except Exception:
            # If cache lookup fails, continue without this enhancement
            pass
    
    # P7482: origine del file
    # FIX: Only emit P7482 if the value is a Wikidata QID, not free-text
    # Free-text provenance goes in wikitext |source=, not as an SDC statement
    provenance = ctx.get("provenance")
    if provenance and isinstance(provenance, str) and provenance.startswith("Q"):
        statements.append({
            "property": P7482,
            "label": "origine del file",
            "value": provenance,
            "qid": provenance,
            "source": "context.provenance",
            "status": "ok",
        })
    # Free-text provenance is skipped - it goes in wikitext |source=
    
    return statements


def _build_information(verdict: dict, ctx: dict) -> dict:
    """Build {{Information}} block data for Commons upload.
    
    Returns a dict with keys: description, date, source, author, permission.
    All fields are required by Commons.
    
    Provenance rule: use context provenance as information.source when present.
    {{Self-scanned}} remains the default only when no provenance is declared.
    """
    extracted = verdict.get("extracted_facts", {})
    
    # Description in Italian from the transcription — full text, never truncated.
    # The front side's OCR transcription (written by `ocr --apply`) is the most
    # complete source; the vision transcription is the fallback.
    visible_text = extracted.get("visible_text") or ""
    doc_type = (extracted.get("document_type") or "unknown").lower()
    ocr_texts = extracted.get("ocr_texts") or {}
    images = extracted.get("images") or []
    front_ocr = None
    if images and ocr_texts:
        front_ocr = ocr_texts.get(images[0].split("/")[-1])
    transcription = front_ocr or visible_text
    
    # Build Italian description
    if transcription:
        description = transcription.replace("|", " / ")
    else:
        # Fallback description based on document type
        descriptions = {
            "photo": "Fotografia",
            "postcard": "Cartolina",
            "letter": "Lettera",
            "newspaper": "Quotidiano",
            "magazine": "Rivista",
            "manuscript": "Manoscritto",
        }
        description = descriptions.get(doc_type, "Documento d'archivio")
    
    # Date: reference year or "n/d"
    ref_year = extracted.get("reference_year")
    date = str(ref_year) if ref_year is not None else "n/d"
    
    # Source: use context provenance when present and confirmed, {{Self-scanned}} only as default
    provenance = ctx.get("provenance", "")
    if provenance and "da confermare" not in provenance.lower():
        # Use the declared provenance as the source (only when confirmed)
        source = provenance
    else:
        # No provenance or unconfirmed: default to {{Self-scanned}} or "non specificato"
        # For backward compatibility with existing tests, use "non specificato" for unconfirmed
        source = "non specificato" if provenance else "{{Self-scanned}}"
    
    # Author: from extracted_facts or "sconosciuto"
    author = extracted.get("author") or "sconosciuto"
    
    # Permission: empty string (no specific permission needed for PD works)
    permission = ""
    
    return {
        "description": description,
        "date": date,
        "source": source,
        "author": author,
        "permission": permission,
    }


def build(verdict: dict, ctx: dict) -> dict:
    """Build a wikidata block for a verdict.
    
    Args:
        verdict: A single verdict item from the verdicts JSON
        ctx: Context from context.json (folder-level)
        
    Returns:
        A wikidata block: {"statements": [...], "categories": [...], "review": [...], "information": {...}, "entities_to_create": [...]}
    
    Provenance: every QID is resolved through wd_cache.lookup().
    If a name is not in cache, no QID is emitted and status is da_rivedere.
    """
    statements = _build_statements(verdict, ctx)
    
    # Build {{Information}} block (required by Commons)
    information = _build_information(verdict, ctx)
    
    # Categories: per-item content-based when declared in facts, else folder context
    extracted_for_cat = verdict.get("extracted_facts", {})
    declared_cats = extracted_for_cat.get("commons_categories")
    if declared_cats:
        categories = list(declared_cats)
    else:
        categories = list(ctx.get("categories", []))
    
    # FIX 3: No more zero-category verdicts for IT postcards
    # Always propose "Category:Postcards of Italy" for Italian postcards
    extracted = verdict.get("extracted_facts", {})
    doc_type = (extracted.get("document_type") or "").lower()
    country = (extracted.get("country_of_origin") or "").upper()
    
    if doc_type == "postcard" and country == "IT":
        postcards_italy = "Category:Postcards of Italy"
        if postcards_italy not in categories:
            categories.append(postcards_italy)
    
    # Review: property IDs that need review
    review = [
        s["property"] for s in statements 
        if s.get("status") == "da_rivedere"
    ]
    
    # Build entities_to_create from QID-less statements with cache states not_found or ambiguous
    # EXCLUDE date properties (P571) - dates are not entities that can be created
    entities_to_create = []
    seen_names = set()
    for stmt in statements:
        if stmt.get("qid") is None and stmt.get("status") == "da_rivedere":
            value = stmt.get("value")
            prop = stmt.get("property", "")
            # Skip date properties (P571) - dates are not entities
            if prop == "P571":
                continue
            if value and value not in seen_names:
                # Determine role from property
                role_map = {
                    "P170": "autore",
                    "P123": "editore",
                    "P180": "raffigura",
                    "P7482": "origine",
                }
                role = role_map.get(prop, "entità")
                
                # Build entity entry with optional source_url and p31_qid fields
                entity_entry = {
                    "name": value,
                    "role": role,
                    "source_url": "",  # Empty by default — filled during review
                    "p31_qid": None,  # Optional "instance of" class QID — validated against catalog when present
                    "suggested": {
                        "label": value,
                        "note": "La classe precisa (es. impresa tipografica) si sceglie al momento della creazione"
                    },
                    "action": f"Crea l'item su wikidata.org (occorre una fonte), poi rilancia «resolve '{value}' --live»: il QID va in cache e la dichiarazione diventa ok al prossimo run."
                }
                
                # Add candidates if ambiguous
                if stmt.get("_cache_state") == "found_ambiguous":
                    # Candidates would be available from the cache lookup
                    entity_entry["candidates"] = []  # Placeholder for candidates
                
                entities_to_create.append(entity_entry)
                seen_names.add(value)
    
    # Update verdict's commons_categories
    verdict["commons_categories"] = categories
    
    return {
        "statements": statements,
        "categories": categories,
        "review": review,
        "information": information,
        "entities_to_create": entities_to_create,
    }


def build_manifest(verdicts: dict, ctx: dict) -> list[dict]:
    """Build upload manifest from verdicts.
    
    Returns:
        List of rows: {file, images, license_template, categories, status, upload_flag, wikidata_qids, information}
    """
    manifest = []
    
    for item in verdicts.get("items", []):
        # Build wikidata block to extract QIDs and information
        wikidata_block = build(item.copy(), ctx)
        
        # Collect resolved QIDs by property
        wikidata_qids = {}
        for stmt in wikidata_block["statements"]:
            if stmt.get("qid"):
                prop_label = stmt["label"]
                wikidata_qids[prop_label] = stmt["qid"]
        
        extracted = item.get("extracted_facts", {})
        upload = item.get("upload", {})
        
        # License templates derived from verdict (not hardcoded)
        commons_templates = item.get("commons_templates", [])
        if commons_templates:
            # Use the first template as the primary, and keep the full list
            license_template = commons_templates[0]
            license_templates = commons_templates
        else:
            # No templates in verdict -> no license statement
            license_template = None
            license_templates = []
        
        manifest.append({
            "file": item.get("file"),
            "images": extracted.get("images", []),
            "license_template": license_template,
            "license_templates": license_templates,
            "categories": wikidata_block["categories"],
            "status": item.get("status"),
            "upload_flag": upload.get("flag"),
            "wikidata_qids": wikidata_qids,
            "information": wikidata_block["information"],
        })
    
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build Wikidata statements from pd_checker verdicts."
    )
    parser.add_argument("verdicts", help="Path to verdicts JSON file.")
    parser.add_argument("--context", required=True, help="Folder containing context.json.")
    parser.add_argument("-o", "--output", required=True, help="Output verdicts JSON file.")
    args = parser.parse_args(argv)
    
    # Load inputs
    verdicts_path = Path(args.verdicts)
    if not verdicts_path.exists():
        print(f"Error: verdicts file not found: {args.verdicts}", file=sys.stderr)
        return 1
    
    verdicts = json.loads(verdicts_path.read_text(encoding="utf-8"))
    
    # Resolve the folder context exactly as the rest of the pipeline does
    # (pubdom.json -> context.json -> context.md), so the depicts and the
    # provenance survive both the first run and a re-run.
    ctx = load_context(args.context) or {}
    
    # Build wikidata blocks for each item
    enriched_items = []
    for item in verdicts.get("items", []):
        enriched_item = item.copy()
        wikidata_block = build(enriched_item, ctx)
        enriched_item["wikidata"] = wikidata_block
        # Also set information at item level for direct access
        enriched_item["information"] = wikidata_block["information"]
        enriched_items.append(enriched_item)
    
    # Write enriched verdicts
    output_payload = {
        "rule_table_version": verdicts.get("rule_table_version", "unknown"),
        "current_year": verdicts.get("current_year", 0),
        "items": enriched_items,
    }
    
    output_path = Path(args.output)
    output_path.write_text(
        json.dumps(output_payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[wikidata] enriched verdicts -> {output_path}")
    
    # Write upload manifest next to the enriched verdicts (the pipeline's pd_data/),
    # built from the enriched items so each row carries its resolved QIDs.
    manifest = build_manifest({"items": enriched_items}, ctx)
    manifest_path = Path(args.output).parent / "upload_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"[wikidata] upload manifest -> {manifest_path}")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
