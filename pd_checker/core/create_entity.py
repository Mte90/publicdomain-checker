#!/usr/bin/env python3
"""Create Wikidata items for entities_to_create.

Usage:
    python3 -m pd_checker create-entity <folder> [--live]

Source gate: entities WITHOUT source_url are NEVER created (printed as [SALTATO]).
P31 validation: p31_qid MUST be in wikidata_entities.json; unknown QIDs are refused.

Exit codes:
    Dry-run: 0 (1 if folder has no entities_to_create)
    Live: 0 if ≥1 created, 1 if all refused or credentials missing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path


def cmd_create_entity(argv: list[str]) -> int:
    """Create Wikidata items for entities_to_create in a folder's pubdom.json."""
    from pd_checker.core import catalogs, push
    
    parser = argparse.ArgumentParser(
        description="Create Wikidata items for entities_to_create in a folder's pubdom.json.",
        prog="python3 -m pd_checker create-entity"
    )
    parser.add_argument("folder", help="Material folder (needs pubdom.json with entities_to_create).")
    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Actually create items on Wikidata (default: dry-run, prints payload only)."
    )
    args = parser.parse_args(argv)
    
    folder = Path(args.folder)
    pubdom_path = folder / "pubdom.json"
    
    if not pubdom_path.exists():
        print(f"[create-entity] ERROR: pubdom.json not found in {folder}", file=sys.stderr)
        return 1
    
    # Load pubdom.json
    try:
        pubdom = json.loads(pubdom_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[create-entity] ERROR: Cannot read pubdom.json: {e}", file=sys.stderr)
        return 1
    
    # Get entities_to_create from the aggregated entities_to_create block
    entities_to_create = pubdom.get("entities_to_create", [])
    
    if not entities_to_create:
        print(f"[create-entity] No entities_to_create found in {pubdom_path}")
        return 1
    
    # Load Wikidata entities catalog for P31 validation
    entity_catalog = catalogs.wd_entities_load()
    valid_qids = set(entity_catalog.get("items", {}).keys())
    
    # Build payload for each entity
    created_count = 0
    refused_count = 0
    skipped_count = 0
    
    for entity in entities_to_create:
        name = entity.get("name", "")
        role = entity.get("role", "entità")
        source_url = entity.get("source_url", "")
        p31_qid = entity.get("p31_qid")
        
        # Source gate: skip entities without source_url
        if not source_url:
            print(f"[SALTATO] «{name}»: manca la fonte (source_url) in entities_to_create — riempila prima di creare")
            skipped_count += 1
            continue
        
        # Validate p31_qid if present
        if p31_qid and p31_qid not in valid_qids:
            print(f"[RIFIUTATO] «{name}»: p31_qid '{p31_qid}' non trovato nel catalogo wikidata_entities.json — usa una classe nota")
            refused_count += 1
            continue
        
        # Derive description from role (honest, not overclaimed)
        description_map = {
            "autore": "persona o ente autore di un'opera",
            "editore": "casa editrice / stabilimento tipografico indicato dall'impronta di un pezzo d'epoca",
            "raffigura": "soggetto raffigurato in un'opera",
            "origine": "origine del file",
        }
        description = description_map.get(role, f"entità correlata a {role}")
        
        # Build the payload for action=wbeditentity
        payload = {
            "action": "wbeditentity",
            "new": "item",
            "language": "it",
            "summary": f"Created by pd_checker (source: {source_url})",
            "data": {
                "labels": {"it": {"value": name}},
                "descriptions": {"it": {"value": description}},
            },
        }
        
        # Add P31 if validated
        if p31_qid:
            payload["data"]["claims"] = {
                "P31": [
                    {
                        "mainsnak": {
                            "datatype": "wikibase-item",
                            "datavalue": {"value": {"id": p31_qid, "type": "item"}, "type": "wikibase-entityid"},
                            "property": "P31"
                        },
                        "type": "statement",
                        "rank": "normal"
                    }
                ]
            }
        
        if args.live:
            # Live mode: create the item
            # Validate credentials first
            credentials = push.get_credentials()
            auth_method, error = push.validate_credentials(credentials, live=True)
            
            if not auth_method:
                print(f"[create-entity] ERROR: {error}", file=sys.stderr)
                return 1
            
            # POST to Wikidata API
            api_base = os.environ.get("PD_WM_API_BASE", "https://www.wikidata.org/w/api.php")
            
            # Login if needed
            opener = push.create_opener()
            if auth_method == "bot-password":
                success, msg = push.login_bot_password(opener, credentials)
                if not success:
                    print(f"[create-entity] LOGIN FAILED: {msg}", file=sys.stderr)
                    return 1
            
            # Build form data
            form_data = {
                "action": "wbeditentity",
                "new": "item",
                "language": "it",
                "data": json.dumps(payload["data"]),
                "summary": payload["summary"],
                "format": "json",
            }
            
            # Add OAuth token if using OAuth
            if auth_method == "oauth":
                form_data["oauth_token"] = credentials["oauth_token"]
            
            # POST request
            encoded = urllib.parse.urlencode(form_data).encode("utf-8")
            req = urllib.request.Request(
                api_base,
                data=encoded,
                headers={"User-Agent": f"pd_checker-create-entity/1.0 ({credentials['contact']})"},
                method="POST"
            )
            
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    
                if "error" in result:
                    error_msg = result["error"].get("info", str(result["error"]))
                    print(f"[ERRORE API] «{name}»: {error_msg}")
                    print("  → Se l'item esiste già, usa «resolve --live» per ottenere il QID.")
                    refused_count += 1
                elif "entity" in result:
                    new_qid = result["entity"].get("id", "")
                    print(f"[CREATO] «{name}» → {new_qid}")
                    created_count += 1
                    
                    # Write QID to wd_cache.json as result "ok"
                    cache = catalogs._load_wd_cache()
                    cache.setdefault("names", {})[name] = {
                        "qid": new_qid,
                        "result": "ok",
                        "searched_at": date.today().isoformat()
                    }
                    # Write cache (in live mode, write to the real cache)
                    catalogs._save_wd_cache(cache)
            except urllib.error.HTTPError as e:
                print(f"[ERRORE HTTP] «{name}»: {e.code} {e.reason}", file=sys.stderr)
                refused_count += 1
            except urllib.error.URLError as e:
                print(f"[ERRORE RETE] «{name}»: {e.reason}", file=sys.stderr)
                refused_count += 1
        else:
            # Dry-run mode: print the payload
            print(f"\n[DRY-RUN] «{name}»:")
            print(f"  Label (it): {name}")
            print(f"  Description (it): {description}")
            if p31_qid:
                print(f"  P31 (instance of): {p31_qid}")
            print(f"  Source URL: {source_url}")
            print(f"  POST payload:")
            print(f"    {json.dumps(payload, indent=6, ensure_ascii=False)}")
            print(f"  [SOURCE-GATE: OK]")
            # In dry-run, count as would-be created
            created_count += 1
    
    # Summary
    print(f"\n== Sommario create-entity ==")
    print(f"  Creati: {created_count}")
    print(f"  Saltati (nessuna fonte): {skipped_count}")
    print(f"  Rifiutati (P31 non valido / errore): {refused_count}")
    
    # Exit code
    if args.live:
        return 0 if created_count > 0 else 1
    else:
        # Dry-run: 0 if ≥1 entity, 1 if none
        return 0 if len(entities_to_create) > 0 else 1
