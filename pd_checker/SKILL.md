---
name: pd_checker
description: Use when determining whether archive material (photographs, postcards, letters, newspapers, manuscripts) is in the public domain under Italian, UK or Vatican law, or when triaging a folder of images before a Wikimedia Commons upload. Asks for the folder's context first, extracts text from each item, applies the copyright rules deterministically per country of origin, correlates items into sets, and renders a static HTML dashboard with suggestions.
---

# pd_checker — Italian/UK/Vatican public-domain checker

## AGENT INSTRUCTION — Country of Origin & Language/Place Suggestions

**Determine the material's language and any place evidence (printed place, postmark city, publisher). If the country of origin is not certain, propose the candidate jurisdictions returned by `country_suggest` and mark the item for human review — never assert a country silently.**

### How it works

When `country_of_origin` is missing or empty:
1. **Extract language** from `translation_lang` or `visible_text_lang` (ISO 639-1 codes)
2. **Extract place evidence** from `visible_text`, `postmark.city`, `publisher`
3. **Call `country_suggest.suggest()`** to get ranked candidates with reasons and confidence
4. **Record candidates** in `verdict["country_candidates"]` — never auto-select one
5. **Display in report** — show "Paese ipotizzato" section when candidates exist and no country was declared

### Candidate rules (deterministic)

**Language hints (ISO 639-1):**
- `it` → IT (medium confidence)
- `en` → GB **and** US (both listed, low confidence)
- `la` → VA and IT (low confidence)
- `fr` → FR (medium confidence)
- `de` → DE (medium confidence)
- `es` → ES (medium confidence)
- `pt` → PT (medium confidence)
- `nl` → NL (medium confidence)

**Place names (override/extend language hints):**
- Roma, Perugia, Milano, Napoli, Firenze → IT
- Londra, London, Edinburgh → GB
- "Città del Vaticano", Vatican, "Città del Vaticano" → VA
- New York, Chicago, Boston → US
- Paris → FR
- München, Berlin → DE

**Publisher heuristics:**
- A publisher string containing a place name adds that country (e.g., "Stab. Sansaini - Roma" → IT)

**Confidence levels:**
- Language maps to multiple countries → low
- Language maps to one country → medium
- Language + place agree → high
- Place only (no language) → medium

### CLI usage

```bash
# Language-based suggestion
python3 -m pd_checker suggest-country --language en
# → GB and US candidates (low confidence)

# Place-based suggestion
python3 -m pd_checker suggest-country --postmark "Londra 24 X/XI"
# → GB, medium confidence

# Combined (language + place agree)
python3 -m pd_checker suggest-country --language en --postmark "Londra 24 X/XI"
# → GB (high confidence), US (low confidence)
```

---

## What this does

For a folder of archive material, decide — per item — whether it is in the
public domain under the law of its **country of origin**, and render a static
HTML dashboard for human review.

Three jobs, kept separate:

- **Folder context** (asked of the user): provenance, rights-holder, country,
  period, known authors. The tool must not analyse files cold — context is what
  turns raw verdicts into *suggestions*.
- **Fact extraction** (a vision model): reads each image — dates, names, text,
  classification — with a confidence.
- **Legal evaluation** (`rules.py`): deterministic,
  versioned rule tables. Never calls a model, so identical facts yield the
  identical verdict.

Never let the model decide the law. The model extracts facts; the engine decides.

## Step 0 — Ask for folder context (do this first)

For each material folder, look for `<folder>/context.json`. **If it is absent,
ask the user** for at least: what the folder is / its provenance, who holds the
rights (private / company / institution / Holy See), the country of origin, the
approximate period, and any known authors with their death years. Then write
`context.json`:

```json
{
  "folder": "...", "collection": "...", "provenance": "...",
  "rights_holder": "private|company|public_body|publisher|holy_see|null",
  "country_of_origin": "IT|GB|VA",
  "period": [1920, 1955],
  "notes": "...",
  "known_authors": {"Nome Cognome": 1955}
}
```

Context is advisory: it fills gaps (country, holder, single-year period, author
death years) and drives the suggestions panel. It never overrides a fact that
extraction read directly from the image.

Also record the models used in `<folder>/run.json`, so the dashboard can cite
them (provenance):

