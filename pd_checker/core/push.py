"""Wikimedia Commons upload module.

Provides the `push` subcommand for uploading ready items to Wikimedia Commons.

Usage:
    python3 -m pd_checker push <folder> [--live] [--filename-prefix PREFIX]

Security:
    - Credentials ONLY from environment variables (never argv, never files)
    - PD_WM_OAUTH_TOKEN: OAuth 2 bearer token
    - PD_WM_BOT_USER + PD_WM_BOT_PASSWORD: bot-password login flow
    - PD_WM_CONTACT: contact info for User-Agent (required by Wikimedia API)
    - Never log or print credentials
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from http.cookiejar import CookieJar
from typing import Any


# =============================================================================
# Credential handling (env vars ONLY)
# =============================================================================

def get_credentials() -> dict[str, str | None]:
    """Load credentials from environment variables only.
    
    Returns:
        Dict with keys: 'oauth_token', 'bot_user', 'bot_password', 'contact'
        Values are None if not set.
    """
    return {
        "oauth_token": os.environ.get("PD_WM_OAUTH_TOKEN"),
        "bot_user": os.environ.get("PD_WM_BOT_USER"),
        "bot_password": os.environ.get("PD_WM_BOT_PASSWORD"),
        "contact": os.environ.get("PD_WM_CONTACT", "<pd-checker> (no contact set)"),
    }


def validate_credentials(credentials: dict[str, str | None], live: bool) -> tuple[str | None, str]:
    """Validate credentials for live upload.
    
    Args:
        credentials: Dict from get_credentials()
        live: Whether this is a live upload
        
    Returns:
        Tuple of (auth_method, error_message)
        auth_method is 'oauth' or 'bot-password' or None
        error_message is empty string on success
    """
    if not live:
        return None, ""
    
    # Check for credentials
    has_oauth = credentials["oauth_token"] is not None
    has_bot = credentials["bot_user"] is not None and credentials["bot_password"] is not None
    
    if not has_oauth and not has_bot:
        return None, (
            "Nessuna credenziale trovata. Imposta una delle seguenti variabili d'ambiente:\n"
            "  PD_WM_OAUTH_TOKEN=<token> (OAuth 2 bearer token, preferibile)\n"
            "  oppure\n"
            "  PD_WM_BOT_USER=<username> e PD_WM_BOT_PASSWORD=<password>"
        )
    
    # Prefer OAuth
    if has_oauth:
        return "oauth", ""
    
    return "bot-password", ""


# =============================================================================
# Wikitext assembler
# =============================================================================

def build_wikitext(item: dict, other_versions: str | None = None) -> str:
    """Build Commons description page wikitext from an item package.
    
    Args:
        item: Item dict with 'information', 'commons_templates', 'commons_categories'
        other_versions: value for the |other_versions= field (the data link to the
            retro side); omitted when the item has no retro side
    
    Returns:
        Wikitext string for the file description page
    """
    lines = []
    
    # {{Information}} template
    info = item.get("information", {})
    description = info.get("description", "")
    date = info.get("date", "")
    source = info.get("source", "{{Self-scanned}}")
    author = info.get("author", "")
    author_role = item.get("extracted_facts", {}).get("author_role", "")
    publisher = item.get("extracted_facts", {}).get("publisher", "")
    
    # Handle author: if it's a printing works or role is printer/publisher, use {{unknown|author}}
    author_from_info = info.get("author", "")
    if author_role in ("printer", "publisher") or any(
        marker in (author_from_info or "").lower()
        for marker in ["stab.", "tipografia", "arti grafiche", "stamperia", "editore"]
    ):
        author_display = "{{unknown|author}}"
        # Put imprint in source or description
        if publisher:
            source = f"{source} (imprint: {publisher})"
        elif author_from_info:
            source = f"{source} (imprint: {author_from_info})"
    else:
        author_display = author_from_info if author_from_info else "{{unknown|author}}"
    
    # Build Information template
    lines.append("{{Information")
    lines.append(f" |description={description}")
    lines.append(f" |date={date if date else 'n/d'}")
    lines.append(f" |source={source}")
    lines.append(f" |author={author_display}")
    lines.append(" |permission=")
    if other_versions:
        lines.append(f" |other_versions={other_versions}")
    lines.append("}}")
    lines.append("")
    
    # License templates
    license_templates = item.get("commons_templates", [])
    for template in license_templates:
        lines.append(template)
    if license_templates:
        lines.append("")
    
    # Source template (always include)
    source_template = item.get("source_template", "{{Self-scanned}}")
    lines.append(source_template)
    lines.append("")
    
    # Caveat templates
    caveat_templates = item.get("caveat_templates", [])
    for template in caveat_templates:
        lines.append(template)
    if caveat_templates:
        lines.append("")
    
    # Categories
    categories = item.get("commons_categories", [])
    for cat in categories:
        lines.append(f"[[{cat}]]")
    
    return "\n".join(lines)


# =============================================================================
# Filename derivation
# =============================================================================

def derive_filename(item: dict, prefix: str, item_number: int) -> str:
    """Derive Commons filename from item.
    
    Args:
        item: Item dict (used for validation)
        prefix: Filename prefix from --filename-prefix
        item_number: Item number (1-based)
        
    Returns:
        Filename string like "<prefix> - <n>.png"
    """
    # Get image extension from first image
    images = (item.get("extracted_facts") or {}).get("images", [])
    ext = ".png"  # default
    if images:
        first_image = images[0]
        ext = Path(first_image).suffix.lower()
        if not ext:
            ext = ".png"
    
    return f"{prefix} - {item_number}{ext}"


def _resolve_image_path(folder: Path, name: str) -> Path | None:
    """Resolve an image reference to an existing path.

    Image names arrive in different encodings depending on the pipeline
    version: absolute paths, folder-prefixed ('Cardinale-Del-Val/1_fronte.png')
    or bare filenames ('1_fronte.png'). All three must resolve.
    """
    p = Path(name)
    if p.is_absolute():
        return p if p.exists() else None
    if (folder / p).exists():
        return folder / p
    if (folder / p.name).exists():
        return folder / p.name
    if p.exists():
        return p
    return None


# =============================================================================
# Upload flow
# =============================================================================

def create_opener(cookiejar: CookieJar | None = None) -> urllib.request.OpenerDirector:
    """Create urllib opener with optional cookie jar.
    
    Args:
        cookiejar: CookieJar for session persistence, or None
        
    Returns:
        Configured OpenerDirector
    """
    opener = urllib.request.build_opener()
    if cookiejar:
        opener.add_handler(urllib.request.HTTPCookieProcessor(cookiejar))
    return opener


def login_bot_password(opener: urllib.request.OpenerDirector, credentials: dict[str, str | None]) -> tuple[bool, str]:
    """Login using bot-password flow.
    
    Args:
        opener: Opener with cookie jar
        credentials: Dict with 'bot_user', 'bot_password'
        
    Returns:
        Tuple of (success, message)
    """
    base_url = "https://commons.wikimedia.org/w/api.php"
    
    # Step 1: Get login token
    params = {
        "action": "query",
        "meta": "tokens",
        "type": "login",
        "format": "json",
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": credentials["contact"]})
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        return False, f"Errore rete ottenendo login token: {e}"
    
    login_token = data.get("query", {}).get("tokens", {}).get("logintoken")
    if not login_token:
        return False, "Login token non ricevuto dall'API"
    
    # Step 2: Login
    params = {
        "action": "login",
        "lgname": credentials["bot_user"],
        "lgpassword": credentials["bot_password"],
        "lgtoken": login_token,
        "format": "json",
    }
    
    try:
        req = urllib.request.Request(
            base_url,
            data=urllib.parse.urlencode(params).encode("utf-8"),
            method="POST",
            headers={"User-Agent": credentials["contact"]}
        )
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        return False, f"Errore durante il login: {e}"
    
    login_result = data.get("login", {}).get("result")
    if login_result != "Success":
        return False, f"Login fallito: {data.get('login', {})}"
    
    return True, "Login riuscito"


def get_csrf_token(opener: urllib.request.OpenerDirector, credentials: dict[str, str | None]) -> tuple[str | None, str]:
    """Get CSRF token for upload.
    
    Args:
        opener: Opener with cookie jar (must be logged in)
        credentials: Dict with 'contact'
        
    Returns:
        Tuple of (token, error_message)
    """
    base_url = "https://commons.wikimedia.org/w/api.php"
    
    params = {
        "action": "query",
        "meta": "tokens",
        "type": "csrf",
        "format": "json",
    }
    url = f"{base_url}?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url, headers={"User-Agent": credentials["contact"]})
        with opener.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        return None, f"Errore rete ottenendo CSRF token: {e}"
    
    token = data.get("query", {}).get("tokens", {}).get("csrftoken")
    if not token:
        return None, "CSRF token non ricevuto dall'API"
    
    return token, ""


def upload_file(
    opener: urllib.request.OpenerDirector,
    filepath: Path,
    filename: str,
    wikitext: str,
    comment: str,
    token: str,
    credentials: dict[str, str | None]
) -> tuple[bool, dict]:
    """Upload a single file to Wikimedia Commons.
    
    Args:
        opener: Opener with cookie jar
        filepath: Path to image file
        filename: Commons filename
        wikitext: File description wikitext
        comment: Upload comment
        token: CSRF token
        credentials: Dict with 'contact'
        
    Returns:
        Tuple of (success, response_dict)
        On warning (exists/duplicate), success=False but response contains warning info
    """
    base_url = "https://commons.wikimedia.org/w/api.php"
    
    # Read file bytes
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
    except (IOError, OSError) as e:
        return False, {"error": f"Cannot read file: {e}"}
    
    # Build multipart form data manually
    boundary = "----WebKitFormBoundary" + str(int(time.time() * 1000))
    body = b""
    
    def add_field(name: str, value: str):
        nonlocal body
        body += f"--{boundary}\r\n".encode("utf-8")
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        body += f"{value}\r\n".encode("utf-8")
    
    def add_file_field(name: str, filename: str, data: bytes):
        nonlocal body
        body += f"--{boundary}\r\n".encode("utf-8")
        body += f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode("utf-8")
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += data + b"\r\n"
    
    add_field("action", "upload")
    add_field("format", "json")
    add_field("filename", filename)
    add_field("filesize", str(len(file_bytes)))
    add_field("text", wikitext)
    add_field("comment", comment)
    add_field("token", token)
    add_field("ignorewarnings", "false")  # NEVER ignore warnings
    add_file_field("file", filename, file_bytes)
    body += f"--{boundary}--\r\n".encode("utf-8")
    
    try:
        req = urllib.request.Request(
            base_url,
            data=body,
            method="POST",
            headers={
                "User-Agent": credentials["contact"],
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            }
        )
        with opener.open(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        return False, {"error": f"Upload failed: {e}"}
    
    upload_data = data.get("upload", {})
    
    # Check for warnings (exists, duplicate, etc.)
    warnings = upload_data.get("warnings", {})
    if warnings:
        # Has warnings - do NOT retry, do NOT overwrite
        return False, {"upload": upload_data, "warnings": warnings}
    
    # Check result
    result = upload_data.get("result")
    if result == "Success":
        return True, {"upload": upload_data}
    else:
        return False, {"upload": upload_data}


def handle_rate_limit(response: dict) -> bool:
    """Check if response indicates rate limiting.
    
    Args:
        response: API response dict
        
    Returns:
        True if rate limited, False otherwise
    """
    if "error" in response:
        errors = response.get("error", {})
        if isinstance(errors, list):
            for err in errors:
                if err.get("code") == "ratelimited":
                    return True
        elif isinstance(errors, dict) and errors.get("code") == "ratelimited":
            return True
    return False


def get_retry_after(response: dict) -> int:
    """Get retry-after seconds from rate limit response.
    
    Args:
        response: API response dict
        
    Returns:
        Seconds to wait (default 30)
    """
    if "error" in response:
        errors = response.get("error", {})
        if isinstance(errors, dict):
            retry_after = errors.get("retry-after")
            if retry_after:
                try:
                    return int(retry_after)
                except (ValueError, TypeError):
                    pass
    return 30  # default


# =============================================================================
# Main push command
# =============================================================================

def cmd_push(argv: list[str], live: bool = False) -> int:
    """Push ready items to Wikimedia Commons.
    
    Args:
        argv: Command line arguments [folder, --filename-prefix PREFIX]
        live: If True, perform actual upload; if False, dry-run
        
    Returns:
        Exit code (0=success, 1=failure)
    """
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Upload ready items to Wikimedia Commons."
    )
    parser.add_argument("folder", help="Material folder containing pubdom.json")
    parser.add_argument(
        "--filename-prefix",
        metavar="PREFIX",
        type=str,
        help="Prefix for Commons filenames (REQUIRED for live upload)"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        default=live,
        help="Perform actual upload (default: dry-run)"
    )
    args = parser.parse_args(argv)
    
    folder = Path(args.folder)
    pubdom_path = folder / "pubdom.json"
    
    if not pubdom_path.exists():
        print(f"[push] ERROR: pubdom.json not found in {folder}", file=sys.stderr)
        return 1
    
    # Load pubdom
    try:
        pubdom = json.loads(pubdom_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[push] ERROR: Cannot read {pubdom_path}: {e}", file=sys.stderr)
        return 1
    
    items = pubdom.get("items", [])
    if not items:
        print("[push] ERROR: No items found in pubdom.json", file=sys.stderr)
        return 1
    
    # Filter ready items
    ready_items = [
        (i, item) for i, item in enumerate(items, start=1)
        if item.get("upload", {}).get("flag") == "ready"
    ]
    
    if not ready_items:
        print(f"[push] 0 pronti su {len(items)}; usa --upload-policy per cambiare la politica.")
        return 1
    
    # Validate filename prefix
    prefix = args.filename_prefix
    if live and not prefix:
        print(
            "[push] ERROR: --filename-prefix REQUIRED per upload LIVE.",
            file=sys.stderr
        )
        print(
            "[push] I file su Commons devono avere nomi significativi.",
            file=sys.stderr
        )
        return 1
    
    if not prefix:
        # Dry-run default: use folder name
        prefix = folder.name
    
    # Handle live mode
    credentials = None
    opener = None
    csrf_token = None
    
    if live:
        # Get and validate credentials
        credentials = get_credentials()
        auth_method, error_msg = validate_credentials(credentials, live=True)
        
        if not auth_method:
            print(f"[push] ERROR: {error_msg}", file=sys.stderr)
            return 1
        
        # Prefer OAuth
        if auth_method == "oauth":
            print(f"[push] Using OAuth 2 bearer token", file=sys.stderr)
        else:
            print(f"[push] Using bot-password login", file=sys.stderr)
        
        # Create opener with cookie jar
        cookiejar = CookieJar()
        opener = create_opener(cookiejar)
        
        # Set User-Agent header for all requests
        if auth_method == "oauth":
            # Add Authorization header
            opener.addheaders = [
                ("User-Agent", credentials["contact"]),
                ("Authorization", f"Bearer {credentials['oauth_token']}"),
            ]
        else:
            # Login first
            success, msg = login_bot_password(opener, credentials)
            if not success:
                print(f"[push] ERROR: {msg}", file=sys.stderr)
                return 1
            print(f"[push] {msg}", file=sys.stderr)
            
            # Update opener headers after login
            opener.addheaders = [("User-Agent", credentials["contact"])]
        
        # Get CSRF token
        csrf_token, error_msg = get_csrf_token(opener, credentials)
        if not csrf_token:
            print(f"[push] ERROR: {error_msg}", file=sys.stderr)
            return 1
        print(f"[push] CSRF token obtained", file=sys.stderr)
    
    # Process items - upload ALL images per item (front + back + any further sides)
    print(f"\n== Push: {folder.name} ==")
    print(f"Items pronti: {len(ready_items)}")
    print(f"Modalità: {'LIVE' if live else 'DRY-RUN'}")
    print(f"Prefix: {prefix}")
    print()
    
    results = []
    
    # For live mode: pre-check existing files in batches
    if live:
        # Collect all planned filenames for pre-check
        planned_titles = []
        for item_num, item in ready_items:
            images = item.get("images") or []
            if not images:
                images = [item.get("file", "")] if item.get("file") else []
            
            for side_idx, img_path_str in enumerate(images):
                # Derive filename for this side
                if side_idx == 0:
                    filename = derive_filename(item, prefix, item_num)
                else:
                    # Retro side: derive base name then add retro suffix
                    base_filename = derive_filename(item, prefix, item_num)
                    # Replace extension to add retro suffix
                    stem = Path(base_filename).stem
                    ext = Path(base_filename).suffix
                    if side_idx == 1:
                        filename = f"{stem} (retro){ext}"
                    else:
                        k = side_idx  # 2, 3, ...
                        filename = f"{stem} (retro {k}){ext}"
                
                planned_titles.append(filename)
        
        # Pre-check all planned titles in ONE API query
        if planned_titles:
            existing_titles = _check_existing_files(opener, planned_titles, credentials)
            print(f"[PRE-CHECK] {len(existing_titles)} file già esistenti su Commons")
    
    for item_num, item in ready_items:
        file_path = item.get("file", "")
        facts = item.get("extracted_facts") or {}
        image_names = facts.get("images") or []
        
        # Fallback: no images in the facts — probe the file field (and its retro twin)
        if not image_names:
            if file_path:
                probe = folder / Path(file_path).name
                if probe.exists():
                    image_names = [str(probe.absolute())]
                    stem = Path(file_path).stem
                    suffix = Path(file_path).suffix
                    retro_probe = folder / f"{stem.replace('_fronte', '_retro')}{suffix}"
                    if retro_probe.exists():
                        image_names.append(str(retro_probe.absolute()))
            if not image_names:
                print(f"[SKIP] {file_path}: no images")
                results.append({"item": item_num, "status": "skip", "reason": "no images"})
                continue
        
        # Resolve every image to an existing path before planning anything
        sides = []
        for side_idx, name in enumerate(image_names):
            img_path = _resolve_image_path(folder, name)
            if img_path is None:
                print(f"[SKIP] {file_path} (side {side_idx + 1}): image not found ({name})")
            else:
                sides.append((side_idx, img_path))
        if not sides:
            results.append({"item": item_num, "status": "skip", "reason": "image not found"})
            continue
        
        # Plan side filenames first: the wikitext of each side must link to the others
        front_filename = derive_filename(item, prefix, item_num)
        front_stem = Path(front_filename).stem
        ext = Path(front_filename).suffix
        planned = []
        for side_idx, img_path in sides:
            if side_idx == 0:
                planned.append((side_idx, img_path, front_filename))
            elif side_idx == 1:
                planned.append((side_idx, img_path, f"{front_stem} (retro){ext}"))
            else:
                planned.append((side_idx, img_path, f"{front_stem} (retro {side_idx}){ext}"))
        
        # Bidirectional data link (|other_versions=): front<->retro
        front_other = None
        if len(planned) > 1:
            front_other = f"Retro: [[:File:{planned[1][2]}|{planned[1][2]}]]"
        main_wikitext = build_wikitext(item, other_versions=front_other)
        
        # Build wikitext for the main/retro files (with the data link to the front)
        for side_idx, img_path, filename in planned:
            if side_idx == 0:
                wikitext = main_wikitext
            else:
                wikitext = build_retro_wikitext(
                    item, front_filename,
                    other_versions=f"Fronte: [[:File:{front_filename}|{front_filename}]]",
                )
            
            # Upload comment
            comment = f"Upload da pd-checker (item {item_num}, side {side_idx + 1})"
            
            # Check if file already exists (live mode only)
            if live and planned_titles:
                if filename in existing_titles:
                    print(f"[SKIP] {filename}: già presente su Commons — non toccato")
                    results.append({"item": item_num, "status": "exists", "filename": filename})
                    continue
            
            if live:
                # Perform upload
                print(f"[UPLOAD] {filename}...")
                success, response = upload_file(
                    opener, img_path, filename, wikitext, comment, csrf_token, credentials
                )
                
                if success:
                    print(f"  OK: {filename}")
                    results.append({"item": item_num, "status": "success", "filename": filename, "side": side_idx + 1})
                else:
                    # Check for warnings (exists/duplicate)
                    warnings = response.get("warnings", {})
                    if warnings:
                        # Extract warning type
                        warning_types = list(warnings.keys())
                        print(f"  WARNING: {', '.join(warning_types)} - NOT overwritten")
                        results.append({
                            "item": item_num,
                            "status": "warning",
                            "filename": filename,
                            "warnings": warning_types,
                            "side": side_idx + 1
                        })
                    else:
                        error = response.get("error", "unknown error")
                        print(f"  FAILED: {error}")
                        results.append({"item": item_num, "status": "failed", "error": str(error), "side": side_idx + 1})
                
                # Small sleep between uploads
                time.sleep(0.5)
                
                # Check for rate limiting
                if handle_rate_limit(response):
                    retry_after = get_retry_after(response)
                    print(f"[RATE LIMIT] Waiting {retry_after}s...")
                    time.sleep(retry_after)
            else:
                # Dry-run: print plan
                print(f"[DRY-RUN] {filename}")
                print(f"  File: {img_path}")
                print(f"  Wikitext:")
                for line in wikitext.split("\n")[:10]:
                    print(f"    {line}")
                if len(wikitext.split("\n")) > 10:
                    print(f"    ... ({len(wikitext.split(chr(10))) - 10} more lines)")
                print(f"  Comment: {comment}")
                print()
                results.append({"item": item_num, "status": "dry-run", "filename": filename, "side": side_idx + 1})
    
    # Summary
    print("\n== Summary ==")
    success_count = sum(1 for r in results if r["status"] == "success")
    warning_count = sum(1 for r in results if r["status"] == "warning")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    skip_count = sum(1 for r in results if r["status"] == "skip")
    exists_count = sum(1 for r in results if r["status"] == "exists")
    dry_run_count = sum(1 for r in results if r["status"] == "dry-run")
    
    if live:
        print(f"Success:   {success_count}")
        print(f"Warnings:  {warning_count} (NOT overwritten)")
        print(f"Exists:    {exists_count} (pre-checked, skipped)")
        print(f"Failed:    {failed_count}")
        print(f"Skipped:   {skip_count}")
    else:
        print(f"Dry-run:   {dry_run_count} files planned")
    
    # Return non-zero if any failures in live mode
    if live and (failed_count > 0 or warning_count > 0):
        return 1
    return 0


def _check_existing_files(opener: urllib.request.OpenerDirector, titles: list[str], credentials: dict[str, str | None]) -> set[str]:
    """Check which files already exist on Commons (batch query).
    
    Args:
        opener: Opener with cookie jar
        titles: List of filenames to check (without 'File:' prefix)
        credentials: Dict with 'contact'
        
    Returns:
        Set of titles that already exist
    """
    if not titles:
        return set()
    
    base_url = "https://commons.wikimedia.org/w/api.php"
    
    # Batch all titles in ONE query
    # MediaWiki API has a limit on batch size; chunk if needed
    BATCH_SIZE = 50
    existing = set()
    
    for i in range(0, len(titles), BATCH_SIZE):
        batch = titles[i:i + BATCH_SIZE]
        titles_param = "|".join(f"File:{t}" for t in batch)
        
        params = {
            "action": "query",
            "prop": "imageinfo",
            "iiprop": "sha1",
            "titles": titles_param,
            "format": "json",
        }
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        
        try:
            req = urllib.request.Request(url, headers={"User-Agent": credentials["contact"]})
            with opener.open(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
            print(f"[PRE-CHECK WARNING] Query failed: {e}")
            continue
        
        # Extract existing pages
        pages = data.get("query", {}).get("pages", {})
        for page_id, page_data in pages.items():
            if page_id != "-1":  # -1 means page doesn't exist
                title = page_data.get("title", "")
                if title.startswith("File:"):
                    existing.add(title[5:])  # Remove "File:" prefix
    
    return existing


def build_retro_wikitext(item: dict, main_filename: str, other_versions: str | None = None) -> str:
    """Build wikitext for a retro (back/further side) file.
    
    Args:
        item: Item dict with 'information', 'commons_templates', 'commons_categories'
        main_filename: The main file's filename (for the backlink)
        other_versions: value for the |other_versions= field (the data link to
            the front side)
        
    Returns:
        Wikitext string for the retro file description page
    """
    lines = []
    
    # {{Information}} template with retro description
    info = item.get("information", {})
    date = info.get("date", "")
    source = info.get("source", "{{Self-scanned}}")
    author = info.get("author", "")
    author_role = item.get("extracted_facts", {}).get("author_role", "")
    publisher = item.get("extracted_facts", {}).get("publisher", "")
    
    # Handle author: same logic as build_wikitext
    author_from_info = info.get("author", "")
    if author_role in ("printer", "publisher") or any(
        marker in (author_from_info or "").lower()
        for marker in ["stab.", "tipografia", "arti grafiche", "stamperia", "editore"]
    ):
        author_display = "{{unknown|author}}"
        # Put imprint in source or description
        if publisher:
            source = f"{source} (imprint: {publisher})"
        elif author_from_info:
            source = f"{source} (imprint: {author_from_info})"
    else:
        author_display = author_from_info if author_from_info else "{{unknown|author}}"
    
    # Build retro description
    retro_description = f"Retro della cartolina [[:File:{main_filename}|{main_filename}]]"
    
    # Build Information template
    lines.append("{{Information")
    lines.append(f" |description={retro_description}")
    lines.append(f" |date={date if date else 'n/d'}")
    lines.append(f" |source={source}")
    lines.append(f" |author={author_display}")
    lines.append(" |permission=")
    if other_versions:
        lines.append(f" |other_versions={other_versions}")
    lines.append("}}")
    lines.append("")
    
    # License templates (same as main file)
    license_templates = item.get("commons_templates", [])
    for template in license_templates:
        lines.append(template)
    if license_templates:
        lines.append("")
    
    # Source template (same as main file)
    source_template = item.get("source_template", "{{Self-scanned}}")
    lines.append(source_template)
    lines.append("")
    
    # Caveat templates (same as main file)
    caveat_templates = item.get("caveat_templates", [])
    for template in caveat_templates:
        lines.append(template)
    if caveat_templates:
        lines.append("")
    
    # Categories (same as main file)
    categories = item.get("commons_categories", [])
    for cat in categories:
        lines.append(f"[[{cat}]]")
    
    return "\n".join(lines)
