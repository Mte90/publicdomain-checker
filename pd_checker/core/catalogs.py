#!/usr/bin/env python3
"""Unified catalog module for pd_checker.

Merges four near-duplicate catalogue modules into one:
- Commons template catalogue (templates.py)
- Wikidata entity catalogue (wd_entities.py)
- Name→QID cache (wd_cache.py)
- Commons category cache (cat_cache.py)

All public functions and constants are preserved for backward compatibility.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# COMMON USER-AGENT FOR ALL MEDIAWIKI API CALLS
_MW_USER_AGENT = "pubdom-checker/1.0 (mte90@github)"

# ============================================================================
# DATA DIRECTORY (single source of truth for all JSON files)
# ============================================================================

# Data directory is the 'data/' sibling of this core/ subpackage
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# ============================================================================
# COMMONS TEMPLATE CATALOGUE (from templates.py)
# ============================================================================

# Path to the catalog file (in the data/ directory)
TEMPLATE_CATALOG_PATH = DATA_DIR / "commons_templates.json"

# MediaWiki API endpoint for Category:Primary license tags (flat list)
_TEMPLATE_API_URL = (
    "https://commons.wikimedia.org/w/api.php"
    "?action=query"
    "&list=categorymembers"
    "&cmtitle=Category:Primary_license_tags_(flat_list)"
    "&cmtype=page"
    "&ctype=json"
    "&format=json"
    "&cmlimit=500"
)

# Required User-Agent (MediaWiki API returns 403 without one)
_TEMPLATE_USER_AGENT = _MW_USER_AGENT


def load_templates() -> set[str]:
    """Load the Commons template catalog from disk.
    
    Returns:
        A set of valid template names (bare names, no braces, no "Template:" prefix).
        
    Raises:
        FileNotFoundError: If the catalog file does not exist.
        json.JSONDecodeError: If the catalog file contains invalid JSON.
        IOError: If the catalog file cannot be read.
        ValueError: If the catalog structure is invalid.
    """
    if not TEMPLATE_CATALOG_PATH.exists():
        raise FileNotFoundError(f"Template catalog not found at {TEMPLATE_CATALOG_PATH}")
    
    try:
        with open(TEMPLATE_CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        templates = data.get("templates", [])
        if not isinstance(templates, list):
            raise ValueError(f"'templates' key is not a list in {TEMPLATE_CATALOG_PATH}")
        
        # Validate all entries are strings
        valid = set()
        for t in templates:
            if isinstance(t, str):
                valid.add(t)
            else:
                raise ValueError(f"Non-string template entry found in {TEMPLATE_CATALOG_PATH}: {t!r}")
        
        return valid
    
    except (json.JSONDecodeError, IOError, ValueError):
        raise


# Alias for backward compatibility
load = load_templates


def normalize(name: str) -> str:
    """Normalize a template name.
    
    Strips surrounding braces, whitespace, and drops a leading "Template:" prefix.
    
    Examples:
        "{{PD-Italy}}" -> "PD-Italy"
        " {{PD-1996}} " -> "PD-1996"
        "Template:PD-anon-70-EU" -> "PD-anon-70-EU"
        "{{Template:PD-Italy}}" -> "PD-Italy" (strips braces then prefix)
        "{}" -> ""
    """
    # Strip whitespace
    name = name.strip()
    
    # Strip surrounding {{ }} (can be applied multiple times for nested braces)
    while len(name) >= 4 and name.startswith("{{") and name.endswith("}}"):
        name = name[2:-2].strip()
    
    # Also strip single braces {}
    while len(name) >= 2 and name.startswith("{") and name.endswith("}"):
        name = name[1:-1].strip()
    
    # Drop leading "Template:" prefix (case-insensitive)
    if name.lower().startswith("template:"):
        name = name[9:]  # len("Template:") == 9
    
    # Strip again after removing prefix
    name = name.strip()
    
    return name


def unknown(names: list[str] | str, catalog: set[str] | None = None) -> list[str]:
    """Return the list of template names not present in the catalog.
    
    Args:
        names: A list of template names (as emitted by the engine, e.g. "{{PD-Italy}}")
               or a single string (normalized or not).
        catalog: Optional pre-loaded catalog. If None, loads from disk.
    
    Returns:
        A list of normalized names that are NOT in the catalog.
        The returned names are already normalized (no braces, no "Template:" prefix).
    """
    if catalog is None:
        catalog = load()
    
    # Handle single string input
    if isinstance(names, str):
        names = [names]
    
    result = []
    for name in names:
        normalized = normalize(name)
        if normalized and normalized not in catalog:
            result.append(normalized)
    
    return result


def update_catalog() -> bool:
    """Download the latest template catalog from Wikimedia Commons.
    
    Returns True on success, False on failure (with error printed to stderr).
    """
    # Build paginated request
    all_pages = []
    cmcontinue = None
    
    while True:
        url = _TEMPLATE_API_URL
        if cmcontinue:
            url += f"&cmcontinue={cmcontinue}"
        
        req = Request(url, headers={"User-Agent": _TEMPLATE_USER_AGENT})
        
        try:
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            print(f"ERROR: HTTP {e.code} from MediaWiki API: {e.reason}", file=sys.stderr)
            return False
        except URLError as e:
            print(f"ERROR: Failed to connect to MediaWiki API: {e.reason}", file=sys.stderr)
            return False
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON response from API: {e}", file=sys.stderr)
            return False
        
        # Extract page titles from categorymembers
        query = data.get("query", {})
        members = query.get("categorymembers", [])
        
        for member in members:
            title = member.get("title", "")
            # Strip "Template:" prefix if present in the title
            if title.lower().startswith("template:"):
                title = title[9:]
            if title:
                all_pages.append(title)
        
        # Check for continuation
        if "continue" in data:
            cmcontinue = data["continue"].get("cmcontinue")
            if not cmcontinue:
                break
        else:
            break
    
    if not all_pages:
        print("ERROR: No templates found in catalog", file=sys.stderr)
        return False
    
    # Build catalog structure
    catalog_data = {
        "note": "Flat list of template names from Wikimedia Commons Category:Primary license tags (flat list)",
        "source": "https://commons.wikimedia.org/wiki/Category:Primary_license_tags_(flat_list)",
        "fetched": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
        "count": len(set(all_pages)),
        "templates": sorted(set(all_pages))  # Dedupe and sort
    }
    
    # Write to file
    try:
        with open(TEMPLATE_CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(catalog_data, f, ensure_ascii=False, indent=2)
        print(f"OK: Updated catalog with {len(all_pages)} templates -> {TEMPLATE_CATALOG_PATH}")
        return True
    except IOError as e:
        print(f"ERROR: Cannot write catalog to {TEMPLATE_CATALOG_PATH}: {e}", file=sys.stderr)
        return False


def check_verdicts(verdicts_path: Path) -> tuple[list[tuple[str, list[str]]], bool]:
    """Check all templates in a verdicts file against the catalog.
    
    Args:
        verdicts_path: Path to the verdicts JSON file.
    
    Returns:
        A tuple of (unknown_templates_by_file, has_any_unknown).
        unknown_templates_by_file is a list of (file_path, [unknown_template_names]).
    """
    if not verdicts_path.exists():
        print(f"ERROR: Verdicts file not found: {verdicts_path}", file=sys.stderr)
        return [], True
    
    try:
        with open(verdicts_path, "r", encoding="utf-8") as f:
            verdicts_data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Cannot read verdicts file: {e}", file=sys.stderr)
        return [], True
    
    catalog = load()
    unknown_by_file = []
    
    items = verdicts_data.get("items", [])
    for item in items:
        file_path = item.get("file", "unknown")
        templates_list = item.get("commons_templates", [])
        
        if templates_list:
            found_unknown = unknown(templates_list, catalog)
            if found_unknown:
                unknown_by_file.append((file_path, found_unknown))
    
    return unknown_by_file, len(unknown_by_file) > 0


def verify_live(names: list[str]) -> dict[str, dict]:
    """Query the Commons API to verify template existence live.
    
    Args:
        names: List of template names (normalized or not, e.g. "PD-Italy" or "{{PD-Italy}}").
    
    Returns:
        A dict mapping each normalized name to:
        {
            "status": "exists" | "missing" | "redirect",
            "redirect_to": str | None  # target if status == "redirect"
        }
    
    Batches requests by joining titles with "|" (about 50 titles per request).
    Uses the existing User-Agent header; fails with exit code 1 on HTTP/network error.
    """
    # Normalize all names first
    normalized_names = [normalize(name) for name in names if normalize(name)]
    
    results: dict[str, dict] = {}
    
    # Batch size: ~50 titles per request
    batch_size = 50
    
    for i in range(0, len(normalized_names), batch_size):
        batch = normalized_names[i:i + batch_size]
        
        # Build titles parameter: "Template:Name1|Template:Name2|..."
        titles = "|".join(f"Template:{name}" for name in batch)
        
        url = (
            "https://commons.wikimedia.org/w/api.php"
            f"?action=query"
            f"&titles={titles}"
            f"&redirects=1"
            f"&format=json"
        )
        
        req = Request(url, headers={"User-Agent": _TEMPLATE_USER_AGENT})
        
        try:
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            print(f"ERROR: HTTP {e.code} from MediaWiki API: {e.reason}", file=sys.stderr)
            sys.exit(1)
        except URLError as e:
            print(f"ERROR: Failed to connect to MediaWiki API: {e.reason}", file=sys.stderr)
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON response from API: {e}", file=sys.stderr)
            sys.exit(1)
        
        # Process results
        query = data.get("query", {})
        pages = query.get("pages", {})
        
        # pages is a dict keyed by pageid; -1 means missing
        for pageid, page_data in pages.items():
            title = page_data.get("title", "")
            missing = "missing" in page_data  # API returns "missing": "" for missing pages
            redirect = page_data.get("redirect", {})
            redirect_to = redirect.get("to") if redirect else None
            
            # Extract template name (strip "Template:" prefix)
            if title.lower().startswith("template:"):
                name = title[9:]
            else:
                name = title
            
            if missing:
                results[name] = {"status": "missing", "redirect_to": None}
            elif redirect_to:
                results[name] = {"status": "redirect", "redirect_to": redirect_to}
            else:
                results[name] = {"status": "exists", "redirect_to": None}
    
    return results


def enrich_catalog() -> bool:
    """Fetch categories for every template and store them in meta.
    
    For every name in the catalog, fetch its categories via prop=categories.
    Batches about 50 titles per request joined with "|", uses cllimit=max,
    the existing User-Agent, pacing between successful batches (2s default),
    honors Retry-After header on 429, retries with backoff, and saves
    incrementally after each batch. On total failure leaves the existing
    file untouched.
    
    Returns True on success, False on failure.
    """
    # Load existing catalog
    if not TEMPLATE_CATALOG_PATH.exists():
        print(f"ERROR: Catalog not found at {TEMPLATE_CATALOG_PATH}; run --update first", file=sys.stderr)
        return False
    
    try:
        with open(TEMPLATE_CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Cannot read catalog: {e}", file=sys.stderr)
        return False
    
    templates_list = data.get("templates", [])
    if not templates_list:
        print("ERROR: No templates in catalog", file=sys.stderr)
        return False
    
    # Build meta dict
    meta: dict[str, dict] = {}
    batch_size = 5  # Very small batches to avoid rate limiting
    total = len(templates_list)
    
    print(f"Fetching categories for {total} templates...")
    
    for i in range(0, total, batch_size):
        batch = templates_list[i:i + batch_size]
        
        # Build titles parameter: "Template:Name1|Template:Name2|..."
        # URL-encode each title to handle spaces and special characters
        encoded_titles = "|".join(quote(f"Template:{name}", safe="") for name in batch)
        
        url = (
            "https://commons.wikimedia.org/w/api.php"
            "?action=query"
            f"&titles={encoded_titles}"
            "&prop=categories"
            "&cllimit=max"
            "&format=json"
        )
        
        req = Request(url, headers={"User-Agent": _TEMPLATE_USER_AGENT})
        
        # Retry up to 3 times on 429 with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with urlopen(req, timeout=30) as response:
                    api_data = json.loads(response.read().decode("utf-8"))
                break  # Success
            except HTTPError as e:
                if e.code == 429:
                    # Honor Retry-After header if present
                    retry_after = e.headers.get("Retry-After")
                    if retry_after:
                        try:
                            wait_time = int(retry_after)
                        except ValueError:
                            wait_time = 10 * (attempt + 1)
                    else:
                        wait_time = 10 * (attempt + 1)  # 10s, 20s, 30s
                    print(f"HTTP 429 (rate limit), attempt {attempt+1}/{max_retries}, waiting {wait_time}s...", file=sys.stderr)
                    time.sleep(wait_time)
                    if attempt == max_retries - 1:
                        print(f"ERROR: Rate limit exceeded after {max_retries} retries", file=sys.stderr)
                        return False
                    continue
                print(f"ERROR: HTTP {e.code} from MediaWiki API: {e.reason}", file=sys.stderr)
                return False
            except URLError as e:
                print(f"ERROR: Failed to connect to MediaWiki API: {e.reason}", file=sys.stderr)
                return False
            except json.JSONDecodeError as e:
                print(f"ERROR: Invalid JSON response from API: {e}", file=sys.stderr)
                return False
        
        # Process results
        query = api_data.get("query", {})
        pages = query.get("pages", {})
        
        for pageid, page_data in pages.items():
            title = page_data.get("title", "")
            categories = page_data.get("categories", [])
            
            # Extract category names (strip "Category:" prefix)
            cat_names = []
            for cat in categories:
                cat_title = cat.get("title", "")
                if cat_title.lower().startswith("category:"):
                    cat_title = cat_title[9:]
                if cat_title:
                    cat_names.append(cat_title)
            
            # Extract template name (strip "Template:" prefix)
            if title.lower().startswith("template:"):
                name = title[9:]
            else:
                name = title
            
            if name:
                meta[name] = {"categories": sorted(cat_names)}
        
        # Pace successful batches: 2 second delay between batches
        if i + batch_size < total:
            time.sleep(2.0)
        
        # Incremental save after each batch to preserve progress
        try:
            data["meta"] = meta
            with open(TEMPLATE_CATALOG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"  Saved batch {i//batch_size + 1} ({len(meta)} templates so far)")
        except IOError as e:
            print(f"WARNING: Could not save progress: {e}", file=sys.stderr)
    
    print(f"OK: Enriched catalog with categories for {len(meta)} templates -> {TEMPLATE_CATALOG_PATH}")
    return True


def suggest_templates(country: str | None, anonymous: bool, restrictions: bool) -> int:
    """Suggest templates based on country/anonymous/restrictions filters.
    
    OFFLINE: Filter the enriched catalog by category tokens and print candidates.
    
    Returns exit code (0 success, 1 no catalog/meta, 2 no matches).
    """
    if not TEMPLATE_CATALOG_PATH.exists():
        print("ERROR: Catalog not found; run `templates --enrich` first", file=sys.stderr)
        return 1
    
    try:
        with open(TEMPLATE_CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Cannot read catalog: {e}", file=sys.stderr)
        return 1
    
    meta = data.get("meta")
    if not meta:
        print("ERROR: Catalog has no 'meta' section; run `templates --enrich` first", file=sys.stderr)
        print("       (Questo comando richiede di eseguire prima `templates --enrich` per popolare le categorie)", file=sys.stderr)
        return 1
    
    # Build category mapping
    country_cat_map = {
        "IT": "PD Italy license tags",
        "GB": "PD UK license tags",
        "VA": "PD Vatican license tags",
        "US": "PD US license tags",
        "EU": "PD European Union license tags",
    }
    
    candidates: list[tuple[str, list[str]]] = []
    
    if country:
        # Map country code to category
        if country.upper() in country_cat_map:
            target_cat = country_cat_map[country.upper()]
        else:
            # Try generic pattern: "PD <CODE> license tags"
            target_cat = f"PD {country.upper()} license tags"
        
        # Filter templates whose categories CONTAIN the target category
        # Normalize categories: strip "Category:" prefix for comparison
        for name, info in meta.items():
            cats = info.get("categories", [])
            # Normalize category names (strip "Category:" prefix if present)
            normalized_cats = [
                c[9:] if c.startswith("Category:") else c
                for c in cats
            ]
            # Match if target_cat appears in normalized category list
            if target_cat in normalized_cats:
                candidates.append((name, cats))
        
        # If no matches for country-specific tag, show fallbacks
        if not candidates:
            # Check if this country has any specific tag at all
            has_country_tag = any(
                any(target_cat in c for c in info.get("categories", []))
                for info in meta.values()
            )
            
            if not has_country_tag:
                # No country-specific templates exist; show generic fallbacks
                print(f"No country-specific template found for '{country}'.")
                print("Generic fallbacks:")
                
                # Find PD-old-70 and PD-anon-70-EU
                fallbacks = []
                for name in ["PD-old-70", "PD-anon-70-EU"]:
                    if name in meta:
                        cats = meta[name].get("categories", [])
                        reason = ""
                        if "PD-old license tags" in cats:
                            reason = "for old works (70 years p.m.a.)"
                        elif "PD-anon license tags" in cats or "PD European Union license tags" in cats:
                            reason = "for anonymous/EU works"
                        fallbacks.append((name, reason))
                
                if fallbacks:
                    for name, reason in fallbacks:
                        print(f"  {name} — {reason}")
                else:
                    print("  (no generic fallbacks available)")
                return 0
            else:
                print(f"No templates found with category '{target_cat}'", file=sys.stderr)
                return 2
    
    if anonymous:
        anon_cats = ["PD-anon license tags", "PD-anon-70-EU license tags"]
        for name, info in meta.items():
            cats = info.get("categories", [])
            if any(ac in cats for ac in anon_cats):
                if (name, cats) not in candidates:
                    candidates.append((name, cats))
    
    if restrictions:
        restriction_cat = "Non-copyright restriction templates (flat list)"
        for name, info in meta.items():
            cats = info.get("categories", [])
            if restriction_cat in cats:
                if (name, cats) not in candidates:
                    candidates.append((name, cats))
    
    if not candidates:
        print("No matching templates found")
        return 2
    
    # Print numbered list
    print(f"Candidates ({len(candidates)}):")
    for i, (name, cats) in enumerate(sorted(candidates), 1):
        matching_cats = cats[:5]  # Show first 5 categories
        print(f"  {i}. {name}")
        print(f"     Categories: {', '.join(matching_cats)}")
    
    return 0


def search_templates(term: str) -> int:
    """Search templates by name or category.
    
    OFFLINE: Match the term (case-insensitive) against template NAMES and their categories.
    
    Returns exit code (0 success, 1 no catalog/meta).
    """
    if not TEMPLATE_CATALOG_PATH.exists():
        print("ERROR: Catalog not found", file=sys.stderr)
        return 1
    
    try:
        with open(TEMPLATE_CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Cannot read catalog: {e}", file=sys.stderr)
        return 1
    
    meta = data.get("meta")
    if not meta:
        print("ERROR: Catalog has no 'meta' section; run `templates --enrich` first", file=sys.stderr)
        return 1
    
    term_lower = term.lower()
    matches: list[tuple[str, list[str]]] = []
    
    for name, info in meta.items():
        cats = info.get("categories", [])
        
        # Check if term matches name
        if term_lower in name.lower():
            matches.append((name, cats))
            continue
        
        # Check if term matches any category
        matching_cats = [c for c in cats if term_lower in c.lower()]
        if matching_cats:
            matches.append((name, matching_cats))
    
    if not matches:
        print(f"No templates found matching '{term}'")
        return 2
    
    # Print results
    print(f"Matches ({len(matches)}):")
    for name, matching_cats in sorted(matches):
        print(f"  {name}")
        print(f"     Matching categories: {', '.join(matching_cats)}")
    
    return 0


# ============================================================================
# WIKIDATA ENTITY CATALOGUE (from wd_entities.py)
# ============================================================================

# Path to the catalog file (same directory as this module)
# Path to the entity catalog file (in the data/ directory)
ENTITY_CATALOG_PATH = DATA_DIR / "wikidata_entities.json"

# Wikidata API endpoint
_WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"

# Required User-Agent (Wikidata API returns 403 without one)
_WIKIDATA_USER_AGENT = _MW_USER_AGENT


def load_entities() -> dict:
    """Load the Wikidata entity catalog from disk.
    
    Returns:
        A dict with shape:
        {"properties": {"P31": {...}, ...}, "items": {"Q192425": {...}, ...}}
        
    Raises:
        FileNotFoundError: If the catalog file does not exist.
        json.JSONDecodeError: If the catalog file contains invalid JSON.
        IOError: If the catalog file cannot be read.
        ValueError: If the catalog structure is invalid.
    """
    if not ENTITY_CATALOG_PATH.exists():
        raise FileNotFoundError(f"Entity catalog not found at {ENTITY_CATALOG_PATH}")
    
    try:
        with open(ENTITY_CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        properties = data.get("properties", {})
        items = data.get("items", {})
        
        if not isinstance(properties, dict):
            raise ValueError(f"'properties' key is not a dict in {ENTITY_CATALOG_PATH}")
        
        if not isinstance(items, dict):
            raise ValueError(f"'items' key is not a dict in {ENTITY_CATALOG_PATH}")
        
        return {"properties": properties, "items": items}
    
    except (json.JSONDecodeError, IOError, ValueError):
        raise


def unknown_entities(ids: list[str], catalog: dict | None = None) -> list[str]:
    """Return the list of entity IDs not present in the catalog.
    
    Args:
        ids: A list of entity IDs (e.g., ["P31", "Q192425"])
        catalog: Optional pre-loaded catalog. If None, loads from disk.
    
    Returns:
        A list of IDs that are NOT in the catalog.
    """
    if catalog is None:
        catalog = load_entities()
    
    result = []
    for entity_id in ids:
        if entity_id.startswith("P"):
            if entity_id not in catalog.get("properties", {}):
                result.append(entity_id)
        elif entity_id.startswith("Q"):
            if entity_id not in catalog.get("items", {}):
                result.append(entity_id)
    
    return result


# Aliases for backward compatibility (old wd_entities.py used load(), unknown())
# Note: wd_entities.load() is now wd_entities_load() to avoid conflict with templates.load()
# Note: wd_entities.unknown() is now wd_entities_unknown() to avoid conflict with templates.unknown()
wd_entities_load = load_entities
wd_entities_unknown = unknown_entities


def extract_entity_ids(text: str) -> list[str]:
    """Extract all P#### and Q#### IDs from a string.
    
    Args:
        text: Any string that may contain Wikidata entity IDs.
    
    Returns:
        A list of unique entity IDs found in the text, in order of appearance.
    """
    pattern = r'\b(P\d+|Q\d+)\b'
    matches = re.findall(pattern, text)
    # Return unique IDs in order of appearance
    seen = set()
    result = []
    for match in matches:
        if match not in seen:
            seen.add(match)
            result.append(match)
    return result


def extract_ids_from_file(file_path: Path) -> list[str]:
    """Extract all P#### and Q#### IDs from a Python source file.
    
    Args:
        file_path: Path to a .py file.
    
    Returns:
        A list of unique entity IDs found in the file.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        return extract_entity_ids(content)
    except IOError:
        return []