```json
{
  "generated": "YYYY-MM-DD",
  "rule_table_version": "...",
  "registry": [
    {"role": "Estrazione fatti (vision)", "provider": "...", "model": "..."},
    {"role": "Valutazione / verdetto", "provider": "-", "model": "pd_checker (deterministico)"}
  ],
  "notes": "..."
}
```

The dashboard renders this as a "Modelli e riferimenti" panel. Take each
model from the runtime: the agent's own model is exposed by the runtime, and
the per-subagent models come from the opencode agent config
(`oh-my-opencode-slim.json`, preset). The legal verdict itself never uses a
model.

## Main entry point

Use the unified CLI for all operations:

```
python3 -m pd_checker <subcommand>
```

| Subcommand | Description | Network |
|------------|-------------|---------|
| `run <folder>` | Run the whole pipeline (context → engine → wikidata → correlate → report → consolidate) | No |
| `status <folder>` | Read `pubdom.json` and print a one-screen summary | No |
| `validate <folder>` | Check templates and entities in `verdicts.json` (offline) | No |
| `preflight <folder> [--offline]` | Validate upload package before Commons upload (SHA1, duplicates, completeness) | Yes (default), No with `--offline` |
| `push <folder> [--live] [--filename-prefix PREFIX]` | Upload ready items to Wikimedia Commons | No (dry-run), Yes with `--live` |
| `verify-live <folder>` | Verify templates live against Commons API | Yes |
| `update` | Refresh Commons template and Wikidata entity catalogs | Yes |
| `resolve <name>` | Cache-first entity resolution (QID lookup) | No |
| `templates-enrich` | Enrich template catalog with category metadata (network) | Yes |
| `templates-suggest` | Suggest templates by country/anonymous/restrictions (offline) | No |
| `templates-search <term>` | Search templates by name or category (offline) | No |

```bash
# Run the whole pipeline
python3 -m pd_checker run Cardinale-Del-Val

# Check status
python3 -m pd_checker status Cardinale-Del-Val

# Validate templates and entities
python3 -m pd_checker validate Cardinale-Del-Val

# Preflight check before upload (live duplicate check)
python3 -m pd_checker preflight Cardinale-Del-Val

# Preflight check offline (no network)
python3 -m pd_checker preflight Cardinale-Del-Val --offline

# Validate templates and entities
python3 -m pd_checker validate Cardinale-Del-Val

# Update catalogs (requires network)
python3 -m pd_checker update

# Resolve a name to QID
python3 -m pd_checker resolve "Rafael Merry del Val"
# → Q507540

# Enrich template catalog with categories (run once)
python3 -m pd_checker templates-enrich

# Suggest templates for Italian works
python3 -m pd_checker templates-suggest --country IT

# Suggest anonymous templates for EU works
python3 -m pd_checker templates-suggest --country EU --anonymous

# Search for templates by name or category
python3 -m pd_checker templates-search "Italy"
```

### Advanced / direct use

The package is seven modules; the library ones are internal steps run by the pipeline:

- `rules.py` — deterministic legal evaluation (Italy, United Kingdom, Vatican).
- `catalogs.py` — Commons template, Wikidata entity, QID-cache and category-cache access.
- `context.py` — folder context, country suggestion and series correlation.
- `wikidata.py` — Commons/Wikidata statement building.
- `report.py` — dashboard generation.
- `__main__.py` — the CLI, the pipeline orchestration and the upload preflight.

Import them directly instead of shelling out, e.g. `from pd_checker import rules`.

### Template selection workflow

**Before choosing a template, ALWAYS use the suggestion tools — never hand-write a tag:**

1. **Enrich the template catalog with categories** (requires network, run once):
   ```bash
   python3 -m pd_checker templates-enrich
   ```
   This fetches real category metadata from Wikimedia Commons for every template in the catalog and populates the `meta` section in `commons_templates.json`.
   
   **Note**: The MediaWiki API has strict rate limits. The command uses small batches (~5 templates) with:
   - **2-second delay** between successful batches (paces requests to avoid 429s)
   - **Retry-After header** honored on HTTP 429 (falls back to 10s/20s/30s exponential backoff)
   - **Incremental saves** after each batch (partial runs preserve progress)
   - **3 retries** on 429 before failing
    
   For large catalogs (1357+ templates), this may take several minutes. If interrupted, re-running will continue from the last saved state.

