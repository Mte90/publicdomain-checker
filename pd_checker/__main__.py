#!/usr/bin/env python3
"""Unified entry point for pd_checker.

Usage:
  python3 -m pd_checker run <folder> [--upload-policy strict|pd-only] [--images-base .] [--current-year YYYY]
  python3 -m pd_checker status <folder>
  python3 -m pd_checker validate <folder>
  python3 -m pd_checker preflight <folder> [--offline]
  python3 -m pd_checker push <folder> [--live] [--filename-prefix PREFIX]
  python3 -m pd_checker update
  python3 -m pd_checker resolve <name>
  python3 -m pd_checker suggest-country [--language CODE] [--text TEXT] [--postmark TEXT] [--publisher TEXT]

All commands except `update`, `verify-live`, and `push --live` work offline. `update` and `verify-live`
touch the network. `preflight` performs a live duplicate check by default; use `--offline`
to skip network checks. `push` is DRY-RUN by default; use `--live` for actual upload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


# =============================================================================
# Upload preflight utilities (merged from preflight.py)
# =============================================================================

USER_AGENT = "pubdom-preflight/1.0 (pd_checker)"


def compute_sha1(file_path: Path) -> str | None:
    """Compute SHA1 hash of a file."""
    if not file_path.exists():
        return None
    try:
        sha1 = hashlib.sha1()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha1.update(chunk)
        return sha1.hexdigest().lower()
    except (IOError, OSError):
        return None


def _load_verdicts(path: Path) -> dict:
    """Load verdicts.json with error handling.
    
    Args:
        path: Path to verdicts.json
    
    Returns:
        Parsed JSON as dict, or empty dict if file is missing/malformed.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"[load-verdicts] WARNING: Cannot read {path}: {e}", file=sys.stderr)
        return {}


def check_commons_duplicates(sha1_list: list[tuple[str, str]], offline: bool = False) -> dict[str, str | None]:
    """Check if files already exist on Commons by SHA1.
    
    Args:
        sha1_list: List of (filename, sha1) tuples to check.
        offline: If True, skip the network check and return empty dict.
        
    Returns:
        A dict mapping original filenames to existing Commons filenames.
        
    Raises:
        URLError/HTTPError/JSONDecodeError/TimeoutError: If the network check fails
            and offline is False.
    """
    if offline or not sha1_list:
        return {}
    
    results: dict[str, str | None] = {}
    batch_size = 50
    
    for i in range(0, len(sha1_list), batch_size):
        batch = sha1_list[i:i + batch_size]
        sha1s = [sha1 for _, sha1 in batch]
        
        aisha1_param = "|".join(sha1s)
        url = f"https://commons.wikimedia.org/w/api.php?action=query&list=allimages&aisha1={aisha1_param}&format=json&useragent={USER_AGENT}"
        
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            
            allimages = data.get("query", {}).get("allimages", [])
            sha1_to_filename = {sha1: filename for filename, sha1 in batch}
            for item in allimages:
                found_sha1 = item.get("aisha1", "").lower()
                found_filename = item.get("name", "")
                if found_sha1 in sha1_to_filename:
                    original_filename = sha1_to_filename[found_sha1]
                    results[original_filename] = found_filename
                    
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
            # Re-raise network errors for live mode (offline mode already returned early)
            raise
    
    return results


def check_information_completeness(information: dict) -> list[str]:
    """Check {{Information}} template completeness."""
    missing = []
    required = ["description", "date", "source", "author"]
    
    for field in required:
        value = information.get(field)
        if not value or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    
    return missing