def update_entity_catalog(ids: list[str]) -> bool:
    """Fetch entity data from Wikidata and update the catalog.
    
    Args:
        ids: List of entity IDs to fetch (e.g., ["P31", "Q192425", ...])
    
    Returns:
        True on success, False on failure (with error printed to stderr).
    """
    if not ids:
        print("ERROR: No entity IDs to fetch", file=sys.stderr)
        return False
    
    # Split into batches of up to 50 (API limit)
    batch_size = 50
    all_properties = {}
    all_items = {}
    
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        ids_param = "|".join(batch)
        
        url = f"{_WIKIDATA_API_URL}?action=wbgetentities&ids={ids_param}&sites=global&languages=it|en&props=labels|datatype&format=json"
        
        req = Request(url, headers={"User-Agent": _WIKIDATA_USER_AGENT})
        
        try:
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            print(f"ERROR: HTTP {e.code} from Wikidata API: {e.reason}", file=sys.stderr)
            return False
        except URLError as e:
            print(f"ERROR: Failed to connect to Wikidata API: {e.reason}", file=sys.stderr)
            return False
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON response from API: {e}", file=sys.stderr)
            return False
        
        # Extract entities
        entities = data.get("entities", {})
        for entity_id, entity_data in entities.items():
            labels = entity_data.get("labels", {})
            label_it = labels.get("it", {}).get("value", "")
            label_en = labels.get("en", {}).get("value", "")
            
            if entity_id.startswith("P"):
                all_properties[entity_id] = {
                    "label_it": label_it,
                    "label_en": label_en,
                    "datatype": entity_data.get("datatype", "unknown")
                }
            elif entity_id.startswith("Q"):
                all_items[entity_id] = {
                    "label_it": label_it,
                    "label_en": label_en
                }
    
    if not all_properties and not all_items:
        print("ERROR: No entities fetched from API", file=sys.stderr)
        return False
    
    # Build catalog structure
    catalog_data = {
        "note": "Wikidata property and item catalog for pd_checker validation. Fetched via wbgetentities API.",
        "source": "https://www.wikidata.org/w/api.php",
        "fetched": __import__("datetime").datetime.now().strftime("%Y-%m-%d"),
        "properties": all_properties,
        "items": all_items
    }
    
    # Write to file
    try:
        with open(ENTITY_CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(catalog_data, f, ensure_ascii=False, indent=2)
        print(f"OK: Updated catalog with {len(all_properties)} properties and {len(all_items)} items -> {ENTITY_CATALOG_PATH}")
        return True
    except IOError as e:
        print(f"ERROR: Cannot write catalog to {ENTITY_CATALOG_PATH}: {e}", file=sys.stderr)
        return False


def check_file(file_path: Path) -> tuple[list[str], bool]:
    """Check a JSON file for unknown entity IDs.
    
    Args:
        file_path: Path to a JSON file (e.g., verdicts.json).
    
    Returns:
        A tuple of (unknown_ids, has_any_unknown).
    """
    if not file_path.exists():
        print(f"ERROR: File not found: {file_path}", file=sys.stderr)
        return [], True
    
    try:
        content = file_path.read_text(encoding="utf-8")
        # Parse JSON to validate it
        json.loads(content)
        # Extract all entity IDs from the file content
        ids = extract_entity_ids(content)
    except (json.JSONDecodeError, IOError) as e:
        print(f"ERROR: Cannot read file: {e}", file=sys.stderr)
        return [], True
    
    catalog = load_entities()
    unknown_ids = unknown_entities(ids, catalog)
    
    return unknown_ids, len(unknown_ids) > 0


def scan_package_for_ids(package_dir: Path) -> list[str]:
    """Scan all Python files in the package for P#### and Q#### literals.
    
    Args:
        package_dir: Path to the pd_checker package directory.
    
    Returns:
        A list of unique entity IDs found in all .py files.
    """
    all_ids = set()
    
    for py_file in package_dir.glob("*.py"):
        ids = extract_ids_from_file(py_file)
        all_ids.update(ids)
    
    return sorted(all_ids)


# ============================================================================
# NAME→QID CACHE (from wd_cache.py)
# ============================================================================

# Path to the name-to-QID cache file (in the data/ directory)
WD_CACHE_PATH = DATA_DIR / "wd_cache.json"
# Alias for backward compatibility (old wd_cache.py used CACHE_PATH)
CACHE_PATH = WD_CACHE_PATH

# Wikidata API endpoint for name resolution
_WD_CACHE_API_URL = "https://www.wikidata.org/w/api.php"

# Required User-Agent (Wikidata API returns 403 without one)
_WD_CACHE_USER_AGENT = _MW_USER_AGENT


def _today() -> str:
    """Return today's date as YYYY-MM-DD."""
    return date.today().strftime("%Y-%m-%d")


def _load_cache_file(path: Path, key: str, default: dict) -> dict:
    """Load a cache from disk, parameterized by path and root key.
    
    Args:
        path: Path to the cache file
        key: Root key to validate in the cache (e.g., "names" or "categories")
        default: Default cache structure to return if file is missing/malformed
    
    Returns:
        The cache data, or default if the file is missing or malformed.
    """
    if not path.exists():
        return default
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default
        if key not in data or not isinstance(data.get(key), dict):
            return default
        return data
    except (json.JSONDecodeError, IOError):
        return default


def _save_cache_file(path: Path, cache: dict) -> bool:
    """Save a cache to disk. Returns True on success."""
    try:
        cache["fetched"] = _today()
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except IOError:
        return False


def _load_wd_cache() -> dict:
    """Load the Wikidata cache from disk."""
    return _load_cache_file(
        WD_CACHE_PATH,
        "names",
        {"note": "", "fetched": _today(), "names": {}}
    )


def _save_wd_cache(cache: dict) -> bool:
    """Save the Wikidata cache to disk. Returns True on success."""
    return _save_cache_file(WD_CACHE_PATH, cache)


# Aliases for backward compatibility (old wd_cache.py used load(), _load_cache, _save_cache)
# Note: wd_cache.load() is now wd_cache_load() to avoid conflict with wd_entities.load()
wd_cache_load = _load_wd_cache
_load_cache = _load_wd_cache
_save_cache = _save_wd_cache




def lookup(name: str) -> str | None:
    """Look up a name in the cache ONLY (offline, no network).
    
    Args:
        name: The name to look up
        
    Returns:
        The QID if found in cache with result "ok", None otherwise.
        
    This function performs NO network I/O. It only reads the local cache file.
    """
    cache = _load_wd_cache()
    entry = cache.get("names", {}).get(name)
    
    if entry and entry.get("result") == "ok" and entry.get("qid"):
        return entry["qid"]
    
    return None


def resolve_live(name: str) -> dict:
    """Live Wikidata lookup with an exact-match guard.

    The QID is auto-cached only when the top hit's label coincides with the
    queried name (normalized, case/punctuation-insensitive). When the top
    hits do not coincide, the result is stored as "ambiguous" with the top
    candidates — no QID is auto-accepted on a weak match (never-invention
    rule).

    Returns:
        {"status": "found"|"not_found"|"ambiguous", "qid": str|None, "candidates": [...]} 
    """
    outcome, qid, candidates = _live_resolve(name)
    cache = _load_wd_cache()
    if outcome == "found":
        cache["names"][name] = {"qid": qid, "label_it": name, "label_en": name, "result": "ok", "checked": _today()}
        _save_wd_cache(cache)
        return {"status": "found", "qid": qid, "candidates": []}
    if outcome == "ambiguous":
        cache["names"][name] = {"qid": None, "label_it": name, "label_en": name, "result": "ambiguous", "candidates": candidates, "checked": _today()}
        _save_wd_cache(cache)
        return {"status": "ambiguous", "qid": None, "candidates": candidates}
    cache["names"][name] = {"qid": None, "label_it": name, "label_en": name, "result": "not_found", "checked": _today()}
    _save_wd_cache(cache)
    return {"status": "not_found", "qid": None, "candidates": []}


def resolve(name: str, live: bool = False) -> str | None:
    """Resolve a name to a QID, cache-first.
    
    Args:
        name: The name to resolve
        live: If True and name is not in cache, perform a live Wikidata API lookup.
              If False and name is not in cache, return None without creating an entry.
    
    Returns:
        The QID if found (in cache or via live lookup), None otherwise.
    
    When live=True and the name is not in cache:
      - Calls Wikidata wbsearchentities API
      - Stores the best candidate (or result: "not_found")
      - Returns the QID if found, None if not found
    """
    cache = _load_wd_cache()
    entry = cache.get("names", {}).get(name)
    
    # Cache hit
    if entry:
        if entry.get("result") == "ok" and entry.get("qid"):
            return entry["qid"]
        else:
            return None  # not_found or null qid
    
    # Cache miss
    if not live:
        return None  # Don't create entry, just return None
    
    # Live lookup (three-state; qid only on exact match — see resolve_live)
    outcome = resolve_live(name)
    return outcome.get("qid")


def _normalize_for_match(s: str) -> str:
    """Case/punctuation-insensitive normal form for exact-match guarding."""
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in s.casefold()).split())