2. **Use `templates-suggest` to find the right template** for your case:
   ```bash
   # Italian work
   python3 -m pd_checker templates-suggest --country IT
   
   # Anonymous EU work
   python3 -m pd_checker templates-suggest --country EU --anonymous
   
   # Non-copyright restrictions
   python3 -m pd_checker templates-suggest --restrictions
   
   # UK work with anonymous templates
   python3 -m pd_checker templates-suggest --country GB --anonymous
   ```

3. **Use `templates-search` to find templates by keyword**:
   ```bash
   python3 -m pd_checker templates-search "Italy"
   python3 -m pd_checker templates-search "anonymous"
   ```

This ensures every suggested tag exists in the real Commons catalog with verified categories.

## Preflight check before upload

**Before uploading to Wikimedia Commons, run the preflight validator** to ensure the package is ready:

```bash
# Live check (includes duplicate detection on Commons)
python3 -m pd_checker preflight <folder>

# Offline check (no network, only local validation)
python3 -m pd_checker preflight <folder> --offline
```

The preflight performs these checks for each item:

1. **SHA1 computation** — Computes the SHA1 hash of each image file (Commons publishes SHA1 for every file)
2. **Duplicate detection** — Queries the Commons API to check if any file already exists (by SHA1)
3. **Licence tags** — Validates that all licence templates exist in the catalog
4. **Categories** — Checks that categories exist in the cache (offline, no network)
5. **Wikidata entities** — Validates that QIDs exist in the entity catalog
6. **{{Information}} completeness** — Ensures `description`, `date`, `source`, and `author` are non-empty
7. **Consistency** — Verifies `upload.flag` is `ready` and unknown fields are empty

**Output**: Per-item `READY` or `BLOCKED` with explicit reasons, plus a summary (N ready, M blocked). Exit code is 1 if any item is blocked.

**Important**: The preflight is read-only and does NOT perform the actual upload. The real upload requires a Wikimedia Commons account, a conscious human decision, and credentials via the `push` subcommand (see below).

The preflight exists so a human can see, before publishing, exactly what would be uploaded and what is missing. It never mutates anything.

## Push to Wikimedia Commons

**Upload items with `upload.flag='ready'` to Wikimedia Commons.**

```bash
# Dry-run (default): shows what would be uploaded, NO network calls
python3 -m pd_checker push <folder> [--filename-prefix PREFIX]

# Live upload: actual upload to Commons (requires credentials)
python3 -m pd_checker push <folder> --live --filename-prefix PREFIX
```

**Dry-run is the default** — without `--live` it prints the plan per item:
- Derived Commons filename
- Wikitext that would be used
- Upload comment
- Makes ZERO network calls, needs NO credentials

**Live upload requires:**
1. **Explicit `--filename-prefix`** — never upload files with meaningless names
2. **Credentials via environment variables ONLY** (never argv, never files, never printed):
   - `PD_WM_OAUTH_TOKEN` — OAuth 2 owner-only bearer token (preferred). Registered at https://meta.wikimedia.org/wiki/Special:OAuthConsumerRegistration/propose/oauth2 (check "This consumer is for use only by <user>"; the access token is shown on the confirmation page — Wikimedia OAuth is centralised on meta, NOT Commons)
   - `PD_WM_BOT_USER` + `PD_WM_BOT_PASSWORD` — bot-password login flow. Created at https://commons.wikimedia.org/wiki/Special:BotPasswords (choose the upload/edit grants; the login name becomes `<account>@<suffix>`)
   - `PD_WM_CONTACT` — contact info for User-Agent (required by Wikimedia API etiquette)

**Upload behavior:**
- Only items with `upload.flag == "ready"` are uploaded
- Sequential uploads (one image at a time)
- Honors HTTP 429 + `Retry-After` headers
- **NEVER overwrites existing files** — if response has `upload.warning` of type `exists`/`exists-normalized`/`duplicate`, the item is marked failed and the upload continues with the next item
- Never passes `ignorewarnings=true` to the API

**Security:**
- Credentials only from environment variables

**Worked example** (real output on `Cardinale-Del-Val`, run with `--upload-policy all` so items are `ready`):