def validate_item(
    item: dict,
    folder: Path,
    offline: bool,
    template_unknown: list[str],
    cat_cache_lookup,
    wd_entities_unknown,
    commons_duplicates: dict[str, str | None]
) -> dict:
    """Validate a single item for upload readiness."""
    reasons = []
    blocked = False
    
    file_path = item.get("file", "")
    images = item.get("images", [])
    commons_templates = item.get("commons_templates", [])
    commons_categories = item.get("commons_categories", [])
    wikidata = item.get("wikidata", {})
    information = item.get("information", {})
    upload = item.get("upload", {})
    category_unknown = item.get("category_unknown", [])
    template_unknown_field = item.get("template_unknown", [])
    entity_unknown = item.get("entity_unknown", [])
    
    sha1_results = {}
    for img in images:
        img_path = folder / img if not img.startswith("/") else Path(img)
        sha1 = compute_sha1(img_path)
        if sha1:
            sha1_results[img] = sha1
    
    duplicates_found = []
    for img, sha1 in sha1_results.items():
        if img in commons_duplicates and commons_duplicates[img]:
            duplicates_found.append(commons_duplicates[img])
    
    if duplicates_found:
        blocked = True
        for dup in duplicates_found:
            reasons.append(f"already on Commons: {dup}")
    
    if template_unknown_field or template_unknown:
        blocked = True
        unknown_templates = template_unknown_field or template_unknown
        reasons.append(f"unknown licence tags: {', '.join(unknown_templates)}")
    
    categories_missing = []
    for cat in commons_categories:
        result = cat_cache_lookup(cat)
        if result != "exists":
            categories_missing.append(cat)
    
    if categories_missing or category_unknown:
        blocked = True
        all_missing = categories_missing + category_unknown
        reasons.append(f"categories not in cache: {', '.join(all_missing)}")
    
    if entity_unknown or wd_entities_unknown:
        blocked = True
        unknown_entities = entity_unknown or wd_entities_unknown
        reasons.append(f"unknown Wikidata entities: {', '.join(unknown_entities)}")
    
    missing_info = check_information_completeness(information)
    if missing_info:
        blocked = True
        reasons.append(f"missing Information fields: {', '.join(missing_info)}")
    
    upload_flag = upload.get("flag") if isinstance(upload, dict) else None
    if upload_flag != "ready":
        blocked = True
        reasons.append(f"upload.flag is '{upload_flag}', expected 'ready'")
    
    if not blocked:
        if category_unknown:
            blocked = True
            reasons.append(f"category_unknown not empty: {', '.join(category_unknown)}")
        if template_unknown_field:
            blocked = True
            reasons.append(f"template_unknown not empty: {', '.join(template_unknown_field)}")
        if entity_unknown:
            blocked = True
            reasons.append(f"entity_unknown not empty: {', '.join(entity_unknown)}")
    
    return {
        "file": file_path,
        "ready": not blocked,
        "reasons": reasons if reasons else ["OK"]
    }


