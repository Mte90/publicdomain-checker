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

def build_wikitext(item: dict) -> str:
    """Build Commons description page wikitext from item package.
    
    Args:
        item: Item dict with 'information', 'commons_templates', 'commons_categories'
        
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
    images = item.get("images", [])
    ext = ".png"  # default
    if images:
        first_image = images[0]
        ext = Path(first_image).suffix.lower()
        if not ext:
            ext = ".png"
    
    return f"{prefix} - {item_number}{ext}"


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
    
    # Process items
    print(f"\n== Push: {folder.name} ==")
    print(f"Items pronti: {len(ready_items)}")
    print(f"Modalità: {'LIVE' if live else 'DRY-RUN'}")
    print(f"Prefix: {prefix}")
    print()
    
    results = []
    
    for item_num, item in ready_items:
        file_path = item.get("file", "")
        images = item.get("images") or []  # Handle None
        
        # Use first image for upload, or fall back to file field
        if not images:
            # Try to use the file field as the image path
            if file_path:
                # The file path is relative to project root, e.g. "Cardinale-Del-Val/1_fronte.png"
                # folder is the material folder, e.g. Path("Cardinale-Del-Val")
                # So we extract just the filename and check relative to folder
                img_path = folder / Path(file_path).name
                if img_path.exists():
                    # Use the absolute path for the upload
                    images = [str(img_path.absolute())]
                else:
                    print(f"[SKIP] {file_path}: image not found ({img_path})")
                    results.append({"item": item_num, "status": "skip", "reason": "no images"})
                    continue
            else:
                print(f"[SKIP] {file_path}: no images")
                results.append({"item": item_num, "status": "skip", "reason": "no images"})
                continue
        
        img_path = folder / images[0] if not images[0].startswith("/") else Path(images[0])
        if not img_path.exists():
            print(f"[SKIP] {file_path}: image not found ({images[0]})")
            results.append({"item": item_num, "status": "skip", "reason": f"image not found"})
            continue
        
        # Derive filename
        filename = derive_filename(item, prefix, item_num)
        
        # Build wikitext
        wikitext = build_wikitext(item)
        
        # Upload comment
        comment = f"Upload da pd-checker (item {item_num})"
        
        if live:
            # Perform upload
            print(f"[UPLOAD] {filename}...")
            success, response = upload_file(
                opener, img_path, filename, wikitext, comment, csrf_token, credentials
            )
            
            if success:
                print(f"  OK: {filename}")
                results.append({"item": item_num, "status": "success", "filename": filename})
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
                        "warnings": warning_types
                    })
                else:
                    error = response.get("error", "unknown error")
                    print(f"  FAILED: {error}")
                    results.append({"item": item_num, "status": "failed", "error": str(error)})
            
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
            results.append({"item": item_num, "status": "dry-run", "filename": filename})
    
    # Summary
    print("\n== Summary ==")
    success_count = sum(1 for r in results if r["status"] == "success")
    warning_count = sum(1 for r in results if r["status"] == "warning")
    failed_count = sum(1 for r in results if r["status"] == "failed")
    skip_count = sum(1 for r in results if r["status"] == "skip")
    dry_run_count = sum(1 for r in results if r["status"] == "dry-run")
    
    if live:
        print(f"Success:   {success_count}")
        print(f"Warnings:  {warning_count} (NOT overwritten)")
        print(f"Failed:    {failed_count}")
        print(f"Skipped:   {skip_count}")
    else:
        print(f"Dry-run:   {dry_run_count} items planned")
    
    # Return non-zero if any failures in live mode
    if live and (failed_count > 0 or warning_count > 0):
        return 1
    return 0