```text
$ python3 -m pd_checker push Cardinale-Del-Val

== Push: Cardinale-Del-Val ==
Items pronti: 8
Modalità: DRY-RUN
Prefix: Cardinale-Del-Val

[DRY-RUN] Cardinale-Del-Val - 1.png
  File: /media/.../Cardinale-Del-Val/1_fronte.png
  Wikitext:
    {{Information
     |description=(FRONT, printed): A / LONDRA / 10 ottobre / 1865 ...
     |date=1932
     |source=non specificato (imprint: Stab. Sansaini - Roma)
     |author={{unknown|author}}
     |permission=
    }}
 ...
```
With zero ready items (strict policy) the command refuses and exits 1:

```text
$ python3 -m pd_checker push Cardinale-Del-Val
[push] 0 pronti su 8; usa --upload-policy per cambiare la politica.
```
And the real send:

```bash
export PD_WM_OAUTH_TOKEN=...            # or PD_WM_BOT_USER + PD_WM_BOT_PASSWORD
export PD_WM_CONTACT="you@example.org"
python3 -m pd_checker push Cardinale-Del-Val --live --filename-prefix "Merry del Val santino"
```
- Never logged or printed
- Multipart form data built with stdlib (no shell strings)
- ONE urllib opener with cookiejar for the whole session (login + csrf + upload share cookies)

**Output:**
- Per-item status: OK / WARNING (exists, not overwritten) / FAILED / SKIP
- Summary with counts
- Exit code 1 if any failures or warnings in live mode

**Country code mapping**:
- `IT` → "PD Italy license tags" (matches ALL templates with this category, e.g., PD-Italy, PD-Italy-EdictGov, PD-Italy-audio, PD-ItalyGov)
- `GB` → "PD UK license tags"
- `VA` → "PD Vatican license tags" (if none exist, shows generic fallbacks: PD-old-70, PD-anon-70-EU)
- `US` → "PD US license tags"
- `EU` → "PD European Union license tags"
- `--anonymous` also matches "PD-anon license tags"
- `--restrictions` lists templates in "Non-copyright restriction templates (flat list)"

**Empty country fallback**: If a country code has no specific templates (e.g., VA for Vatican), the command prints a clear message and lists generic fallbacks (PD-old-70 for old works, PD-anon-70-EU for anonymous/EU works) instead of "Candidates (0)".

## Facts schema (input)

- `file` (str), `document_type` (`photo|postcard|letter|newspaper|magazine|manuscript|unknown`)
- `country_of_origin` (`IT|GB|VA`, default `IT`)
- `author` (str|null), `author_nationality` (str|null), `author_death_year` (int|null)
- `author_role` (`photographer|illustrator|printer|publisher`|null): role of the person/firm named in `author`; absent means unknown
- `is_anonymous` (bool), `rights_holder` (`natural|company|public_body|publisher|holy_see`|null)
- `photo_classification` (`simple|artistic|unclear`), `reference_year` (int|null), `year_source` (str|null)
- `visible_text` (str), `visible_text_translation` (str), `visible_text_lang` (str|null): language code for visible_text (e.g., `it`, `en`), inferred if not provided
- `translation_lang` (str), `has_identifiable_people` (bool|null), `vlm_confidence` (`high|medium|low`)
- `date_evidence` (str|null): the exact snippet the year came from
- `postmark` (object|null): `{"city": str|null, "date_text": str|null, "year": int|null}` — a postal cancellation is strong dating evidence; capture it even when the year is illegible (city + day/month narrow the estimate)
- `publisher` (str|null): publisher imprint found in the document (e.g., "Stab. Sansaini - Roma"); used for country suggestion and anonymous contradiction detection
- `is_vatican_asset` (bool|null): true when the item is a Vatican asset (distinct from rights_holder == "holy_see")
- `set_id` (physical object: groups front/back), `part` (`front|back|single`), `series_id` (related items), `images` (all files for the item: front + back)

## Verdict schema (output)

`status` (`public_domain|not_public_domain|uncertain`), `confidence`, `reason`,
`legal_basis`, `commons_templates`, `commons_categories`, `missing_data`,
`pd_year`, `years_until_pd`, `human_review_required`, `blockers_non_copyright`,
`notes`, `rule_table_version`, `country_of_origin`, `shorter_term_applied`,
`jurisdictions` (`{origin: {...}, italy: {...}}`), `extracted_facts`.