def cmd_run(argv: list[str]) -> int:
    """Run the whole pd_checker pipeline for a material folder."""
    # Pipeline orchestration (merged from pipeline.py)
    import argparse
    import json
    import shutil
    from datetime import date
    
    from pd_checker.core import catalogs, context, rules, wikidata, report
    templates = catalogs
    wd_entities = catalogs

    parser = argparse.ArgumentParser(description="Run the whole pd_checker pipeline for a folder.")
    parser.add_argument("folder", help="Material folder (needs facts.json; context/run optional).")
    parser.add_argument("--images-base", default=".", help="Image base for thumbnails in the root dashboard.")
    parser.add_argument("--current-year", type=int, default=date.today().year)
    parser.add_argument(
        "--upload-policy",
        choices=["strict", "pd-only", "all"],
        default="strict",
        help="Upload policy: 'strict' (default), 'pd-only' (copyright-only reading), or 'all' (ready all items, including uncertain)."
    )
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    if not folder.is_dir():
        raise SystemExit(f"[pipeline] not a folder: {folder}")

    work_dir = folder / "pd_data"
    work_dir.mkdir(exist_ok=True)

    def _first_existing(*candidates: Path) -> Path | None:
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def _read_json(path: Path | None):
        if path is None:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _load_pubdom(folder: Path) -> dict:
        data = _read_json(folder / "pubdom.json")
        return data if isinstance(data, dict) else {}

    def _check_templates(verdicts_path: Path) -> None:
        """Check verdicts for unknown templates and annotate them."""
        verdicts_data = _load_verdicts(verdicts_path)
        if not verdicts_data:
            return
        
        catalog = templates.load()
        unknown_found = False
        
        for item in verdicts_data.get("items", []):
            templates_list = item.get("commons_templates", [])
            if templates_list:
                unknown = templates.unknown(templates_list, catalog)
                item["template_unknown"] = unknown
                if unknown:
                    unknown_found = True
        
        if unknown_found:
            print("[template-check] WARNING: Unknown templates detected:", file=sys.stderr)
            for item in verdicts_data.get("items", []):
                unknown = item.get("template_unknown", [])
                if unknown:
                    names = ", ".join("{{" + t + "}}" for t in unknown)
                    print(f"  {item.get('file', 'unknown')}: {names}", file=sys.stderr)
        else:
            for item in verdicts_data.get("items", []):
                if "template_unknown" not in item:
                    item["template_unknown"] = []
        
        try:
            with open(verdicts_path, "w", encoding="utf-8") as f:
                json.dump(verdicts_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[template-check] WARNING: Cannot write verdicts: {e}", file=sys.stderr)

    def _check_categories(verdicts_path: Path) -> None:
        """Check verdicts for unknown Commons categories (offline, cache-only)."""
        verdicts_data = _load_verdicts(verdicts_path)
        if not verdicts_data:
            return
        
        unknown_found = False
        all_unknown_cats = set()
        
        for item in verdicts_data.get("items", []):
            categories = item.get("commons_categories", [])
            if categories:
                unknown = []
                for cat in categories:
                    if not catalogs.lookup_cat(cat):
                        unknown.append(cat)
                        all_unknown_cats.add(cat)
                
                item["category_unknown"] = unknown
                if unknown:
                    unknown_found = True
        
        if unknown_found:
            print("[category-check] WARNING: Unknown Commons categories detected (not in cache):", file=sys.stderr)
            for cat in sorted(all_unknown_cats):
                print(f"  {cat}", file=sys.stderr)
        else:
            for item in verdicts_data.get("items", []):
                if "category_unknown" not in item:
                    item["category_unknown"] = []
        
        try:
            with open(verdicts_path, "w", encoding="utf-8") as f:
                json.dump(verdicts_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[category-check] WARNING: Cannot write verdicts: {e}", file=sys.stderr)

    def _check_entities(verdicts_path: Path) -> None:
        """Check verdicts for unknown Wikidata entities and annotate them."""
        verdicts_data = _load_verdicts(verdicts_path)
        if not verdicts_data:
            return
        
        catalog = wd_entities.wd_entities_load()
        unknown_found = False
        all_unknown_ids = set()
        
        for item in verdicts_data.get("items", []):
            wikidata_block = item.get("wikidata", {})
            statements = wikidata_block.get("statements", [])
            
            item_unknown = []
            for stmt in statements:
                prop = stmt.get("property", "")
                qid = stmt.get("qid")
                
                if prop and prop.startswith("P"):
                    if prop not in catalog.get("properties", {}):
                        item_unknown.append(prop)
                        all_unknown_ids.add(prop)
                
                if qid and qid.startswith("Q"):
                    if qid not in catalog.get("items", {}):
                        item_unknown.append(qid)
                        all_unknown_ids.add(qid)
            
            if item_unknown:
                item["entity_unknown"] = item_unknown
                unknown_found = True
        
        if unknown_found:
            print("[entity-check] WARNING: Unknown Wikidata entities detected:", file=sys.stderr)
            for entity_id in sorted(all_unknown_ids):
                print(f"  {entity_id}", file=sys.stderr)
        else:
            for item in verdicts_data.get("items", []):
                if "entity_unknown" not in item:
                    item["entity_unknown"] = []
        
        try:
            with open(verdicts_path, "w", encoding="utf-8") as f:
                json.dump(verdicts_data, f, ensure_ascii=False, indent=2)
        except IOError as e:
            print(f"[entity-check] WARNING: Cannot write verdicts: {e}", file=sys.stderr)

    facts = _first_existing(folder / "facts.json", work_dir / "facts.json")
    if facts is None:
        raise SystemExit(f"[pipeline] missing facts.json in {folder} or {work_dir}")

    facts_ctx = work_dir / "facts_ctx.json"
    verdicts = work_dir / "verdicts.json"
    corr = work_dir / "correlations.json"
    manifest_path = work_dir / "upload_manifest.json"

    # [1/4] context
    ctx_data = context.load(folder)
    facts_list = json.loads(facts.read_text(encoding="utf-8"))
    enriched = context.apply(facts_list, ctx_data or {})
    facts_ctx.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[1/4] context   -> {facts_ctx}")

    # [2/5] engine
    rules.main([str(facts_ctx), "-o", str(verdicts), "--current-year", str(args.current_year), "--upload-policy", args.upload_policy])
    print(f"[2/5] engine    -> {verdicts}")

    # Template validation
    _check_templates(verdicts)

    # [3/5] wikidata
    wikidata.main([str(verdicts), "--context", str(folder), "-o", str(verdicts)])
    print(f"[3/5] wikidata  -> {verdicts}")
    if (folder / "upload_manifest.json").exists():
        shutil.move(str(folder / "upload_manifest.json"), str(manifest_path))
        print(f"[3b] manifest -> {manifest_path}")

    # Entity validation
    _check_entities(verdicts)

    # Category validation
    _check_categories(verdicts)

    # [4/6] correlate
    verdicts_data = json.loads(verdicts.read_text(encoding="utf-8"))
    corr_data = context.correlate_group(verdicts_data)
    corr.write_text(json.dumps(corr_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[4/6] correlate -> {corr}")

    # [5/6] report
    report.main([str(verdicts), "-o", str(ROOT / "dashboard.html"), "--images-base", args.images_base, "--context", str(folder), "--upload-policy", args.upload_policy])
    report.main([str(verdicts), "-o", str(folder / "dashboard.html"), "--images-base", "..", "--context", str(folder), "--upload-policy", args.upload_policy])
    print(f"[5/6] report    -> {ROOT / 'dashboard.html'}  and  {folder / 'dashboard.html'}")

    data = json.loads(verdicts.read_text(encoding="utf-8"))
    print(f"\n== Verdicts ({len(data['items'])} items, {data['rule_table_version']}, {data['current_year']}) ==")
    for item in data["items"]:
        upload = item.get("upload", {}).get("flag", "-")
        print(f'  {item["file"]:32} {item["status"]:15} conf={item["confidence"]:6} upload={upload}')

    groups = json.loads(corr.read_text(encoding="utf-8"))["groups"]
    print("== Correlations ==")
    for group in groups:
        print(f'  {group["set_id"]:16} files={len(group["files"])} warnings={len(group["warnings"])}')

    # A root context.json/run.json is a one-time seed; afterwards pubdom.json is canonical.
    previous = _load_pubdom(folder)
    ctx_data = _read_json(folder / "context.json") or previous.get("context")
    run_data = _read_json(folder / "run.json") or previous.get("run")

    pubdom = {
        "folder": folder.name,
        "generated": date.today().isoformat(),
        "rule_table_version": data.get("rule_table_version", "unknown"),
        "current_year": data.get("current_year", args.current_year),
        "context": ctx_data,
        "run": run_data,
        "items": data.get("items", []),
        "correlations": json.loads(corr.read_text(encoding="utf-8")),
        "manifest": _read_json(manifest_path),
    }
    pubdom_path = folder / "pubdom.json"
    pubdom_path.write_text(json.dumps(pubdom, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[6/6] consolidate -> {pubdom_path}")

    for stray in (folder / "facts.json", folder / "context.json", folder / "run.json"):
        if stray.exists():
            shutil.move(str(stray), str(work_dir / stray.name))

    print("== Suggestions ==")
    verdicts_data = json.loads(verdicts.read_text(encoding="utf-8"))
    for s in context.suggestions(ctx_data or {}, verdicts_data):
        print(s)

    image_suffixes = (".jpg", ".jpeg", ".png", ".gif", ".webp")
    images = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in image_suffixes]
    root_files = [p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() not in image_suffixes]
    print(f"\n== Layout di {folder.name}/ ==")
    print("  " + (", ".join(sorted(root_files)) if root_files else "(solo immagini)"))
    print(f"  pd_data/: {len(list(work_dir.iterdir()))} file (scratch)")
    print(f"  {len(images)} immagini")
    return 0


def cmd_status(argv: list[str]) -> int:
    """OFFLINE: Read <folder>/pubdom.json and print a one-screen summary."""
    parser = argparse.ArgumentParser(
        description="OFFLINE: Read pubdom.json and print a one-screen summary."
    )
    parser.add_argument("folder", help="Material folder containing pubdom.json")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    pubdom_path = folder / "pubdom.json"

    if not pubdom_path.exists():
        print(f"[status] ERROR: pubdom.json not found in {folder}", file=sys.stderr)
        return 1

    try:
        pubdom = json.loads(pubdom_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[status] ERROR: Cannot read pubdom.json: {e}", file=sys.stderr)
        return 1

    if not isinstance(pubdom, dict):
        print("[status] ERROR: pubdom.json is not a valid JSON object", file=sys.stderr)
        return 1

    # Extract summary data
    folder_name = pubdom.get("folder", folder.name)
    generated = pubdom.get("generated", "unknown")
    rule_table_version = pubdom.get("rule_table_version", "unknown")
    items = pubdom.get("items", [])
    n_items = len(items)

    # Count by status
    status_counts = {"public_domain": 0, "not_public_domain": 0, "uncertain": 0}
    for item in items:
        status = item.get("status")
        if status in status_counts:
            status_counts[status] += 1

    # Count by upload flag
    upload_counts = {"ready": 0, "review": 0, "no": 0}
    for item in items:
        upload = item.get("upload") if isinstance(item, dict) else None
        if upload and isinstance(upload, dict):
            flag = upload.get("flag")
            if flag in upload_counts:
                upload_counts[flag] += 1

    # Count items with non-empty template_unknown / entity_unknown
    template_unknown_count = 0
    entity_unknown_count = 0
    for item in items:
        if item.get("template_unknown"):
            template_unknown_count += 1
        if item.get("entity_unknown"):
            entity_unknown_count += 1

    # Print summary
    print(f"== {folder_name} ==")
    print(f"Generated: {generated}")
    print(f"Rule table version: {rule_table_version}")
    print(f"Items: {n_items}")
    print()
    print("By status:")
    print(f"  public_domain:     {status_counts['public_domain']}")
    print(f"  not_public_domain: {status_counts['not_public_domain']}")
    print(f"  uncertain:         {status_counts['uncertain']}")
    print()
    print("By upload flag:")
    print(f"  ready:   {upload_counts['ready']}")
    print(f"  review:  {upload_counts['review']}")
    print(f"  no:      {upload_counts['no']}")
    print()
    print("Validation flags:")
    print(f"  template_unknown:  {template_unknown_count}")
    print(f"  entity_unknown:    {entity_unknown_count}")

    return 0


def cmd_validate(argv: list[str]) -> int:
    """OFFLINE: Run template check AND entity check on <folder>/pd_data/verdicts.json."""
    # Lazy imports
    from pd_checker.core import catalogs
    templates = catalogs
    wd_entities = catalogs
    
    parser = argparse.ArgumentParser(
        description="OFFLINE: Run template check AND entity check on verdicts.json."
    )
    parser.add_argument("folder", help="Material folder containing pd_data/verdicts.json")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    verdicts_path = folder / "pd_data" / "verdicts.json"

    if not verdicts_path.exists():
        print(f"[validate] ERROR: verdicts.json not found in {folder}/pd_data/", file=sys.stderr)
        return 1

    problems = []

    # Template check
    try:
        unknown_by_file, has_unknown = templates.check_verdicts(verdicts_path)
        if has_unknown:
            for file_path, templates_list in unknown_by_file:
                for t in templates_list:
                    problems.append(f"{file_path}: unknown template {{{{{t}}}}}")
    except Exception as e:
        print(f"[validate] WARNING: Template check failed: {e}", file=sys.stderr)

    # Entity check
    try:
        unknown_ids, has_unknown = wd_entities.check_file(verdicts_path)
        if has_unknown:
            for entity_id in unknown_ids:
                problems.append(f"unknown entity: {entity_id}")
    except Exception as e:
        print(f"[validate] WARNING: Entity check failed: {e}", file=sys.stderr)

    # Print results
    if problems:
        print("PROBLEMS FOUND:")
        for problem in problems:
            print(f"  {problem}")
        print(f"\nTotal: {len(problems)} problem(s)")
        return 1
    else:
        print("OK: All templates and entities are valid")
        return 0


def cmd_verify_live(argv: list[str]) -> int:
    """NETWORK: Verify templates live against the Commons API."""
    # Lazy import
    from pd_checker.core import catalogs
    templates = catalogs
    
    parser = argparse.ArgumentParser(
        description="NETWORK: Verify templates live against the Commons API."
    )
    parser.add_argument("folder", help="Material folder containing pd_data/verdicts.json")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    verdicts_path = folder / "pd_data" / "verdicts.json"

    if not verdicts_path.exists():
        print(f"[verify-live] ERROR: verdicts.json not found in {folder}/pd_data/", file=sys.stderr)
        return 1

    # Collect distinct template names
    verdicts_data = _load_verdicts(verdicts_path)
    if not verdicts_data:
        print(f"[verify-live] ERROR: Cannot read verdicts file", file=sys.stderr)
        return 1

    all_templates = set()
    items = verdicts_data.get("items", [])
    for item in items:
        templates_list = item.get("commons_templates", [])
        for t in templates_list:
            normalized = templates.normalize(t)
            if normalized:
                all_templates.add(normalized)

    if not all_templates:
        print("[verify-live] OK: No templates to verify")
        return 0

    print(f"[verify-live] Verifying {len(all_templates)} distinct template(s) against Commons API...")
    results = templates.verify_live(sorted(all_templates))

    # Print results
    has_missing = False
    for name in sorted(results.keys()):
        info = results[name]
        status = info["status"]
        redirect_to = info.get("redirect_to")

        if status == "missing":
            print(f"[verify-live] {name} -> MISSING")
            has_missing = True
        elif status == "redirect":
            print(f"[verify-live] {name} -> redirect to {redirect_to}")
        else:
            print(f"[verify-live] {name} -> exists")

    if has_missing:
        missing_names = [n for n, info in results.items() if info["status"] == "missing"]
        print(f"[verify-live] ERROR: {len(missing_names)} template(s) missing from Commons: {', '.join(missing_names)}")
        return 1
    else:
        print("[verify-live] OK: All templates exist on Commons")
        return 0


def cmd_update(argv: list[str]) -> int:
    """NETWORK: Refresh the Commons template catalog and the Wikidata entity catalog."""
    # Lazy imports
    from pd_checker.core import catalogs
    templates = catalogs
    wd_entities = catalogs
    wd_cache = catalogs
    
    print("Updating Commons template catalog...")
    templates_success = templates.update_catalog()

    print("\nUpdating Wikidata entity catalog...")
    # Scan package for all IDs
    package_dir = HERE
    ids = wd_entities.scan_package_for_ids(package_dir)

    if not ids:
        print("ERROR: No entity IDs found in package source", file=sys.stderr)
        return 1

    print(f"Found {len(ids)} entity IDs in package source")
    wd_entities_success = wd_entities.update_catalog(ids)

    # Print counts
    print("\n== Catalog counts ==")

    # Template count
    template_catalog = templates.load()
    print(f"Templates: {len(template_catalog)}")

    # Entity count
    entity_catalog = wd_entities.load()
    print(f"Properties: {len(entity_catalog.get('properties', {}))}")
    print(f"Items: {len(entity_catalog.get('items', {}))}")

    return 0 if (templates_success and wd_entities_success) else 1


def cmd_templates_enrich(argv: list[str]) -> int:
    """NETWORK: Enrich the template catalog with category metadata."""
    from pd_checker.core import catalogs
    templates = catalogs
    
    success = templates.enrich_catalog()
    return 0 if success else 1


def cmd_templates_suggest(argv: list[str]) -> int:
    """OFFLINE: Suggest templates based on country/anonymous/restrictions filters."""
    from pd_checker.core import catalogs
    templates = catalogs
    
    parser = argparse.ArgumentParser(
        description="OFFLINE: Suggest templates based on filters."
    )
    parser.add_argument(
        "--country",
        metavar="CODE",
        type=str,
        help="Country code (IT, GB, VA, US, EU, or custom)"
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Include anonymous templates (PD-anon)"
    )
    parser.add_argument(
        "--restrictions",
        action="store_true",
        help="Include non-copyright restriction templates"
    )
    args = parser.parse_args(argv)
    
    if not args.country and not args.anonymous and not args.restrictions:
        print("ERROR: --suggest requires at least one of --country, --anonymous, or --restrictions", file=sys.stderr)
        return 1
    
    return templates.suggest_templates(args.country, args.anonymous, args.restrictions)


def cmd_templates_search(argv: list[str]) -> int:
    """OFFLINE: Search templates by name or category."""
    from pd_checker.core import catalogs
    templates = catalogs
    
    parser = argparse.ArgumentParser(
        description="OFFLINE: Search templates by name or category."
    )
    parser.add_argument("term", help="Search term")
    args = parser.parse_args(argv)
    
    return templates.search_templates(args.term)


def cmd_resolve(argv: list[str]) -> int:
    """OFFLINE cache-first entity resolution."""
    # Lazy import
    from pd_checker.core import catalogs
    wd_cache = catalogs
    
    parser = argparse.ArgumentParser(
        description="OFFLINE cache-first entity resolution."
    )
    parser.add_argument("name", help="Name to resolve to QID")
    args = parser.parse_args(argv)

    # Cache-first lookup (no network)
    qid = wd_cache.lookup(args.name)

    if qid:
        print(qid)
        return 0
    else:
        print("not_found")
        return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point for pd_checker unified CLI."""
    parser = argparse.ArgumentParser(
        description="Unified entry point for pd_checker - Italian/UK/Vatican public-domain checker."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # run subcommand
    run_parser = subparsers.add_parser(
        "run",
        help="Run the whole pd_checker pipeline for a folder",
        description="Run the whole pd_checker pipeline for a material folder with one command."
    )
    run_parser.add_argument("folder", help="Material folder (needs facts.json; context/run optional).")
    run_parser.add_argument("--images-base", default=".", help="Image base for thumbnails in the root dashboard.")
    run_parser.add_argument("--current-year", type=int, default=date.today().year)
    run_parser.add_argument(
        "--upload-policy",
        choices=["strict", "pd-only", "all"],
        default="strict",
        help="Upload policy: 'strict' (default), 'pd-only' (copyright-only reading), or 'all' (ready all items, including uncertain)."
    )

    # status subcommand
    status_parser = subparsers.add_parser(
        "status",
        help="Read pubdom.json and print a one-screen summary",
        description="OFFLINE: Read <folder>/pubdom.json and print a one-screen summary: folder, generated date, rule-table version, N items, counts per status, counts per upload flag, and the number of items with a non-empty template_unknown / entity_unknown. Exit 1 if the file is missing."
    )
    status_parser.add_argument("folder", help="Material folder containing pubdom.json")

    # validate subcommand
    validate_parser = subparsers.add_parser(
        "validate",
        help="Run template check AND entity check on verdicts.json",
        description="OFFLINE: Run the template check (templates.py) AND the entity check (wd_entities.py) on <folder>/pd_data/verdicts.json; print one line per problem; exit 1 if anything is unknown, 0 if clean."
    )
    validate_parser.add_argument("folder", help="Material folder containing pd_data/verdicts.json")

    # verify-live subcommand
    verify_live_parser = subparsers.add_parser(
        "verify-live",
        help="Verify templates live against the Commons API",
        description="NETWORK: Query the Commons API live to verify that every template emitted in verdicts.json actually exists on Wikimedia Commons; print one line per template (exists/missing/redirect); exit 1 if any template is missing, 0 otherwise."
    )
    verify_live_parser.add_argument("folder", help="Material folder containing pd_data/verdicts.json")

    # update subcommand
    update_parser = subparsers.add_parser(
        "update",
        help="Refresh the Commons template catalog and the Wikidata entity catalog",
        description="NETWORK: refresh the Commons template catalog and the Wikidata entity catalog (delegate to templates.py --update and wd_entities.py --update-entities), then print the new counts."
    )

    # resolve subcommand
    resolve_parser = subparsers.add_parser(
        "resolve",
        help="Cache-first entity resolution",
        description="OFFLINE cache-first entity resolution (delegate to wd_cache); print the QID or not_found."
    )
    resolve_parser.add_argument("name", help="Name to resolve to QID")

    # templates-enrich subcommand
    subparsers.add_parser(
        "templates-enrich",
        help="Enrich the template catalog with category metadata (network)",
        description="NETWORK: Fetch categories for every template in the catalog and store them in a 'meta' section; requires --update to have run first."
    )

    # templates-suggest subcommand
    templates_suggest_parser = subparsers.add_parser(
        "templates-suggest",
        help="Suggest templates based on filters (offline)",
        description="OFFLINE: Suggest templates based on country/anonymous/restrictions filters; requires --enrich to have run first."
    )
    templates_suggest_parser.add_argument(
        "--country",
        metavar="CODE",
        type=str,
        help="Country code (IT, GB, VA, US, EU, or custom)"
    )
    templates_suggest_parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Include anonymous templates (PD-anon)"
    )
    templates_suggest_parser.add_argument(
        "--restrictions",
        action="store_true",
        help="Include non-copyright restriction templates"
    )

    # templates-search subcommand
    templates_search_parser = subparsers.add_parser(
        "templates-search",
        help="Search templates by name or category (offline)",
        description="OFFLINE: Search templates by name or category; requires --enrich to have run first."
    )
    templates_search_parser.add_argument("term", help="Search term")

    # preflight subcommand
    preflight_parser = subparsers.add_parser(
        "preflight",
        help="Validate upload package before Commons upload",
        description="Preflight validator for Commons/Wikidata upload packages. Computes SHA1 for each image, checks duplicates on Commons (live by default, use --offline to skip), validates licence tags, categories, Wikidata entities, and {{Information}} completeness. Exit 1 if any item is blocked."
    )
    preflight_parser.add_argument("folder", help="Material folder containing pubdom.json")
    preflight_parser.add_argument(
        "--offline",
        action="store_true",
        help="Run only local checks (no network duplicate check)"
    )

    # push subcommand
    push_parser = subparsers.add_parser(
        "push",
        help="Upload ready items to Wikimedia Commons",
        description="Upload items with upload.flag='ready' to Wikimedia Commons. DRY-RUN by default (no network, no credentials needed). Use --live for actual upload. Credentials via env vars: PD_WM_OAUTH_TOKEN or PD_WM_BOT_USER+PD_WM_BOT_PASSWORD."
    )
    push_parser.add_argument("folder", help="Material folder containing pubdom.json")
    push_parser.add_argument(
        "--filename-prefix",
        metavar="PREFIX",
        type=str,
        help="Prefix for Commons filenames (REQUIRED for live upload)"
    )
    push_parser.add_argument(
        "--live",
        action="store_true",
        help="Perform actual upload (default: dry-run)"
    )

    # suggest-country subcommand
    suggest_country_parser = subparsers.add_parser(
        "suggest-country",
        help="Suggest candidate jurisdictions from language and place evidence (offline)",
        description="OFFLINE: Suggest candidate jurisdictions based on language and place evidence; prints country, reason, confidence."
    )
    suggest_country_parser.add_argument(
        "--language",
        metavar="CODE",
        type=str,
        help="ISO 639-1 language code (e.g., 'en', 'it')"
    )
    suggest_country_parser.add_argument(
        "--text",
        metavar="TEXT",
        type=str,
        help="Visible text from the material"
    )
    suggest_country_parser.add_argument(
        "--postmark",
        metavar="TEXT",
        type=str,
        help="Postmark string"
    )
    suggest_country_parser.add_argument(
        "--publisher",
        metavar="TEXT",
        type=str,
        help="Publisher string"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "run":
        return cmd_run([args.folder, "--images-base", args.images_base, "--current-year", str(args.current_year), "--upload-policy", args.upload_policy])
    elif args.command == "status":
        return cmd_status([args.folder])
    elif args.command == "validate":
        return cmd_validate([args.folder])
    elif args.command == "verify-live":
        return cmd_verify_live([args.folder])
    elif args.command == "update":
        return cmd_update([])
    elif args.command == "resolve":
        return cmd_resolve([args.name])
    elif args.command == "templates-enrich":
        return cmd_templates_enrich([])
    elif args.command == "templates-suggest":
        suggest_args = []
        if args.country:
            suggest_args.extend(["--country", args.country])
        if args.anonymous:
            suggest_args.append("--anonymous")
        if args.restrictions:
            suggest_args.append("--restrictions")
        return cmd_templates_suggest(suggest_args)
    elif args.command == "templates-search":
        return cmd_templates_search([args.term])
    elif args.command == "preflight":
        from pd_checker.core import catalogs
        folder = Path(args.folder)
        offline = args.offline
        
        pubdom_path = folder / "pubdom.json"
        if not pubdom_path.exists():
            fallback = folder / "pd_data" / "verdicts.json"
            if not fallback.exists():
                print(f"[preflight] ERROR: Neither pubdom.json nor verdicts.json found in {folder}", file=sys.stderr)
                return 1
            pubdom_path = fallback
        
        try:
            pubdom = json.loads(pubdom_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[preflight] ERROR: Cannot read {pubdom_path}: {e}", file=sys.stderr)
            return 1
        
        if not isinstance(pubdom, dict):
            print("[preflight] ERROR: pubdom.json is not a valid JSON object", file=sys.stderr)
            return 1
        
        items = pubdom.get("items", [])
        if not items:
            print("[preflight] ERROR: No items found in pubdom.json", file=sys.stderr)
            return 1
        
        cat_cache_lookup = catalogs.lookup_cat
        # unknown_entities takes a list of IDs and returns unknown ones
        
        sha1_list = []
        for item in items:
            file_path = item.get("file", "")
            images = item.get("images", [])
            for img in images:
                img_path = folder / img if not img.startswith("/") else Path(img)
                sha1 = compute_sha1(img_path)
                if sha1:
                    sha1_list.append((file_path or img, sha1))
        
        try:
            commons_duplicates = check_commons_duplicates(sha1_list, offline=offline)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
            print(f"[preflight] ERROR: Network failure during live duplicate check: {e}", file=sys.stderr)
            print("[preflight] Re-run with --offline or fix network connectivity.", file=sys.stderr)
            return 1
        
        if not offline and commons_duplicates:
            print(f"[preflight] Live duplicate check: {len(commons_duplicates)} file(s) found on Commons")
        elif offline:
            print("[preflight] Offline mode: skipping Commons duplicate check")
        
        ready_count = 0
        blocked_count = 0
        
        print(f"\n== Preflight check: {folder.name} ==")
        print(f"Items: {len(items)}")
        print(f"Mode: {'offline' if offline else 'live'}")
        print()
        
        for item in items:
            file_path = item.get("file", "unknown")
            
            template_unknown = item.get("template_unknown", [])
            
            entity_ids = []
            statements = item.get("wikidata", {}).get("statements", [])
            if isinstance(statements, list):
                for stmt in statements:
                    if isinstance(stmt, dict):
                        qid = stmt.get("qid")
                        if qid and isinstance(qid, str):
                            entity_ids.append(qid)
            
            wd_unknown = catalogs.unknown_entities(entity_ids) if entity_ids else []
            if item.get("entity_unknown"):
                wd_unknown = list(set(wd_unknown + item.get("entity_unknown", [])))
            
            result = validate_item(
                item,
                folder,
                offline,
                template_unknown,
                cat_cache_lookup,
                wd_unknown,
                commons_duplicates
            )
            
            status = "READY" if result["ready"] else "BLOCKED"
            print(f"[{status}] {file_path}")
            for reason in result["reasons"]:
                print(f"         - {reason}")
            print()
            
            if result["ready"]:
                ready_count += 1
            else:
                blocked_count += 1
        
        print("== Summary ==")
        print(f"Ready:   {ready_count}")
        print(f"Blocked: {blocked_count}")
        print()
        
        if blocked_count > 0:
            print("RESULT: BLOCKED - Fix the issues above before uploading.")
            return 1
        else:
            print("RESULT: READY - All items pass preflight checks.")
            return 0
    elif args.command == "suggest-country":
        from pd_checker.core import context
        # Build args list for context.suggest_main
        suggest_args_list = []
        if args.language:
            suggest_args_list.extend(["--language", args.language])
        if args.text:
            suggest_args_list.extend(["--text", args.text])
        if args.postmark:
            suggest_args_list.extend(["--postmark", args.postmark])
        if args.publisher:
            suggest_args_list.extend(["--publisher", args.publisher])
        return context.suggest_main(suggest_args_list)
    elif args.command == "push":
        from pd_checker.core import push
        # Pass live flag from args
        push_argv = [args.folder]
        if args.filename_prefix:
            push_argv.extend(["--filename-prefix", args.filename_prefix])
        if args.live:
            push_argv.append("--live")
        return push.cmd_push(push_argv, live=args.live)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