def _live_resolve(name: str) -> tuple[str, str | None, list]:
    """Perform a live Wikidata API lookup for a name.
    
    Uses wbsearchentities to search for the name. Returns a three-state
    outcome: ("found", qid, []) when the top hit's label normalizes equal
    to the queried name; ("ambiguous", None, top candidates) when the top
    hits do not coincide — the human must confirm, no QID is auto-accepted;
    ("none", None, []) when the search returns nothing.
    """
    params = urlencode({"action": "wbsearchentities", "search": name, "language": "en", "format": "json"})
    url = f"{_WD_CACHE_API_URL}?{params}"
    
    req = Request(url, headers={"User-Agent": _MW_USER_AGENT})
    
    try:
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        print(f"ERROR: HTTP {e.code} from Wikidata API: {e.reason}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"ERROR: Failed to connect to Wikidata API: {e.reason}", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON response from API: {e}", file=sys.stderr)
        return None
    
    results = data.get("search", [])
    if not results:
        return ("none", None, [])
    
    first = results[0]
    qid = first.get("id")
    label = first.get("label") or ""
    if qid and str(qid).startswith("Q") and _normalize_for_match(label) == _normalize_for_match(name):
        return ("found", qid, [])
    
    candidates = [
        {"qid": r.get("id"), "label": r.get("label") or "", "description": r.get("description") or ""}
        for r in results[:3]
        if r.get("id") and str(r.get("id")).startswith("Q")
    ]
    return ("ambiguous", None, candidates)