## Wikidata statement shape

Each statement in `wikidata.statements` has:
- `property` (e.g., "P571", "P170")
- `label` (Italian label, e.g., "data di creazione", "autore")
- `value` (human-readable string)
- `time` (ISO time string for date properties, e.g., `+1955-00-00T00:00:00Z` for year precision)
- `precision` (integer: 9 for year precision)
- `qid` (Wikidata QID when resolved, else null)
- `source` (field path in verdict/context)
- `status` (`ok` | `da_rivedere`)
- `qualifiers` (optional list of `{property, label, qid}` for qualifiers)

### Provenance rule: cache-first QID resolution

**Every emitted QID must be traceable to a verified cache entry.** The metadata step
may ONLY emit a QID that exists in `wd_cache.json` — otherwise it emits no QID and
marks the statement `da_rivedere`.

**Commands:**

**Resolve a name to QID** (cache-first, live fallback):
```bash
python3 -m pd_checker resolve "Rafael Merry del Val"
# Prints: Q507540
```

**Note**: Cache search/list functionality is available through the `catalogs` module as library calls (no standalone CLI).

**Cache shape** (`wd_cache.json`):
```json
{
  "note": "Name -> Wikidata QID lookups",
  "fetched": "YYYY-MM-DD",
  "names": {
    "<name>": {
      "qid": "Q…" | null,
      "label_it": str,
      "label_en": str,
      "result": "ok" | "not_found",
      "checked": "YYYY-MM-DD"
    }
  }
}
```

**Resolution rules:**
- `lookup(name)` → OFFLINE, cache-only (no network, no side effects)
- `resolve(name, live=False)` → cache-first; if absent returns None WITHOUT creating an entry
- `resolve(name, live=True)` → cache-first; if absent calls Wikidata API `wbsearchentities` with proper User-Agent, stores result, returns QID or None
- **Never hand-write a QID** without a cache entry — the guard in `wikidata.py` drops any qid that is not in the cache and marks that statement `da_rivedere`.

### Extraction contract: `author_role`

The `author_role` field tells the tool how to interpret the `author` value. The extracting agent must look at what the scans actually show:

