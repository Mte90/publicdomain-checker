#!/usr/bin/env python3
"""CLI to generate a static HTML review dashboard from verdicts JSON."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from . import context
from .push import derive_filename


def _escape(s: str | None) -> str:
    """HTML-escape a string, handling None safely."""
    return html.escape(str(s or ""))


def _translate_confidence(val: str | None) -> str:
    """Translate confidence enum to Italian."""
    return {"high": "alta", "medium": "media", "low": "bassa"}.get(str(val or ""), _escape(str(val or "")))


def _translate_document_type(val: str | None) -> str:
    """Translate document_type enum to Italian."""
    return {
        "photo": "fotografia",
        "postcard": "cartolina",
        "letter": "lettera",
        "newspaper": "quotidiano",
        "magazine": "rivista",
        "manuscript": "manoscritto",
        "unknown": "sconosciuto",
    }.get(str(val or ""), _escape(str(val or "")))


def _translate_photo_classification(val: str | None) -> str:
    """Translate photo_classification enum to Italian."""
    return {"simple": "semplice", "artistic": "artistica", "unclear": "incerta"}.get(str(val or ""), _escape(str(val or "")))


def _translate_bool(val: bool | None) -> str:
    """Translate boolean to Italian yes/no."""
    if val is None:
        return ""
    return "sì" if val else "no"


def _translate_context_key(key: str) -> str:
    """Translate context JSON keys to Italian labels."""
    # Mapping of known keys to Italian labels
    translations = {
        "folder": "Cartella",
        "collection": "Collezione",
        "provenance": "Provenienza",
        "rights_holder": "Titolare dei diritti",
        "country_of_origin": "Paese di origine",
        "period": "Periodo",
        "notes": "Note",
        "depicts": "Raffigura",
        "categories": "Categorie",
        "known_authors": "Autori noti",
    }
    # Return translated label or format the key: Title-case, replace underscores with spaces
    return translations.get(key, key.replace("_", " ").title())


def _status_color(status: str) -> str:
    """Return CSS color class for status."""
    return {
        "public_domain": "green",
        "not_public_domain": "red",
        "uncertain": "amber",
    }.get(status, "gray")


def _format_pipeline_details(v: dict, ctx: any, run_manifest: any, images_base: str | None, upload_policy: str | None = None) -> str:
    """Format pipeline details with real provenance data."""
    import os
    from datetime import datetime
    
    # Build definition list HTML
    items = []
    
    # Rule table version from verdict
    rule_table_version = v.get("rule_table_version")
    if rule_table_version:
        items.append(("Versione tabelle regole", str(rule_table_version)))
    
    # Current year from verdict
    current_year = v.get("current_year")
    if current_year:
        items.append(("Anno corrente", str(current_year)))
    
    # Generation date: use modification time of first image, or current year if unavailable
    generation_date = None
    images = v.get("extracted_facts", {}).get("images", [])
    if images and images_base:
        first_image = images[0]
        image_path = os.path.join(images_base, first_image)
        try:
            mtime = os.path.getmtime(image_path)
            generation_date = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            # File missing or cannot read mtime - use current year
            generation_date = str(current_year) if current_year else str(datetime.now().year)
    else:
        # No images available - use current year
        generation_date = str(current_year) if current_year else str(datetime.now().year)
    items.append(("Data generazione", generation_date))
    
    # Upload policy from verdict or argument, translated to Italian with explanation
    upload_policy_val = upload_policy or v.get("upload_policy")
    if upload_policy_val:
        policy_label = {"strict": "severo", "pd-only": "solo copyright", "all": "tutto"}.get(upload_policy_val, upload_policy_val)
        # Add explanation: active policy meaning + alternative
        if upload_policy_val == "strict":
            policy_explanation = f"{policy_label} — un item è 'Pronto' solo senza vincoli diritti-immagine/privacy e senza dati mancanti (l'alternativa 'solo copyright' guarda solo il copyright)."
        elif upload_policy_val == "pd-only":
            policy_explanation = f"{policy_label} — un item è 'Pronto' quando è in pubblico dominio con confidenza alta, anche con altri vincoli (l'alternativa 'severo' richiede anche assenza di blocchi non-copyright)."
        elif upload_policy_val == "all":
            policy_explanation = f"{policy_label} — l'utente sceglie di procedere comunque: tutti gli item sono marcato 'Pronti' (anche incerti), ma le ragioni e i template restano onesti sui blocchi/limiti."
        else:
            policy_explanation = policy_label
        items.append(("Politica di upload", policy_explanation))
    
    # Model roles from run manifest
    if run_manifest:
        models = run_manifest.get("models", [])
        if models:
            roles = []
            for m in models:
                role = m.get("role", m.get("name", "N/A"))
                name = m.get("name", m.get("model", "N/A"))
                if role != name:
                    roles.append(f"{role}: {name}")
                else:
                    roles.append(name)
            items.append(("Ruoli modelli", ", ".join(roles)))
        else:
            # Try alternative structure
            roles = run_manifest.get("model_registry", "")
            if roles:
                items.append(("Registro modelli", roles))
    
    # Per-file pipeline trail if available
    trail = v.get("pipeline_trail", {})
    if trail:
        items.append(("Traccia per file", json.dumps(trail, indent=2, ensure_ascii=False)))
    
    if not items:
        return "Nessun dato disponibile"
    
    # Render as definition list
    html_parts = ['<dl class="pipeline-details">']
    for label, value in items:
        html_parts.append(f'<dt>{_escape(label)}:</dt>')
        html_parts.append(f'<dd>{_escape(value)}</dd>')
    html_parts.append('</dl>')
    return "\n".join(html_parts)


def _render_licence_template(tag: str) -> str:
    """Render a licence template as a link to Commons."""
    # Strip braces and create link
    clean_name = tag.strip("{}").strip()
    encoded_name = clean_name.replace(" ", "%20")
    return f'<a href="https://commons.wikimedia.org/wiki/Template:{encoded_name}" target="_blank" rel="noopener">{tag}</a>'


def _render_status_reason(stmt: dict) -> str:
    """Render the SHORT status badge for the statements table.
    
    The badge says one thing; the full human reason lives in the detail list
    under the table (and as the hover tooltip) — never stuffed into the
    status cell.
    """
    status = stmt.get("status", "ok")
    
    if status == "ok":
        return '<span class="wd-status-ok">OK</span>'
    
    badges = {
        "absent": "Assente su Wikidata",
        "unsearched": "Da cercare",
        "ambiguous": "Candidati da verificare",
        "date_estimate": "Data stimata",
    }
    label = badges.get(stmt.get("state", ""), "Rivedere")
    reason = stmt.get("reason")
    tip = f' title="{_escape(reason)}"' if reason else ""
    return f'<span class="wd-status-review"{tip}>{label}</span>'


def _sort_key(item: dict) -> tuple[int, str]:
    """Sort key: uncertain first, then not_public_domain, then public_domain."""
    order = {"uncertain": 0, "not_public_domain": 1, "public_domain": 2}
    return (order.get(item["status"], 3), item["file"])


def _generate_html(verdicts: list[dict], images_base: str | None, ctx: any, run_manifest: any, upload_policy: str, package_upload_prefix: str | None = None) -> str:
    """Generate a self-contained HTML dashboard for review."""
    
    # Preserve original structure for context.suggestions
    verdicts_orig = verdicts
    
    # Extract items list if verdicts is a dict with "items" key
    if isinstance(verdicts, dict) and "items" in verdicts:
        verdicts = verdicts["items"]
    
    # Redefined visual system for "Editorial Precision"
    style = f'''
    :root {{
      --bg-main: #f8f9fa;
      --bg-card: #ffffff;
      --text-main: #1a1c1e;
      --text-muted: #5f6368;
      --accent-primary: #0056b3;
      --status-ok: #1a7f37;
      --status-ok-bg: #e6f4ea;
      --status-review: #b08800;
      --status-review-bg: #fff8e1;
      --status-error: #d93025;
      --status-error-bg: #fce8e6;
      --border-soft: #dadce0;
      --shadow-sm: 0 1px 2px rgba(0,0,0,0.1);
      --shadow-md: 0 4px 12px rgba(0,0,0,0.08);
      --radius-md: 8px;
      --radius-sm: 4px;
      --spacing-xs: 0.5rem;
      --spacing-sm: 0.75rem;
      --spacing-md: 1rem;
      --spacing-lg: 1.5rem;
    }}

    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.6;
      margin: 0;
      padding: 0;
      background: var(--bg-main);
      color: var(--text-main);
      display: flex;
      flex-direction: column;
    }}
    .app-container {{
      display: flex;
      flex-direction: row;
    }}
    @media (max-width: 1024px) {{
      .app-container {{
        flex-direction: column;
      }}
    }}
    .sidebar {{
      width: 320px;
      height: 100vh;
      position: sticky;
      top: 0;
      background: #fff;
      border-right: 1px solid var(--border-soft);
      overflow-y: auto;
      padding: var(--spacing-lg);
      flex-shrink: 0;
      z-index: 10;
    }}
    @media (max-width: 1024px) {{
      .sidebar {{
        width: 100%;
        height: auto;
        position: relative;
        border-right: none;
        border-bottom: 1px solid var(--border-soft);
      }}
    }}
    .sidebar h3 {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      margin-bottom: var(--spacing-md);
      padding-bottom: var(--spacing-xs);
      border-bottom: 1px solid var(--border-soft);
    }}
    .nav-group {{
      margin-bottom: var(--spacing-lg);
    }}
    .nav-set {{
      display: block;
      font-weight: 600;
      font-size: 0.9rem;
      color: var(--text-main);
      text-decoration: none;
      margin-bottom: var(--spacing-sm);
      padding: var(--spacing-xs);
      border-radius: var(--radius-sm);
      transition: background 0.2s;
    }}
    .nav-set:hover {{
      background: var(--bg-main);
    }}
    .nav-item {{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.8rem;
      color: var(--text-muted);
      text-decoration: none;
      padding: 0.2rem 0 0.2rem 0.75rem;
      border-radius: var(--radius-sm);
      transition: all 0.2s;
    }}
    .nav-item:hover {{
      color: var(--accent-primary);
      background: var(--bg-main);
    }}
    .nav-item .status-dot {{
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }}
    .nav-item .status-dot.status-green {{
      background: var(--status-ok);
    }}
    .nav-item .status-dot.status-red {{
      background: var(--status-error);
    }}
    .nav-item .status-dot.status-amber {{
      background: var(--status-review);
    }}
    .main-content {{
      flex-grow: 1;
      padding: var(--spacing-lg);
      max-width: 1100px;
      margin: 0 auto;
      width: 100%;
    }}
    .dashboard-header {{
      background: var(--bg-card);
      padding: var(--spacing-lg);
      border-radius: var(--radius-md);
      margin-bottom: var(--spacing-lg);
      box-shadow: var(--shadow-sm);
      border: 1px solid var(--border-soft);
    }}
    .dashboard-header h1 {{
      margin: 0 0 var(--spacing-md) 0;
      font-size: 1.5rem;
      font-weight: 700;
    }}
    .verdict-box {{
      margin-bottom: var(--spacing-md);
      padding: var(--spacing-md);
      background: var(--bg-main);
      border-radius: var(--radius-sm);
      border-left: 4px solid var(--accent-primary);
    }}
    .verdict-primary {{
      margin: 0 0 0.5rem 0;
      font-size: 1.1rem;
      font-weight: 600;
      color: var(--text-main);
    }}
    .verdict-merged-counts {{
      margin: 0;
      font-size: 0.9rem;
      color: var(--text-muted);
    }}
    .filter-panel {{
      background: #fff;
      padding: var(--spacing-md);
      border-radius: var(--radius-sm);
      margin-bottom: var(--spacing-lg);
      display: flex;
      gap: var(--spacing-lg);
      flex-wrap: wrap;
      border: 1px solid var(--border-soft);
    }}
    .filter-group {{
      display: flex;
      align-items: center;
      gap: var(--spacing-sm);
      font-size: 0.8rem;
      color: var(--text-muted);
    }}
    .filter-group strong {{
      color: var(--text-main);
      margin-right: var(--spacing-xs);
    }}
    .filter-group input {{
      cursor: pointer;
      width: 14px;
      height: 14px;
    }}
    .filter-group input:focus-visible {{
      outline: 2px solid var(--accent-primary);
      outline-offset: 2px;
    }}
    .filter-group input:focus-visible + label {{
      outline: 2px solid var(--accent-primary);
      outline-offset: 2px;
    }}
    .filter-group label {{
      cursor: pointer;
      margin-right: var(--spacing-sm);
      display: flex;
      align-items: center;
      gap: 0.25rem;
      transition: all 0.2s;
    }}
    .filter-group input:checked + label {{
      background: var(--status-review-bg);
      font-weight: 600;
      border-radius: var(--radius-sm);
      padding: 0 4px;
      color: var(--text-main);
    }}
    /* Tabs */
    .tab-radio {{
      display: none;
    }}
    .tab-strip {{
      display: flex;
      gap: 0;
      border-bottom: 1px solid var(--border-soft);
      margin-bottom: var(--spacing-lg);
    }}
    .tab-label {{
      padding: 0.6rem 1.2rem;
      cursor: pointer;
      border: 1px solid transparent;
      border-bottom: none;
      border-radius: var(--radius-sm) var(--radius-sm) 0 0;
      font-weight: 500;
      font-size: 0.9rem;
      color: var(--text-muted);
      background: transparent;
      transition: all 0.2s;
      margin-bottom: -1px;
    }}
    .tab-label:hover {{
      color: var(--text-main);
      background: var(--bg-main);
    }}
    .tab-label:focus-visible {{
      outline: 2px solid var(--accent-primary);
      outline-offset: -2px;
    }}
    #tab-verdetto:checked ~ .tab-strip label[for="tab-verdetto"],
    #tab-contesto:checked ~ .tab-strip label[for="tab-contesto"],
    #tab-suggerimenti:checked ~ .tab-strip label[for="tab-suggerimenti"] {{
      background: #fff;
      color: var(--accent-primary);
      border-color: var(--border-soft);
      border-bottom: 2px solid var(--accent-primary);
      font-weight: 700;
    }}
    .tab-panels {{
      position: relative;
    }}
    .tab-panel {{
      display: none;
      padding: var(--spacing-md) 0;
    }}
    #tab-verdetto:checked ~ .tab-panels .tab-panel-verdetto,
    #tab-contesto:checked ~ .tab-panels .tab-panel-contesto,
    #tab-suggerimenti:checked ~ .tab-panels .tab-panel-suggerimenti {{
      display: block;
    }}
    .tab-panel-contesto .context-panel h2,
    .tab-panel-suggerimenti .suggestions-panel h2 {{
      display: none;
    }}
    .context-panel, .suggestions-panel {{
      background: #fff;
      border: 1px solid var(--border-soft);
      border-radius: var(--radius-md);
      padding: var(--spacing-lg);
      margin-bottom: var(--spacing-lg);
      box-shadow: var(--shadow-sm);
    }}
    .context-panel h2, .suggestions-panel h2 {{
      margin: 0 0 var(--spacing-md) 0;
      font-size: 1.1rem;
      color: var(--accent-primary);
    }}
    .context-fields {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      gap: var(--spacing-md);
    }}
    .context-fields p {{
      margin: 0;
      font-size: 0.875rem;
      color: var(--text-muted);
    }}
    .context-fields strong {{
      color: var(--text-main);
    }}
    .suggestions-panel ul {{
      margin: 0;
      padding-left: 1.2rem;
    }}
    .suggestions-panel li {{
      margin-bottom: 0.75rem;
      font-size: 0.9rem;
      color: var(--text-main);
    }}
    .set-section {{
      background: var(--bg-card);
      border: 1px solid var(--border-soft);
      border-radius: var(--radius-md);
      margin-bottom: var(--spacing-lg);
      padding: var(--spacing-lg);
      box-shadow: var(--shadow-sm);
    }}
    .set-section h2 {{
      margin: 0 0 0.5rem 0;
      font-size: 1.25rem;
      font-weight: 600;
    }}
    .series-id {{
      color: var(--text-muted);
      font-size: 0.875rem;
      margin: 0 0 var(--spacing-md) 0;
    }}
    .status-summary {{
      display: flex;
      gap: var(--spacing-md);
      margin-bottom: var(--spacing-lg);
      font-size: 0.8rem;
      font-weight: 500;
    }}
    .summary-green {{ color: var(--status-ok); }}
    .summary-red {{ color: var(--status-error); }}
    .summary-amber {{ color: var(--status-review); }}
    .warnings-block {{
      background: var(--status-review-bg);
      border: 1px solid var(--status-review);
      border-radius: var(--radius-sm);
      padding: var(--spacing-md);
      margin-bottom: var(--spacing-lg);
    }}
    .warnings-block h4 {{
      margin: 0 0 0.5rem 0;
      color: var(--status-review);
      font-size: 0.9rem;
      text-transform: uppercase;
    }}
    .warnings-block ul {{
      margin: 0;
      padding-left: 1.2rem;
      font-size: 0.875rem;
    }}
    .card {{
      background: var(--bg-card);
      border: 1px solid var(--border-soft);
      border-radius: var(--radius-md);
      margin-bottom: var(--spacing-lg);
      box-shadow: var(--shadow-sm);
      overflow: hidden;
      transition: box-shadow 0.2s;
    }}
    .card:hover {{
      box-shadow: var(--shadow-md);
    }}
    .card-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: var(--spacing-md);
      border-bottom: 1px solid var(--border-soft);
      background: #fafafa;
    }}
    .filename {{
      margin: 0;
      font-size: 0.95rem;
      font-weight: 600;
      word-break: break-all;
      color: var(--text-main);
    }}
    .badges {{
      display: flex;
      gap: var(--spacing-sm);
      align-items: center;
      flex-wrap: wrap;
    }}
    .badge {{
      padding: 0.2rem 0.6rem;
      border-radius: var(--radius-sm);
      font-weight: 600;
      font-size: 0.75rem;
      text-transform: uppercase;
    }}
    .badge.status-green {{
      background: var(--status-ok-bg);
      color: var(--status-ok);
      border: 1px solid var(--status-ok);
    }}
    .badge.status-red {{
      background: var(--status-error-bg);
      color: var(--status-error);
      border: 1px solid var(--status-error);
    }}
    .badge.status-amber {{
      background: var(--status-review-bg);
      color: var(--status-review);
      border: 1px solid var(--status-review);
    }}
    .upload-badge {{
      padding: 0.2rem 0.5rem;
      border-radius: var(--radius-sm);
      font-weight: 600;
      font-size: 0.7rem;
      text-transform: uppercase;
      border: 1px solid transparent;
    }}
    .upload-badge.upload-ready {{
      background: var(--status-ok-bg);
      color: var(--status-ok);
      border-color: var(--status-ok);
    }}
    .upload-badge.upload-review {{
      background: var(--status-review-bg);
      color: var(--status-review);
      border-color: var(--status-review);
    }}
    .upload-badge.upload-no {{
      background: #eee;
      color: #666;
      border-color: #ccc;
    }}
    .upload-reason-line {{
      font-size: 0.8rem;
      color: var(--text-main);
      margin-top: var(--spacing-xs);
      line-height: 1.4;
    }}
    .thumbnails-container {{
      display: flex;
      flex-wrap: wrap;
      gap: var(--spacing-sm);
      background: #f1f3f4;
      border-bottom: 1px solid var(--border-soft);
      padding: var(--spacing-sm);
    }}
    .thumbnail {{
      width: 120px;
      max-height: 160px;
      object-fit: contain;
      background: #fff;
      border: 1px solid var(--border-soft);
      border-radius: var(--radius-sm);
      transition: transform 0.2s;
    }}
    .thumbnail:hover {{
      transform: scale(1.05);
    }}
    .card-body {{
      padding: var(--spacing-md);
      line-height: 1.6;
    }}
    .card-body .meta-row {{
      margin-bottom: var(--spacing-md);
      padding-bottom: var(--spacing-sm);
      border-bottom: 1px solid var(--border-soft);
    }}
    .meta-row {{
      display: flex;
      gap: var(--spacing-lg);
      margin-bottom: var(--spacing-md);
      font-size: 0.85rem;
    }}
    .confidence {{
      color: var(--text-muted);
    }}
    .human-review {{
      background: var(--status-review-bg);
      border-left: 4px solid var(--status-review);
      padding: var(--spacing-sm) var(--spacing-md);
      margin-bottom: var(--spacing-md);
      font-weight: 600;
      font-size: 0.85rem;
      color: var(--status-review);
      line-height: 1.5;
    }}
    .shorter-term {{
      background: #e8f0fe;
      border-left: 4px solid var(--accent-primary);
      padding: var(--spacing-sm) var(--spacing-md);
      margin-bottom: var(--spacing-md);
      font-weight: 600;
      font-size: 0.85rem;
      color: var(--accent-primary);
      line-height: 1.5;
    }}
    section {{
      margin-bottom: var(--spacing-md);
      line-height: 1.7;
    }}
    section h4 {{
      margin: 0 0 var(--spacing-sm) 0;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-muted);
      border-bottom: 1px solid var(--border-soft);
      padding-bottom: 0.25rem;
    }}
    section p {{
      margin: 0.4rem 0;
      font-size: 0.9rem;
    }}
    section ul {{
      margin: 0.5rem 0;
    }}
    ul {{
      margin: 0;
      padding-left: 1.2rem;
      font-size: 0.875rem;
    }}
    li.missing {{
      color: var(--status-review);
      font-weight: 500;
    }}
    li.blocker {{
      color: var(--status-error);
      font-weight: 800;
      text-decoration: underline;
    }}
    .scrollable {{
      max-height: 200px;
      overflow-y: auto;
      border: 1px solid var(--border-soft);
      border-radius: var(--radius-sm);
      padding: var(--spacing-sm);
      background: #fafafa;
    }}
    pre {{
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.85rem;
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      color: var(--text-main);
    }}
    .trail-block {{
      margin-top: var(--spacing-md);
      border: 1px solid var(--border-soft);
      border-radius: var(--radius-sm);
    }}
    .trail-block summary {{
      cursor: pointer;
      padding: var(--spacing-sm) var(--spacing-md);
      background: var(--bg-main);
      font-weight: 600;
      font-size: 0.85rem;
      color: var(--text-main);
      line-height: 1.5;
    }}
    .pipeline-details {{
      margin: 0;
      padding: var(--spacing-sm) var(--spacing-md);
      font-size: 0.85rem;
    }}
    .pipeline-details dt {{
      font-weight: 600;
      color: var(--text-muted);
      margin-top: var(--spacing-sm);
      margin-bottom: 0.1rem;
    }}
    .pipeline-details dd {{
      margin-left: 0;
      margin-bottom: var(--spacing-sm);
      color: var(--text-main);
      word-break: break-word;
    }}
    .trail-table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.8rem;
    }}
    .trail-table th, .trail-table td {{
      padding: 0.5rem;
      border: 1px solid var(--border-soft);
      text-align: left;
    }}
    .trail-table th {{
      background: #f1f3f4;
      font-weight: 600;
      color: var(--text-muted);
    }}
    .status-green {{ color: var(--status-ok); }}
    .status-red {{ color: var(--status-error); }}
    .status-amber {{ color: var(--status-review); }}
    .set-footer {{
      margin-top: var(--spacing-lg);
      text-align: right;
    }}
    .back-to-set-top {{
      font-size: 0.8rem;
      color: var(--accent-primary);
      text-decoration: none;
      font-weight: 500;
    }}
    .back-to-set-top:hover {{
      text-decoration: underline;
    }}
    .date-determination {{
      background: #eef4ff;
      border-left: 4px solid var(--accent-primary);
      padding: var(--spacing-sm) var(--spacing-md);
      margin-bottom: var(--spacing-md);
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
      line-height: 1.6;
    }}
    .date-determination h4 {{
      color: var(--accent-primary);
      border-bottom: none;
      margin-bottom: var(--spacing-xs);
    }}
    .date-determination p {{
      font-size: 0.875rem;
      margin: 0.3rem 0;
    }}
    .country-candidates {{
      background: var(--status-review-bg);
      border-left: 4px solid var(--status-review);
      padding: var(--spacing-sm) var(--spacing-md);
      margin-bottom: var(--spacing-md);
      border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
      line-height: 1.5;
    }}
    .country-candidates h4 {{
      color: var(--status-review);
      border-bottom: none;
      margin-bottom: var(--spacing-xs);
    }}
    .country-candidates ul {{
      margin: 0.5rem 0 0 0;
      font-size: 0.8rem;
    }}
    .estimate-marker {{
      color: var(--status-review);
      font-style: italic;
      font-weight: normal;
    }}
    .wikidata-block {{
      background: #fff;
      border: 1px solid var(--border-soft);
      border-radius: var(--radius-sm);
      padding: var(--spacing-md);
      margin-bottom: var(--spacing-md);
      box-shadow: var(--shadow-sm);
    }}
    .wikidata-block h4 {{
      color: var(--accent-primary);
      border-bottom: 1px solid var(--border-soft);
      margin-bottom: var(--spacing-sm);
    }}
    .wikidata-block h5 {{
      margin: var(--spacing-md) 0 var(--spacing-xs) 0;
      font-size: 0.7rem;
      text-transform: uppercase;
      color: var(--text-muted);
      letter-spacing: 0.05em;
    }}
    .wd-statements {{
      width: 100%;
      font-size: 0.75rem;
      border-collapse: collapse;
      margin-bottom: var(--spacing-sm);
    }}
    .wd-statements th, .wd-statements td {{
      padding: 0.5rem;
      border: 1px solid var(--border-soft);
      text-align: left;
    }}
    .wd-statements th {{
      background: var(--bg-main);
      font-weight: 600;
      color: var(--text-muted);
    }}
    .wd-status-ok {{
      color: var(--status-ok);
      font-weight: 600;
    }}
    .wd-status-review {{
      background: var(--status-review-bg);
      color: var(--status-review);
      font-weight: 600;
      padding: 0.1rem 0.3rem;
      border-radius: var(--radius-sm);
    }}
    .wd-reasons {{
      margin: 0.35rem 0 0 0;
      padding-left: 1.1rem;
      font-size: 0.85rem;
      color: var(--text-muted, #666666);
    }}
    .wd-reasons li {{
      margin-bottom: 0.15rem;
      line-height: 1.45;
    }}
    .qualifier-circa {{
      color: var(--status-review);
      font-weight: bold;
      font-size: 1.1em;
      margin-left: 0.2rem;
    }}
    .wd-review {{
      background: var(--status-review-bg);
      border-left: 3px solid var(--status-review);
      padding: var(--spacing-sm) var(--spacing-md);
      margin-top: var(--spacing-sm);
      font-size: 0.8rem;
      color: var(--status-review);
      line-height: 1.5;
    }}
    .wd-no-data {{
      color: var(--text-muted);
      font-style: italic;
      font-size: 0.8rem;
    }}
    .lightbox-overlay {{
      display: none;
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(26, 28, 30, 0.95);
      z-index: 9999;
      justify-content: center;
      align-items: center;
      padding: var(--spacing-lg);
      backdrop-filter: blur(4px);
    }}
    .lightbox-overlay:target {{
      display: flex;
    }}
    .lightbox-content {{
      max-width: 90%;
      max-height: 90%;
      position: relative;
    }}
    .lightbox-content img {{
      max-width: 100%;
      max-height: 90vh;
      object-fit: contain;
      border-radius: var(--radius-sm);
      box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
    }}
    .lightbox-close {{
      position: absolute;
      top: -3rem;
      right: 0;
      background: #fff;
      color: var(--text-main);
      padding: 0.5rem 1rem;
      border-radius: var(--radius-sm);
      text-decoration: none;
      font-weight: 600;
      font-size: 0.875rem;
      transition: background 0.2s;
    }}
    .lightbox-close:hover {{
      background: var(--bg-main);
    }}
    .back-to-top {{
      position: fixed;
      bottom: var(--spacing-lg);
      right: var(--spacing-lg);
      background: var(--accent-primary);
      color: #fff;
      padding: 0.6rem 1.2rem;
      border-radius: 50px;
      text-decoration: none;
      font-weight: 600;
      font-size: 0.875rem;
      box-shadow: var(--shadow-md);
      z-index: 1000;
      transition: transform 0.2s, background 0.2s;
    }}
    .back-to-top:hover {{
      transform: translateY(-2px);
      background: #004494;
    }}
    .dashboard-footer {{
      margin-top: var(--spacing-xl);
      padding: var(--spacing-md);
      background: var(--bg-main);
      border-top: 1px solid var(--border-soft);
      text-align: center;
    }}
    .dashboard-footer p {{
      margin: 0.25rem 0;
    }}
    @media (max-width: 600px) {{
      .card-header {{
        flex-direction: column;
        align-items: flex-start;
        gap: var(--spacing-sm);
      }}
      .filter-panel {{
        flex-direction: column;
        gap: var(--spacing-md);
      }}
      .meta-row {{
        flex-direction: column;
        gap: var(--spacing-xs);
      }}
      .trail-table {{
        font-size: 0.7rem;
      }}
    }}
    #filter-status-all:not(:checked) ~ .main-content .card {{ display: none; }}
    #filter-status-public_domain:checked ~ .main-content .card.filter-status-public_domain {{ display: block; }}
    #filter-status-not_public_domain:checked ~ .main-content .card.filter-status-not_public_domain {{ display: block; }}
    #filter-status-uncertain:checked ~ .main-content .card.filter-status-uncertain {{ display: block; }}
    #filter-upload-all:not(:checked) ~ .main-content .card {{ display: none; }}
    #filter-upload-ready:checked ~ .main-content .card.filter-upload-ready {{ display: block; }}
    #filter-upload-review:checked ~ .main-content .card.filter-upload-review {{ display: block; }}
    #filter-upload-no:checked ~ .main-content .card.filter-upload-no {{ display: block; }}
    #filter-status-all:not(:checked) ~ .main-content #filter-upload-all:not(:checked) ~ .card {{ display: none; }}
    #filter-status-public_domain:checked ~ .main-content #filter-upload-ready:checked ~ .card.filter-status-public_domain.filter-upload-ready {{ display: block; }}
    #filter-status-public_domain:checked ~ .main-content #filter-upload-review:checked ~ .card.filter-status-public_domain.filter-upload-review {{ display: block; }}
    #filter-status-public_domain:checked ~ .main-content #filter-upload-no:checked ~ .card.filter-status-public_domain.filter-upload-no {{ display: block; }}
    #filter-status-not_public_domain:checked ~ .main-content #filter-upload-ready:checked ~ .card.filter-status-not_public_domain.filter-upload-ready {{ display: block; }}
    #filter-status-not_public_domain:checked ~ .main-content #filter-upload-review:checked ~ .card.filter-status-not_public_domain.filter-upload-review {{ display: block; }}
    #filter-status-not_public_domain:checked ~ .main-content #filter-upload-no:checked ~ .card.filter-status-not_public_domain.filter-upload-no {{ display: block; }}
    #filter-status-uncertain:checked ~ .main-content #filter-upload-ready:checked ~ .card.filter-status-uncertain.filter-upload-ready {{ display: block; }}
    #filter-status-uncertain:checked ~ .main-content #filter-upload-review:checked ~ .card.filter-status-uncertain.filter-upload-review {{ display: block; }}
    #filter-status-uncertain:checked ~ .main-content #filter-upload-no:checked ~ .card.filter-status-uncertain.filter-upload-no {{ display: block; }}
  '''
    
    # Sorters
    sorted_verdicts = sorted(verdicts, key=_sort_key)
    
    # Grouping by material folder instead of set_id
    sets = {}
    for v in sorted_verdicts:
        # Extract folder from file path (e.g., "Cardinale-Del-Val/1_fronte.png" -> "Cardinale-Del-Val")
        file_path = v.get("file", "")
        folder = file_path.rsplit("/", 1)[0] if "/" in file_path else None
        # Also check explicit folder field or extracted_facts
        explicit_folder = v.get("folder") or v.get("extracted_facts", {}).get("folder")
        s_id = explicit_folder if explicit_folder else (folder if folder else v.get("set_id", "unspecified"))
        if s_id not in sets:
            sets[s_id] = []
        sets[s_id].append(v)
    
    # Header components
    verdict_primary = "Verdetto: " + (
        "Tutti gli item sono nel pubblico dominio" if all(v["status"] == "public_domain" for v in verdicts) else
        "Alcuni item richiedono revisione o non sono nel pubblico dominio"
    )
    
    counts = {
        "public_domain": sum(1 for v in verdicts if v["status"] == "public_domain"),
        "not_public_domain": sum(1 for v in verdicts if v["status"] == "not_public_domain"),
        "uncertain": sum(1 for v in verdicts if v["status"] == "uncertain"),
    }
    merged_counts = f"Pubblico Dominio: {counts['public_domain']} | Non PD: {counts['not_public_domain']} | Incerti: {counts['uncertain']}"
    
    # Metadata for footer - get from verdicts data, not ctx
    # verdicts is already extracted to list at this point
    first_verdict = verdicts[0] if verdicts else {}
    rules_version = first_verdict.get("rule_table_version", ctx.get("rules_version", "Sconosciuta") if ctx else "Sconosciuta")
    # Generate timestamp for the report
    from datetime import datetime
    generated_at = ctx.get("generated_at") if ctx and ctx.get("generated_at") else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_registry = run_manifest.get("model_registry", "") if run_manifest else ""
    
    # Sidebar - group by material folder
    sidebar_html = '<div class="sidebar"><h3 class="sidebar-title">Navigazione</h3>'
    for group_label, items in sets.items():
        sidebar_html += f'<div class="nav-group"><a href="#set-{html.escape(group_label)}" class="nav-set">{html.escape(group_label)}</a>'
        for item in items:
            status_cls = _status_color(item["status"])
            sidebar_html += f'<a href="#item-{item["file"]}" class="nav-item"><span class="status-dot status-{status_cls}"></span>{item["file"]}</a>'
        sidebar_html += '</div>'
    sidebar_html += '</div>'
    
    # Filters
    filter_html = f'''
    <div class="filter-panel">
      <div class="filter-group">
        <strong class="filter-label">Stato:</strong>
        <input type="radio" name="status" id="filter-status-all" checked> <label for="filter-status-all">Tutti</label>
        <input type="radio" name="status" id="filter-status-public_domain"> <label for="filter-status-public_domain">PD</label>
        <input type="radio" name="status" id="filter-status-not_public_domain"> <label for="filter-status-not_public_domain">Non PD</label>
        <input type="radio" name="status" id="filter-status-uncertain"> <label for="filter-status-uncertain">Incerti</label>
      </div>
      <div class="filter-group">
        <strong class="filter-label">Upload:</strong>
        <input type="radio" name="upload" id="filter-upload-all" checked> <label for="filter-upload-all">Tutti</label>
        <input type="radio" name="upload" id="filter-upload-ready"> <label for="filter-upload-ready">Pronti</label>
        <input type="radio" name="upload" id="filter-upload-review"> <label for="filter-upload-review">Da Rivedere</label>
        <input type="radio" name="upload" id="filter-upload-no"> <label for="filter-upload-no">No</label>
      </div>
    </div>
    '''
    
    # Top block (Verdict, Context, Suggestions) - tabs only for summaries
    header_html = f'''
    <div class="dashboard-header">
      <h1 class="dashboard-title">Dashboard di Revisione Pubblico Dominio</h1>
      <div class="verdict-box">
        <p class="verdict-primary">{verdict_primary}</p>
        <p class="verdict-merged-counts">{merged_counts}</p>
      </div>
    </div>
    <input type="radio" name="tab" id="tab-verdetto" class="tab-radio" checked>
    <input type="radio" name="tab" id="tab-contesto" class="tab-radio">
    <input type="radio" name="tab" id="tab-suggerimenti" class="tab-radio">
    <div class="tab-strip">
      <label for="tab-verdetto" class="tab-label">Verdetto</label>
      <label for="tab-contesto" class="tab-label">Contesto</label>
      <label for="tab-suggerimenti" class="tab-label">Suggerimenti</label>
    </div>
    <div class="tab-panels">
      <div class="tab-panel tab-panel-verdetto" role="tabpanel" id="panel-verdetto">
        <div class="verdetto-summary">
          <p class="verdict-merged-counts">{merged_counts}</p>
        </div>
      </div>
      <div class="tab-panel tab-panel-contesto" role="tabpanel" id="panel-contesto">
        <div class="context-panel">
          <h2 class="panel-title">Contesto cartella</h2>
            <div class="context-fields">
              {' '.join([f'<p><strong class="field-label">{_translate_context_key(k)}:</strong> {_escape(str(v))}</p>' for k, v in ctx.items()]) if ctx else '<p>Nessun contesto disponibile</p>'}
            </div>
        </div>
      </div>
      <div class="tab-panel tab-panel-suggerimenti" role="tabpanel" id="panel-suggerimenti">
        <div class="suggestions-panel">
          <h2 class="panel-title">Suggerimenti Generali</h2>
           <ul class="suggestions-list">
             {' '.join([f'<li>{_escape(s)}</li>' for s in context.suggestions(ctx, verdicts_orig)]) if ctx and context.suggestions(ctx, verdicts_orig) else '<li>Nessun suggerimento disponibile</li>'}
           </ul>
        </div>
      </div>
    </div>
    '''
    
    # Item Cards and Sets - render BELOW tabs, visible at all times
    main_content_html = ''
    for group_label, items in sets.items():
        # Only show set ID if it's different from folder and not "unspecified"
        set_id_raw = items[0].get("set_id") if items else None
        show_set_id = set_id_raw and set_id_raw != "unspecified" and set_id_raw != group_label
        set_html = f'<div class="set-section" id="set-{html.escape(group_label)}">'
        if show_set_id:
            set_html += f'<h2 class="set-title">Set: {html.escape(set_id_raw)}</h2><p class="series-id">ID Serie: {html.escape(set_id_raw)}</p>'
        else:
            set_html += f'<h2 class="set-title">{html.escape(group_label)}</h2>'
        
        # Set status summary
        set_pd = sum(1 for v in items if v["status"] == "public_domain")
        set_not_pd = sum(1 for v in items if v["status"] == "not_public_domain")
        set_unc = sum(1 for v in items if v["status"] == "uncertain")
        set_html += f'<div class="status-summary"><span class="summary-green">PD: {set_pd}</span> <span class="summary-red">Non PD: {set_not_pd}</span> <span class="summary-amber">Incerti: {set_unc}</span></div>'
        
        # Item cards
        for v in items:
            status_cls = f"filter-status-{v['status']}"
            upload_flag = v.get("upload", {}).get("flag", "no")
            upload_cls = f"filter-upload-{upload_flag}"
            
            # Status badge - show Italian label, not raw enum
            status_labels = {
                "public_domain": "Pubblico Dominio",
                "not_public_domain": "Non PD",
                "uncertain": "Incerto",
            }
            status_badge = f'<span class="badge status-{_status_color(v["status"])}">{status_labels.get(v["status"], v["status"])}</span>'
            # Upload badge - show "Da rivedere" when upload flag is "review"
            upload_label = "Da rivedere" if upload_flag == "review" else upload_flag
            upload_badge = f'<span class="upload-badge upload-{upload_flag}">{upload_label}</span>'
            # Get upload reason for display
            upload_reason = v.get("upload", {}).get("reason", "")
            
            thumbs_html = ''
            if images_base:
                for img in v.get("extracted_facts", {}).get("images", []):
                    img_path = f"{images_base}/{img}"
                    thumbs_html += f'<a href="#light-{img}"><img src="{img_path}" class="thumbnail" alt="{img}"></a>'
            
            wd_html = '<div class="wikidata-block"><h4 class="block-title">Dati per Wikidata</h4>'
            if v.get("wikidata"):
                wd_data = v["wikidata"]
                wd_html += '<h5 class="block-subtitle">Statements</h5><table class="wd-statements"><thead><tr><th class="col-prop">Proprietà</th><th class="col-val">Valore</th><th class="col-stat">Stato</th></tr></thead><tbody>'
                # Handle both list and dict formats for statements
                statements = wd_data.get("statements", [])
                if isinstance(statements, list):
                    for stmt in statements:
                        val_text = str(stmt.get("value", ""))
                        qid = stmt.get("qid")
                        if qid:
                            val_text += f' <a href="https://www.wikidata.org/wiki/{qid}" target="_blank" rel="noopener" class="wd-qid">{qid}</a>'
                        prop = stmt.get("property", "")
                        label = stmt.get("label", "")
                        prop_display = f"{prop} — {label}" if label else prop
                        status_tag = _render_status_reason(stmt)
                        wd_html += f'<tr><td>{prop_display}</td><td>{val_text}</td><td>{status_tag}</td></tr>'
                else:
                    # Old dict format
                    for prop, val in statements.items():
                        status_tag = '<span class="wd-status-ok">OK</span>' if val.get("status") == "ok" else '<span class="wd-status-review">Rivedere</span>'
                        val_text = str(val.get("value", ""))
                        if val.get("qualifier") == "circa":
                            val_text += '<span class="qualifier-circa">circa</span>'
                        wd_html += f'<tr><td>{prop}</td><td>{val_text}</td><td>{status_tag}</td></tr>'
                wd_html += '</tbody></table>'
                
                # Detail list: the full reason per flagged statement, under the table
                if isinstance(statements, list):
                    details = [
                        f'<li><strong>{stmt.get("label") or stmt.get("property")}:</strong> {_escape(stmt.get("reason"))}</li>'
                        for stmt in statements
                        if stmt.get("status") == "da_rivedere" and stmt.get("reason")
                    ]
                    if details:
                        wd_html += f'<ul class="wd-reasons">{"".join(details)}</ul>'
                
                # Handle both "categories" and "proposed_categories"
                categories = wd_data.get("categories") or wd_data.get("proposed_categories")
                if categories:
                    wd_html += '<h5 class="block-subtitle">Categorie Proposte</h5><ul class="wd-cats">'
                    for cat in categories:
                        # Render as link to Wikimedia Commons category
                        encoded_cat = cat.replace(" ", "_")
                        wd_html += f'<li><a href="https://commons.wikimedia.org/wiki/Category:{encoded_cat}" target="_blank" rel="noopener">{cat}</a></li>'
                    wd_html += '</ul>'
                
                # Handle both "information" and "info"
                info = wd_data.get("information") or wd_data.get("info")
                if info:
                    info_text = info if isinstance(info, str) else info.get("description", "")
                    wd_html += f'<div class="wd-review"><strong class="review-label">Info:</strong> {info_text}</div>'
            else:
                wd_html += '<p class="wd-no-data">Nessun dato disponibile</p>'
            wd_html += '</div>'

            date_html = '<div class="date-determination"><h4 class="block-title">Determinazione della data</h4>'
            ef = v.get("extracted_facts", {})
            date_evidence = ef.get("date_evidence")
            reference_year = ef.get("reference_year")
            year_source = ef.get("year_source")
            if date_evidence or reference_year or year_source:
                if date_evidence:
                    date_html += f'<p class="date-line">Evidenza: {date_evidence}</p>'
                if reference_year:
                    date_html += f'<p class="date-line">Anno di riferimento: {reference_year}</p>'
                if year_source:
                    source_label = {"visible_text": "Testo visibile", "postmark": "Timbro postale", "folder_context": "Contesto cartella", "visual_estimate": "Stima visiva"}.get(year_source, year_source)
                    if year_source == "visual_estimate":
                        date_html += f'<p class="date-line">Fonte: {source_label} <span class="estimate-marker">(stimata)</span></p>'
                    else:
                        date_html += f'<p class="date-line">Fonte: {source_label}</p>'
            else:
                date_html += '<p class="date-line">Nessun dato disponibile</p>'
            date_html += '</div>'
            
            # Full transcription panel: OCR per side plus the vision transcription,
            # both complete — the dashboard is the review evidence surface
            transcription_parts = []
            ocr_map = ef.get("ocr_texts") or {}
            for img in (ef.get("images") or []):
                side_text = ocr_map.get(img.split("/")[-1])
                if side_text:
                    transcription_parts.append(
                        f'<details class="ocr-side"><summary>{html.escape(img.split("/")[-1])} — trascrizione OCR</summary>'
                        f'<pre class="transcription">{html.escape(side_text).replace("|", chr(10))}</pre></details>'
                    )
            vision_text = ef.get("visible_text") or ""
            if vision_text:
                transcription_parts.append(
                    f'<details class="ocr-side" open><summary>Trascrizione visione</summary>'
                    f'<pre class="transcription">{html.escape(vision_text).replace("|", chr(10))}</pre></details>'
                )
            if transcription_parts:
                transcription_html = f'<div class="transcription-block"><h4 class="block-title">Trascrizione</h4>{"".join(transcription_parts)}</div>'
            else:
                transcription_html = ''
            
            # Licence templates section - generate outside f-string
            commons_tmpl = v.get("commons_templates", [])
            source_tmpl = v.get("source_template", "")
            caveat_tmpl = v.get("caveat_templates", [])
            template_candidates = v.get("commons_templates_candidate") or []
            if commons_tmpl or source_tmpl or caveat_tmpl or template_candidates:
                licence_items = []
                for t in commons_tmpl:
                    licence_items.append(f'<li class="licence-tag">{_render_licence_template(t)}</li>')
                if source_tmpl:
                    licence_items.append(f'<li class="licence-tag source">{_render_licence_template(source_tmpl)}</li>')
                for t in caveat_tmpl:
                    licence_items.append(f'<li class="licence-tag caveat">{_render_licence_template(t)}</li>')
                # Uncertain verdicts show nature-tailored candidates, never applied
                for cand in template_candidates:
                    licence_items.append(
                        f'<li class="licence-tag candidate">{_render_licence_template(cand.get("template", ""))} '
                        f'— {html.escape(cand.get("perche", ""))} '
                        f'<span class="estimate-marker">(candidato, non applicato: verdetto incerto)</span></li>'
                    )
                licence_html = f'<div class="licence-block"><h4 class="block-title">Licenze e Template</h4><ul class="licence-list">{"".join(licence_items)}</ul></div>'
            else:
                licence_html = '<div class="licence-block"><h4 class="block-title">Licenze e Template</h4><p class="no-licence">Nessun template disponibile</p></div>'
            
            # Country candidates section - FIX: Only show when country is unknown AND candidates exist
            # When country_of_origin is set, hide the section entirely
            # When country is unknown but no candidates, show action line
            country_of_origin = v.get("country_of_origin") or ctx.get("country_of_origin")
            country_candidates = v.get("country_candidates", [])
            
            if country_of_origin:
                # Country is known - hide section entirely
                country_html = ''
            elif country_candidates:
                # Country unknown but candidates exist - show as today
                country_items = []
                for cand in country_candidates:
                    country_items.append(f'<li><strong>{cand.get("country", "")}</strong>: {cand.get("reason", "")} <span class="affidabilita">(Affidabilità: {cand.get("reliability", "N/A")})</span></li>')
                country_html = f'<div class="country-block"><h4 class="block-title">Candidati Paese (Affidabilità)</h4><ul class="country-list">{"".join(country_items)}</ul></div>'
            else:
                # Country unknown and no candidates - show action line
                country_html = '<div class="country-block"><h4 class="block-title">Paese</h4><p class="no-country">Paese non ipotizzabile: dichiara il paese d\'origine nel contesto della cartella (es. "country_of_origin": "IT")</p></div>'
            
            # FIX 5: Planned Commons filename display — use shared derive_filename from push.py
            # For multi-image items, show BOTH front and retro names
            pkg_prefix = package_upload_prefix
            ctx_prefix = (ctx or {}).get("upload_prefix")
            stem = Path(v.get("file", "")).stem
            item_num = stem.split("_")[0]
            if not item_num.isdecimal():
                item_num = "1"
            resolved_prefix = pkg_prefix or ctx_prefix
            if resolved_prefix:
                filename_source = "pubdom.json" if pkg_prefix else "contesto"
                # Get total items count from package
                total_items = len(items) if isinstance(items, list) else 1
                # Check if item has multiple images (front + retro)
                images = v.get("extracted_facts", {}).get("images", [])
                item_num_int = int(item_num) if item_num.isdigit() else 1
                if isinstance(images, list) and len(images) > 1:
                    # Show both front and retro using derive_filename
                    front_name = derive_filename(v, resolved_prefix, item_num_int, total_items)
                    retro_name = derive_filename(v, resolved_prefix, item_num_int, total_items).replace(".png", " (retro).png")
                    filename_html = f'<div class="filename-planned"><strong>Nome previsto su Commons:</strong><br>  Fronte: {html.escape(front_name)}<br>  Retro: {html.escape(retro_name)}<br><small>(da {filename_source})</small></div>'
                else:
                    # Single image - use derive_filename
                    planned_filename = derive_filename(v, resolved_prefix, item_num_int, total_items)
                    filename_html = f'<div class="filename-planned"><strong>Nome previsto su Commons:</strong> {html.escape(planned_filename)} (da {filename_source})</div>'
            else:
                filename_html = '<div class="filename-planned"><strong>Nome previsto su Commons:</strong> Nome file assegnato al momento del push (prefisso mancante: aggiungilo al contesto)</div>'
            
            # FIX 4: "Cosa fare adesso" block - compact checklist for reviewer
            action_items = []
            
            # 1. Actions for da_rivedere statements (use per-statement reasons)
            wd_data = v.get("wikidata", {})
            statements = wd_data.get("statements", []) if wd_data else []
            if isinstance(statements, list):
                for stmt in statements:
                    if stmt.get("status") == "da_rivedere":
                        reason = stmt.get("reason")
                        if reason:
                            action_items.append(f"<strong>{stmt.get('property', '')}:</strong> {reason}")
            
            # 2. Actions for entities_to_create (from wikidata block)
            entities_to_create = wd_data.get("entities_to_create", []) if wd_data else []
            if isinstance(entities_to_create, list):
                # Add disclosure lines for entities_to_create
                for entity in entities_to_create:
                    name = entity.get("name", "")
                    action_items.append(
                        f"<strong>Creazione predefinita:</strong> l'item «{name}» verrà creato su Wikidata con questi dati (etichetta, descrizione, classe, fonte)."
                    )
                    action_items.append(
                        f"<strong>Verifica ora i dati suggeriti prima della creazione</strong> — serve almeno una fonte (source_url), altrimenti «create-entity» salta la creazione."
                    )
                    action = entity.get("action", "")
                    if action:
                        action_items.append(f"<strong>{entity.get('role', 'entità')} «{entity.get('name', '')}»:</strong> {action}")
            
            # 2. Actions for non-copyright blockers
            for blocker in v.get("blockers_non_copyright", []):
                blocker_lower = blocker.lower()
                if "corrispondenza" in blocker_lower or "art. 93" in blocker_lower:
                    action_items.append("Cartolina spedita a persona identificata: valuta il consenso dell'Art. 93 (destinatario/eredi)")
                else:
                    # Generic blocker action
                    action_items.append(f"Verificare: {blocker}")
            
            # 3. Estimated dates (from extracted_facts)
            ef = v.get("extracted_facts", {})
            year_source = ef.get("year_source")
            ref_year = ef.get("reference_year")
            if year_source in ("estimated", "folder_context") and ref_year:
                action_items.append(f"Data stimata {ref_year} (fonte: {year_source}): confermala dal pezzo")
            
            # 4. Upload flag review
            upload_flag = v.get("upload", {}).get("flag", "")
            upload_reason = v.get("upload", {}).get("reason", "")
            if upload_flag == "review" and upload_reason:
                action_items.append(f"Revisione upload richiesta: {upload_reason}")
            
            # 5. Ready items with no flags
            if not action_items and upload_flag == "no":
                action_items.append("Pronto al caricamento: nessuna azione richiesta")
            
            # Build "Cosa fare adesso" HTML
            if action_items:
                what_to_do_html = '<div class="what-to-do-block"><h4 class="block-title">Cosa fare adesso</h4><ul class="what-to-do-list">'
                for action in action_items:
                    what_to_do_html += f'<li>{action}</li>'
                what_to_do_html += '</ul></div>'
            else:
                what_to_do_html = '<div class="what-to-do-block"><h4 class="block-title">Cosa fare adesso</h4><p class="no-action">Nessuna azione specifica richiesta</p></div>'
            
            card_html = f'''
            <div class="card {status_cls} {upload_cls}" id="item-{v['file']}">
              <div class="card-header">
                <p class="filename">{v['file']}</p>
                <div class="badges">
                  {status_badge} {upload_badge}
                </div>
              </div>
              <div class="thumbnails-container">
                {thumbs_html}
              </div>
              <div class="card-body">
                {f'<div class="upload-reason-line"><strong>Motivo upload:</strong> {upload_reason}</div>' if upload_reason else ''}
                {filename_html}
                <section class="issues-section">
                  <h4 class="block-title">Problemi e Blocchi</h4>
                   <ul class="issues-list">
                     {' '.join([f'<li class="{"blocker" if b.get("type") == "blocker" else "missing"}">{b.get("message")}</li>' for b in v.get("blockers", [])]) if v.get("blockers") else '<li class="no-issues">Nessun problema rilevato</li>'}
                   </ul>
                </section>
                {what_to_do_html}
                {date_html}
                {transcription_html}
                <div class="meta-row">
                  <span class="confidence">Confidenza: {_translate_confidence(v.get("confidence"))}</span>
                </div>
                {f'<div class="human-review"><strong class="review-label">Richiesta revisione umana:</strong> {v.get("human_review_reason", "N/A")}</div>' if v.get("human_review") else ''}
                {f'<div class="shorter-term"><strong class="term-label">Termine più breve:</strong> {v.get("shorter_term", "N/A")}</div>' if v.get("shorter_term") else ''}
                {licence_html}
                {country_html}
                {wd_html}
                <div class="trail-block">
                  <details>
                    <summary>Dettagli della pipeline</summary>
                    <div class="scrollable">
                       {_format_pipeline_details(v, ctx, run_manifest, images_base, upload_policy)}
                    </div>
                  </details>
                </div>
              </div>
            </div>
            '''
            set_html += card_html
            
        set_html += '<div class="set-footer"><a href="#set-' + html.escape(group_label) + '" class="back-to-set-top">Torna all\'inizio del set</a></div></div>'
        main_content_html += set_html
    
    lightbox_html = ''
    if images_base:
        for v in sorted_verdicts:
            for img in v.get("extracted_facts", {}).get("images", []):
                lightbox_html += f'''
                <div class="lightbox-overlay" id="light-{img}">
                  <div class="lightbox-content">
                    <a href="#main" class="lightbox-close">Chiudi ×</a>
                    <img src="{images_base}/{img}" alt="{img}">
                  </div>
                </div>
                 '''
    
    return f'''
    <!DOCTYPE html>
    <html lang="it">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>PubDom Dashboard</title>
      <style>{style}</style>
    </head>
    <body id="main">
      <div class="app-container">
        {sidebar_html}
        <div class="main-content">
          {header_html}
          {filter_html}
          {main_content_html}
        </div>
      </div>
      {lightbox_html}
      <footer class="dashboard-footer">
        <p class="meta-subdued">Versione tabelle regole: {rules_version}</p>
        <p class="meta-subdued">Data generazione: {generated_at}</p>
        <p class="meta-subdued">Codice e metadati: CC0 1.0 Universal</p>
        {f'<p class="model-registry">{model_registry}</p>' if model_registry else ''}
      </footer>
      <a href="#main" class="back-to-top">Torna su ↑</a>
      <script>
        document.addEventListener('keydown', function (event) {{
          if (event.key === 'Escape' && document.querySelector('.lightbox-overlay:target')) {{
            window.location.hash = '';
          }}
        }});
      </script>
    </body>
    </html>
    '''


def main(argv: list[str] | None = None) -> int:
    """Generate a static HTML review dashboard from verdicts JSON."""
    parser = argparse.ArgumentParser(description="Generate a static HTML review dashboard from verdicts JSON.")
    parser.add_argument("verdicts", help="Path to the verdicts JSON file.")
    parser.add_argument("-o", "--output", required=True, help="Output HTML file path.")
    parser.add_argument(
        "--images-base",
        help="Base directory for image thumbnails (src = {images_base}/{file}).",
    )
    parser.add_argument(
        "--context",
        help="Folder containing context.json or context.md for folder-level context",
    )
    parser.add_argument(
        "--run",
        help="Path to run manifest JSON (models used).",
    )
    parser.add_argument(
        "--upload-policy",
        choices=["strict", "pd-only", "all"],
        default="strict",
        help="Upload policy used (for display only): 'strict' (default), 'pd-only' (copyright-only reading), or 'all' (ready all items, including uncertain)."
    )
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

    # Load context if provided
    ctx = None
    if args.context:
        try:
            ctx = context.load(args.context)
        except Exception as e:
            print(f"Warning: failed to load context from {args.context}: {e}", file=sys.stderr)

    # Load run manifest: pubdom.json is canonical, a bare run.json is the legacy fallback.
    run_manifest = None
    package_upload_prefix = None
    run_path = Path(args.run) if args.run else None
    if not run_path and args.context:
        pubdom_path = Path(args.context) / "pubdom.json"
        if pubdom_path.exists():
            try:
                pubdom = json.loads(pubdom_path.read_text(encoding="utf-8"))
                if isinstance(pubdom, dict):
                    run_manifest = pubdom.get("run")
                    package_upload_prefix = pubdom.get("upload_prefix")
            except (json.JSONDecodeError, OSError) as e:
                print(f"Warning: failed to load run manifest from {pubdom_path}: {e}", file=sys.stderr)
        if run_manifest is None:
            run_path = Path(args.context) / "run.json"
    if run_manifest is None and run_path and run_path.exists():
        try:
            run_manifest = json.loads(run_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: failed to load run manifest from {run_path}: {e}", file=sys.stderr)
            run_manifest = None

    # Auto-derive images_base from context folder if not provided
    images_base = args.images_base
    if not images_base and args.context:
        images_base = args.context

    # Generate HTML
    html_content = _generate_html(verdicts, images_base, ctx, run_manifest, args.upload_policy, package_upload_prefix)

    # Write output
    output_path = Path(args.output)
    output_path.write_text(html_content, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())