def search_wd_cache(term: str) -> list[str]:
    """Search for names containing the term (offline, cache-only).
    
    Args:
        term: The search term (case-insensitive)
        
    Returns:
        List of matching names from the cache.
    """
    cache = _load_wd_cache()
    term_lower = term.lower()
    
    matches = []
    for name in cache.get("names", {}).keys():
        if term_lower in name.lower():
            matches.append(name)
    
    return sorted(matches)


def list_wd_cache_all() -> dict:
    """List all cached names with their status.
    
    Returns:
        Dict mapping name -> {qid, result, checked}
    """
    cache = _load_wd_cache()
    result = {}
    
    for name, entry in cache.get("names", {}).items():
        result[name] = {
            "qid": entry.get("qid"),
            "result": entry.get("result"),
            "checked": entry.get("checked")
        }
    
    return result


def refresh_wd_cache() -> int:
    """Re-verify all existing "ok" entries against Wikidata.
    
    Calls the API for each entry with result "ok" and updates the cache.
    Returns the number of entries refreshed.
    """
    cache = _load_wd_cache()
    refreshed = 0
    
    for name, entry in cache.get("names", {}).items():
        if entry.get("result") != "ok":
            continue
        
        qid = entry.get("qid")
        if not qid:
            continue
        
        # Verify the QID still exists
        url = f"{_WD_CACHE_API_URL}?action=wbgetentities&ids={qid}&language=it|en&format=json"
        req = Request(url, headers={"User-Agent": _MW_USER_AGENT})
        
        try:
            with urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
            
            entities = data.get("entities", {})
            if qid in entities:
                # Entity still exists, update labels
                labels = entities[qid].get("labels", {})
                entry["label_it"] = labels.get("it", {}).get("value", name)
                entry["label_en"] = labels.get("en", {}).get("value", name)
                entry["checked"] = _today()
                refreshed += 1
            else:
                # Entity no longer exists
                entry["result"] = "not_found"
                entry["qid"] = None
                entry["checked"] = _today()
                refreshed += 1
                
        except (HTTPError, URLError, json.JSONDecodeError):
            # Keep existing entry on error
            pass
    
    _save_wd_cache(cache)
    return refreshed