- **`photographer`**: the signature/name appears **inside the artwork** (e.g., "A. Vasari fot.", a photographer's mark on the image itself). Emit P170 (creator).
- **`illustrator`**: the name appears as an **illustrator credit** separate from the photographic creation (e.g., "dis. X" on a printed card). Emit neither P170 nor P123; mark the item for review.
- **`printer`**: the name appears as a **printing works imprint** (e.g., "Stab. X", "Tipografia Y", "Arti Grafiche Z"). Emit P123 (publisher) instead of P170; mark for review.
- **`publisher`**: the name appears as a **publisher's masthead** (e.g., "Edizioni X", publisher logo). Emit P123 (publisher) instead of P170; mark for review.
- **absent/null**: the role is unknown — use the current marker-based detection (look for "Stab.", "Tipografia", etc. in the author string). Backward compatible: no regression when the field is missing.

**Do NOT guess a role** when the visual evidence is ambiguous. Absent = current behaviour, unchanged.

### Example: P571 with circa qualifier (estimate)

```json
{
  "property": "P571",
  "label": "data di creazione",
  "value": "1955",
  "time": "+1955-00-00T00:00:00Z",
  "precision": 9,
  "qid": null,
  "source": "extracted_facts.reference_year",
  "status": "da_rivedere",
  "qualifiers": [
    {
      "property": "P1480",
      "label": "circostanze della fonte",
      "qid": "Q5727902"
    }
  ]
}
```

When `year_source` is `visual_estimate`, `folder_context`, or the source is missing/not trusted (`visible_text`/`postmark`), the statement:
- sets `status` to `da_rivedere`
- adds a qualifier P1480 = Q5727902 (circa)
- includes `time` and `precision` fields

A folder-context year is not a confirmed date: it is backfilled from the folder
period (a range uses its latest bound), so the claim sent to Wikidata carries the
same uncertainty as an estimate.

Confirmed dates (source is `visible_text` or `postmark`) have no qualifiers and `status: ok`.

Overall `status` is `public_domain` only when the work is PD in its country of
origin **and** in Italy. Foreign works are capped by the Italian rule of the
shorter term (Art. 188): they are protected in Italy no longer than at origin.

## Jurisdictions and terms (rule table `multi@2026-02`)

## Commons templates (real, verified templates)

### Licence templates

| Case | Templates | Reason |
|------|-----------|--------|
| Italian simple photo, PD, author known/irrelevant | `{{PD-Italy}}`, `{{PD-1996}}` | PD-Italy for Italian photos; PD-1996 for US pre-1989 works |
| Italian simple photo, PD, anonymous/pseudonymous | `{{PD-Italy}}`, `{{PD-anon-70-EU}}`, `{{PD-1996}}` | PD-anon-70-EU for anonymous EU works |
| Artistic photo, PD, author death >70y ago | `{{PD-old-70}}`, `{{PD-1996}}` | PD-old-70 for life+70; PD-1996 for US pre-1989 works |
| Anonymous/pseudonymous work, PD | `{{PD-anon-70-EU}}`, `{{PD-Italy}}` | EU anonymous works |
| Not in PD / uncertain | `[]` | No templates |

**Note**: `{{PD-Italy-photo}}` does NOT exist (404). Use `{{PD-Italy}}` for Italian simple photographs.

### Source template

`{{Self-scanned}}` — REQUIRED for every self-made scan. This template indicates that the uploader created the digital reproduction themselves.

**Important**: `{{Self-scanned}}` is used when the uploader scanned a work they did not create. It is NOT used when:
- The work was downloaded from elsewhere (use `{{Own scan}}` only for truly self-made scans)
- The scan was made by someone else

### Caveat templates

| Case | Template | Reason |
|------|----------|--------|
| Identifiable people in PD work | `{{Personality rights}}` | Recommended (not required) when identifiable people appear — including in old PD works. Warns about portrait/personality rights under Art. 96-97. |

### {{Information}} block (REQUIRED by Commons)

Every Commons upload requires an `{{Information}}` template with these fields:

- `description` — Italian description from visible text/subject
- `date` — Reference year or "n/d" if unknown
- `source` — `{{Self-scanned}}` for self-made scans
- `author` — Author name or "sconosciuto" if unknown
- `permission` — Empty string (no specific permission needed for PD works)

### Why {{PD-Art}} and {{PD-scan}} are NOT used

**{{PD-Art}}** is "only for use when the initial reproduction was by means of a photograph, rather than a scan". For a self-made scan of a flat 2D public-domain work, this template is inappropriate.

**{{PD-scan}}** "must not be used when you yourself personally made the scan". This template is for scans made by others, not for self-made scans.

**Correct approach**: A self-made scan of a flat 2D public-domain work needs only:
1. The source-country PD tag (e.g., `{{PD-Italy}}`, `{{PD-1996}}`)
2. `{{Self-scanned}}` as the source template
3. No {{PD-Art}} or {{PD-scan}} — explicitly do NOT use these

### {{PD-Italy}} simple-vs-artistic caveat

Italy has two different terms for photographs:
- **Simple photographs** (`foto semplice`): 70 years from production (Art. 92, L. 182/2025)
- **Artistic photographs** (`foto d'arte`): life + 70 years (Artt. 2+25)

The engine distinguishes these via `photo_classification` (`simple|artistic|unclear`). For simple photos, `{{PD-Italy}}` is correct. For artistic photos, use `{{PD-old-70}}` instead.

## Template catalog validation

The checker validates every Commons template using two complementary methods:

| Check | Command | Network | Authority | Use case |
|-------|---------|---------|-----------|----------|
| **Offline snapshot** | `validate <folder>` | No | Snapshot membership (fast, may age) | Pipeline default, CI |
| **Live API query** | `verify-live <folder>` | Yes | Authoritative (page exists/missing/redirect) | Occasional confirmation |

### Offline snapshot check (`--check`)

Validates templates against a downloaded catalog (`commons_templates.json`) from Wikimedia Commons Category:Primary license tags.

**Update the catalog** (requires network):
```bash
python3 -m pd_checker update
```
Downloads the latest template list from Commons and rewrites `commons_templates.json`.

**Check verdicts for unknown templates** (offline):  
Runs automatically during `python3 -m pd_checker run <folder>` or explicitly via:
```bash
python3 -m pd_checker validate <folder>
```
Prints one line per unknown template with the file that carries it. Exit 1 if any unknown, 0 if clean.

### Live API verification

Queries the MediaWiki API directly (`action=query&titles=Template:X&redirects=1`) to verify each template exists on Commons. This is the authoritative check: it handles redirects and detects missing templates even if they were in the snapshot.

**Verify templates live**:
```bash
python3 -m pd_checker verify-live <folder>
```
Prints one line per template: `Name -> exists` / `Name -> missing` / `Name -> redirect to Y`. Exit 1 if any template is missing, 0 otherwise.

### Test coverage

Run `python3 -m pytest pd_checker/tests/test_templates.py -q` to validate that EVERY template the engine emits is present in the catalog. This test fails if someone reintroduces a phantom template.

| Country | Case | Term | Counted from |
|---------|------|------|--------------|
| IT | Simple photograph | 70 years (Art. 92, L. 182/2025) | production |
| IT | Artistic photograph / work | life + 70 (Artt. 2+25) | author's death |
| IT | Anonymous / collective | 70 years (Artt. 26/27) | first publication |
| IT | Public body | 20 years (Art. 29) | first publication |
| IT | Letter | Art. 93 consent, **no term** | — |
| GB | Photograph (any holder) | life + 70 (CDPA s.12) | author's death |
| GB | Unknown author | 70 years (CDPA s.12(3)) | making |
| GB | Pre-1 Jun 1957 | 50 years (CDPA Sch.1) unless extended/revived → uncertain | creation |
| VA | Vatican-asset / Holy See work | 70 years (L. CXCVII/2017 Artt. 4-5) | fixation / first publication |
| VA | Anything else | Italian law applies suppletively (Art. 1) | — |

**Corporate ownership does not by itself change the term.** In both Italy and
the UK the term follows the natural author's life (life + 70) even when a
company holds the rights. It moves to "from publication" only when the *work*
is anonymous (Art. 27), a collective work (Art. 26), or a public body's (Art. 29).

## Hard rules

- **Public domain ≠ publishable.** Always surface the non-copyright blockers:
  portrait / personality rights (Artt. 96-97), correspondence consent
  (Art. 93), privacy. A corporate holder can never waive the author's moral
  rights (Art. 20).
- **Only a year read from the item itself may decide a boundary.** Printed
  text and postmark years decide; visual estimates and folder-context
  backfills are inferences — when the resulting expiry falls within the
  estimate margin (±10 years) of the current year, the verdict MUST be
  `uncertain`. A period range backfills with its latest bound.
- **No evidence, no verdict.** An item with no textual evidence and a
  low-confidence extraction must not receive a `public_domain` verdict:
  the classification and the year would both be guesses.
- The dashboard is triage + evidence, never a green light.
- Version the rule table. If a law changes, bump `RULE_TABLE_VERSION` and the
  affected jurisdiction table.

## Upload policies

The `--upload-policy` flag controls when an item is marked as *Pronto* (ready for upload):

| Policy | When an item is "Pronto" | Use case |
|--------|--------------------------|----------|
| `strict` (default) | Public domain AND no non-copyright blockers AND no missing data | Conservative approach: only items with no known issues |
| `pd-only` | Public domain with high/medium confidence (non-copyright blockers ignored) | Copyright-only assessment; human reviews other issues |
| `all` | **Every item is marked ready**, including uncertain ones | User override: proceed despite tool's caution |

### Important: `all` policy is an informed-consent override

When using `all` policy:
- **Every item gets `upload.flag = "ready"`** — even uncertain items with no licence templates
- **The reason string keeps all caveats** — never hides blockers or uncertainty
  - For items with blockers: `Politica 'all': caricamento voluto dall'utente; rimangono i blocchi: <...>`
  - For uncertain items: `Politica 'all': caricamento voluto dall'utente; verdetto incerto: <original reason>`
- **Licence templates remain unchanged** — uncertain items still get NO PD licence tags
  - The invariant: the tool states the facts; the human chooses to proceed
  - This is NOT a dark pattern: the reason strings are the user's informed-consent surface

This design ensures that when a user overrides the tool's caution, the audit trail remains honest about what the tool actually knows vs. what the human decided to do anyway.

## Verification

`python3 -m pytest pd_checker/tests -q` — the rule tables, the evidence
guards, the upload policies, the catalogue guards and the dashboard
structure are covered by tests.