# Aliases for backward compatibility (old wd_cache.py used _load_cache, _save_cache, search, list_all)
_load_cache = _load_wd_cache
_save_cache = _save_wd_cache
search = search_wd_cache
list_all = list_wd_cache_all


# ============================================================================
# COMMONS CATEGORY CACHE (from cat_cache.py)
# ============================================================================

# Path to the category cache file (in the data/ directory)
CAT_CACHE_PATH = DATA_DIR / "cat_cache.json"

# Commons API endpoint
_CAT_CACHE_API_URL = "https://commons.wikimedia.org/w/api.php"

# Required User-Agent (Commons API returns 403 without one)
_CAT_CACHE_USER_AGENT = _MW_USER_AGENT


def _load_cat_cache() -> dict:
    """Load the category cache from disk."""
    return _load_cache_file(
        CAT_CACHE_PATH,
        "categories",
        {"note": "", "source": _CAT_CACHE_API_URL, "fetched": _today(), "categories": {}}
    )


def _save_cat_cache(cache: dict) -> bool:
    """Save the category cache to disk. Returns True on success."""
    return _save_cache_file(CAT_CACHE_PATH, cache)


def lookup_cat(name: str) -> str | None:
    """Look up a category in the cache ONLY (offline, no network).
    
    Args:
        name: The category name (e.g., "Category:X")
        
    Returns:
        "exists" if found in cache with status "exists", None otherwise.
        
    This function performs NO network I/O. It only reads the local cache file.
    """
    cache = _load_cat_cache()
    entry = cache.get("categories", {}).get(name)
    
    if entry and entry.get("status") == "exists":
        return "exists"
    
    return None


def verify_many(names: list[str], live: bool = True) -> dict:
    """Verify multiple categories, cache-first.
    
    Args:
        names: List of category names to verify
        live: If True and a category is not in cache, perform a live Commons API lookup.
              If False and a category is not in cache, return None for that category.
    
    Returns:
        Dict mapping category name -> "exists" | "missing" | None (unknown when live=False)
    
    When live=True and a category is not in cache:
      - Calls Commons API with action=query&titles=...
      - Stores the result (exists or missing)
      - Returns the status
    """
    cache = _load_cat_cache()
    result = {}
    categories = cache.get("categories", {})
    
    # Separate cache hits and misses
    to_verify = []
    for name in names:
        entry = categories.get(name)
        if entry:
            result[name] = entry.get("status")
        else:
            to_verify.append(name)
    
    # Live verification for misses
    if to_verify and live:
        statuses = _live_verify_many(to_verify)
        for name, status in statuses.items():
            categories[name] = {
                "status": status,
                "checked": _today()
            }
            result[name] = status
    
    # Save cache if we made any updates
    if to_verify and live:
        cache["categories"] = categories
        _save_cat_cache(cache)
    
    return result


def _live_verify_many(names: list[str]) -> dict:
    """Perform live Commons API verification for multiple categories.
    
    Uses batch queries (~50 titles per request) with 429 retry logic.
    
    Returns:
        Dict mapping category name -> "exists" | "missing"
    """
    result = {}
    batch_size = 50
    
    for i in range(0, len(names), batch_size):
        batch = names[i:i + batch_size]
        titles_param = "|".join(batch)
        
        url = f"{_CAT_CACHE_API_URL}?" + urlencode({"action": "query", "titles": titles_param, "format": "json"})
        
        req = Request(url, headers={"User-Agent": _MW_USER_AGENT})
        
        max_retries = 3
        retry_delay = 1.0
        
        for attempt in range(max_retries):
            try:
                with urlopen(req, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as e:
                if e.code == 429 and attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                    continue
                print(f"ERROR: HTTP {e.code} from Commons API: {e.reason}", file=sys.stderr)
                # Mark all in batch as missing on error
                for name in batch:
                    result[name] = "missing"
                return result
            except URLError as e:
                print(f"ERROR: Failed to connect to Commons API: {e.reason}", file=sys.stderr)
                for name in batch:
                    result[name] = "missing"
                return result
        
        # Parse results
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            title = page_data.get("title", "")
            if title:
                if "missing" in page_data:
                    result[title] = "missing"
                else:
                    result[title] = "exists"
    
    return result


def search_cat(term: str) -> list[str]:
    """Search for categories containing the term (live, Commons API).
    
    Args:
        term: The search term
        
    Returns:
        List of matching category names from Commons search in namespace 14.
    """
    url = f"{_CAT_CACHE_API_URL}?" + urlencode({"action": "query", "list": "search", "srnamespace": "14", "srsearch": term, "format": "json"})
    
    req = Request(url, headers={"User-Agent": _MW_USER_AGENT})
    
    try:
        with urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        print(f"ERROR: HTTP {e.code} from Commons API: {e.reason}", file=sys.stderr)
        return []
    except URLError as e:
        print(f"ERROR: Failed to connect to Commons API: {e.reason}", file=sys.stderr)
        return []
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON response from API: {e}", file=sys.stderr)
        return []
    
    # Extract category titles from search results
    results = data.get("query", {}).get("search", [])
    titles = []
    for item in results:
        title = item.get("title", "")
        if title:
            titles.append(title)
    
    return sorted(titles)


def list_cat_cache_all() -> dict:
    """List all cached categories with their status.
    
    Returns:
        Dict mapping category name -> {status, checked}
    """
    cache = _load_cat_cache()
    result = {}
    
    for name, entry in cache.get("categories", {}).items():
        result[name] = {
            "status": entry.get("status"),
            "checked": entry.get("checked")
        }
    
    return result


def extract_categories_from_verdicts(verdicts_path: Path) -> list[str]:
    """Extract all category names from a verdicts.json file.
    
    Args:
        verdicts_path: Path to verdicts.json
        
    Returns:
        List of unique category names found in the file.
    """
    if not verdicts_path.exists():
        return []
    
    try:
        data = json.loads(verdicts_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        return []
    
    categories = set()
    for item in data.get("items", []):
        cats = item.get("commons_categories", [])
        if isinstance(cats, list):
            for cat in cats:
                if isinstance(cat, str):
                    categories.add(cat)
    
    return sorted(categories)
