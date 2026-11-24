#!/usr/bin/env python3
"""Deterministic public-domain evaluator (multi-jurisdiction: IT, GB, VA).

Core of the pd_checker skill. This module contains both the per-country term
logic and the engine orchestration. It never calls a model, so identical facts
always produce the identical verdict.

Overall status is ``public_domain`` only when the work is public domain in its
country of origin AND in Italy; ``uncertain`` when either assessment is
uncertain. Non-copyright blockers (portrait rights, correspondence consent)
are always surfaced separately and never affect the copyright status.

Sources (verified 2026):
  IT  L. 22 apr. 1941 n. 633 — Art. 25 (life of author + 70), Art. 26
      (collective works, from first publication), Art. 27 (anonymous /
      pseudonymous, from first publication), Art. 29 (public bodies, 20 years),
      Art. 30 (periodicals, counted from year end), Art. 32-ter (Sezione III
      terms run from 1 January of the following year), Art. 92 (simple
      photographs, 70 years from production as amended by L. 182/2025 — was
      20), Art. 93 (correspondence consent, indefinite, no term).
  GB  Copyright, Designs and Patents Act 1988 — s.4/s.12(2) (photographs are
      artistic works, life+70), s.12(3)-(4) (unknown author, 70 years from
      making or first made available), Sch.1 para 12 (pre-1957 50-year rule),
      s.11(2) (a company owning the copyright does not change the term), s.163
      (Crown copyright, 125 years).
  VA  Law CXCVII of 1 September 2017; where it is silent, Italian law applies
      suppletively (Art. 1). Art. 4 (photographs of Vatican cultural assets:
      70 years from fixation), Art. 5 (works under the Holy See / SCV name:
      70 years from first publication, or the author's death if named).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import context as country_suggest

# ============================================================================
# SHARED CONSTANTS AND HELPERS (from jurisdictions.py)
# ============================================================================

PUBLIC_DOMAIN = "public_domain"
NOT_PUBLIC_DOMAIN = "not_public_domain"
UNCERTAIN = "uncertain"

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}

_ART_25 = "Art. 25 - L. 633/1941 (vita dell'autore + 70 anni)"
_ART_26 = "Art. 26 - L. 633/1941 (opere collettive: 70 anni dalla prima pubblicazione)"
_ART_27 = "Art. 27 - L. 633/1941 (opere anonime/pseudonime: 70 anni dalla prima pubblicazione)"
_ART_30 = "Art. 30 - L. 633/1941 (opere periodiche: decorrenza dalla fine dell'anno)"
_ART_92 = (
    "Art. 92 - L. 633/1941, come modificato dalla L. 2 dicembre 2025 n. 182, art. 47 "
    "(fotografie semplici: 70 anni dalla produzione)"
)
_ART_93 = "Art. 93 - L. 633/1941 (corrispondenza: diritto di consenso a durata indeterminata)"
_CDPA_12 = "CDPA 1988 s.12 (fotografie: vita dell'autore + 70 anni)"
_CDPA_12_3 = "CDPA 1988 s.12(3)-(4) (autore ignoto: 70 anni dalla realizzazione)"
_CDPA_SCH1 = "CDPA 1988 Sch.1 para 12 (fotografie anteriori al 1 giugno 1957: regola dei 50 anni)"
_CDPA_163 = "CDPA 1988 s.163 (Crown copyright: 125 anni)"
_VA_4 = "Legge VAT CXCVII/2017, Art. 4 (fotografie di beni culturali vaticani: 70 anni)"
_VA_5 = "Legge VAT CXCVII/2017, Art. 5 (opere a nome della Santa Sede / SCV: 70 anni)"
_VA_1 = "Legge VAT CXCVII/2017, Art. 1 (rinvio suppletivo alla legge italiana)"

_SIMPLE_PHOTO_REFORM_YEAR = 2025  # L. 182/2025 in force 18 Dec 2025

# Estimate margin: when a visual_estimate year puts expiry within this many years of
# current_year, the verdict must be uncertain rather than public_domain.
_ESTIMATE_MARGIN_YEARS = 10

# No-evidence guard: when both text fields are empty AND vlm_confidence is low,
# a visual_estimate year is a pure guess — a public_domain verdict must become uncertain.
_NO_EVIDENCE_VLM_CONFIDENCE_THRESHOLD = "low"

# Printing-work markers: when author or visible_text contains one of these strings,
# it is a printing house/publisher, not a natural-person author.
_PRINTING_WORK_MARKERS = [
    "Stab.",  # Stabilimento (printing works)
    "Tipografia",
    "Arti Grafiche",
    "Litografia",
    "Editore",
]


def _check_estimate_margin(ref_year, year_source, current_year, pd_year, legal_basis):
    """If the year was not read from the item itself and expiry is within margin
    of current_year, return uncertain.

    Only a year read from the item (printed text "visible_text", postmark) may
    decide a boundary. A visual estimate or a folder-context backfill is an
    inference: when pd_year is within _ESTIMATE_MARGIN_YEARS of current_year on
    EITHER side, the status must be uncertain.
    Returns None if the check passes (no margin issue), or an uncertain result dict.
    """
    if year_source not in ("visual_estimate", "folder_context"):
        return None
    years_until_pd = pd_year - current_year
    if abs(years_until_pd) <= _ESTIMATE_MARGIN_YEARS:
        origin = ("stimato visivamente" if year_source == "visual_estimate"
                  else "derivato dal contesto della cartella")
        note = (f"L'anno è una stima visiva; con un margine di {_ESTIMATE_MARGIN_YEARS} anni, non si può decidere."
                if year_source == "visual_estimate"
                else f"L'anno viene dal contesto della cartella, non dalla cartolina stessa; con un margine di {_ESTIMATE_MARGIN_YEARS} anni, non si può decidere.")
        return _result(
            reason=f"Anno di produzione {origin} ({ref_year}): la scadenza (circa {pd_year}) è troppo vicina per un verdetto certo.",
            legal_basis=legal_basis,
            notes=[note],
            confidence="low",
        )
    return None


def _check_no_evidence(facts: dict, ref_year, year_source: str, current_year: int, pd_year: int, legal_basis: list) -> dict | None:
    """If there is no textual evidence (both text fields empty) AND vlm_confidence is low,
    a visual_estimate year is a pure guess — return uncertain instead of public_domain.
    
    This is a second guard beyond _ESTIMATE_MARGIN_YEARS: even when the year is far
    outside the margin, a verdict based on visual guesswork alone is not a verdict.
    """
    if year_source != "visual_estimate":
        return None
    
    visible_text = facts.get("visible_text") or ""
    visible_text_translation = facts.get("visible_text_translation") or ""
    vlm_confidence = facts.get("vlm_confidence") or "low"
    
    # No textual evidence AND low confidence = pure visual guess
    if not visible_text.strip() and not visible_text_translation.strip() and vlm_confidence == "low":
        return _result(
            reason=f"Nessuna evidenza testuale (entrambi i campi testo vuoti) e confidenza visiva bassa: l'anno {ref_year} è una stima visiva pura; il verdetto non può essere certo.",
            legal_basis=legal_basis,
            notes=[
                "La classificazione e l'anno riposano su una stima visiva senza supporto testuale.",
                "Un verdetto di pubblico dominio richiederebbe evidenza concreta (testo visibile, postmark, documento)."
            ],
            confidence="low",
        )
    return None


def _pd_year(event_year: int, term: int, *, year_end: bool = True) -> int:
    """First calendar year in which protection is over.

    ``year_end`` models Art. 32-ter / Art. 30 (Sezione III) and the equivalent
    UK year-end computation: protection runs to the end of the Nth year after
    the event, so public domain starts the following year.
    """
    return int(event_year) + int(term) + (1 if year_end else 0)


def _worst(*confs: str) -> str:
    return min(confs, key=lambda c: _CONF_RANK.get(c, 0))


def _result(**fields) -> dict:
    result = {
        "status": UNCERTAIN,
        "pd_year": None,
        "years_until_pd": None,
        "reason": "",
        "legal_basis": [],
        "commons_templates": [],
        "notes": [],
        "missing_data": [],
        "confidence": "low",
    }
    result.update(fields)
    return result


def _settle(result: dict, current_year: int, *, pd_reason: str, npd_reason: str,
            templates=(), confidence: str = "high", notes=()) -> dict:
    pd_year = result["pd_year"]
    if current_year >= pd_year:
        result["status"] = PUBLIC_DOMAIN
        result["reason"] = pd_reason
        result["commons_templates"] = list(templates)
    else:
        result["status"] = NOT_PUBLIC_DOMAIN
        result["years_until_pd"] = pd_year - current_year
        result["reason"] = npd_reason
    result["confidence"] = confidence
    result["notes"] = list(result.get("notes") or []) + list(notes)
    return result


# ============================================================================
# PER-JURISDICTION ASSESSORS (from jurisdictions.py)
# ============================================================================

# --------------------------------------------------------------------------- IT

def _it(facts: dict, current_year: int) -> dict:
    doc = (facts.get("document_type") or "unknown").lower()
    if doc in ("photo", "postcard"):
        return _it_photo(facts, current_year)
    if doc == "letter":
        return _it_letter(facts, current_year)
    if doc in ("newspaper", "magazine"):
        return _it_periodical(facts, current_year)
    if doc == "manuscript":
        return _it_text(facts, current_year)
    return _result(
        reason="Tipo di documento non riconosciuto.",
        missing_data=["Determinare il tipo di documento."],
    )


def _it_photo(facts: dict, current_year: int) -> dict:
    vlm = facts.get("vlm_confidence") or "low"
    photo_class = (facts.get("photo_classification") or "unclear").lower()
    ref_year = facts.get("reference_year")
    year_source = facts.get("year_source")
    author = facts.get("author")
    author_role = facts.get("author_role")
    death = facts.get("author_death_year")
    is_anon = bool(facts.get("is_anonymous"))
    visible_text = facts.get("visible_text") or facts.get("visible_text_translation") or ""
    holder = (facts.get("rights_holder") or "").lower()
    
    # Determine if author is a printing works/publisher:
    # - Explicit author_role "printer" or "publisher" takes precedence
    # - Otherwise, fall back to marker detection for backward compatibility
    author_is_printing_work = False
    if author_role in ("printer", "publisher"):
        author_is_printing_work = True
    elif author and author_role not in ("photographer", "illustrator"):
        # No role or unknown role: use marker detection (current behaviour)
        for marker in _PRINTING_WORK_MARKERS:
            if marker in author:
                author_is_printing_work = True
                break
    
    # Detect contradiction: is_anonymous=True but visible_text contains a publisher imprint
    anonymous_contradiction = False
    if is_anon and visible_text:
        for marker in _PRINTING_WORK_MARKERS:
            if marker in visible_text:
                anonymous_contradiction = True
                break

    if photo_class == "simple":
        if ref_year is None:
            return _result(
                reason="Fotografia semplice, ma manca la data di produzione.",
                legal_basis=[_ART_92],
                missing_data=["Data di produzione della fotografia."],
                confidence=_worst("low", vlm),
            )
        pd_year = _pd_year(ref_year, 70, year_end=False)
        is_estimate = year_source == "visual_estimate"
        
        # Check estimate margin BEFORE any public_domain return
        margin_check = _check_estimate_margin(ref_year, year_source, current_year, pd_year, [_ART_92])
        if margin_check is not None:
            return margin_check
        
        # Check no-evidence guard: pure visual guess with no textual evidence
        no_evidence_check = _check_no_evidence(facts, ref_year, year_source, current_year, pd_year, [_ART_92])
        if no_evidence_check is not None:
            return no_evidence_check
        
        result = _result(pd_year=pd_year, legal_basis=[_ART_92],
                         notes=["# Commons: PD-Italy e' il nome corretto per le foto semplici italiane; "
                                "non usare {{PD-old-70}} su una foto anonima."])
        
        # Add warnings for anonymous contradiction and printing works
        if anonymous_contradiction:
            result["notes"].append(
                "ATTENZIONE: is_anonymous=True ma il testo visibile contiene un'impronta editoriale; "
                "il verdetto potrebbe essere errato."
            )
        if author_is_printing_work:
            result["notes"].append(
                f"ATTENZIONE: il campo 'author' ('{author}') sembra essere uno stabilimento di stampa, non una persona; "
                "non usare questo valore per Art. 25."
            )
        if current_year >= pd_year:
            pd_reason_base = f"Fotografia semplice del {ref_year}" if not is_estimate else f"Fotografia semplice del {ref_year} (anno stimato)"
            return _settle(result, current_year,
                           pd_reason=f"{pd_reason_base}: diritto connesso "
                                     f"(Art. 92, 70 anni) scaduto nel {pd_year}.",
                            npd_reason="", templates=["{{PD-Italy}}", "{{PD-1996}}"], confidence=_worst("high", vlm))
        if ref_year + 20 < _SIMPLE_PHOTO_REFORM_YEAR:
            # New term not yet expired, but the 20-year term was already over when
            # L. 182/2025 took effect (18 Dec 2025). Dominant reading: no revival.
            year_desc = f"del {ref_year} (anno stimato)" if is_estimate else f"del {ref_year}"
            result["status"] = PUBLIC_DOMAIN
            result["reason"] = (
                f"Fotografia semplice {year_desc}: il termine dei 20 anni era gia' scaduto prima "
                "della riforma (18 dicembre 2025). Lettura dominante: i diritti estinti non si "
                "riattivano -> in pubblico dominio."
            )
            result["notes"].append(
                "La L. 182/2025 non ha norme transitorie: la non-riattivazione e' un'interpretazione, "
                "non una regola scritta (confidenza limitata)."
            )
            result["commons_templates"] = ["{{PD-Italy}}", "{{PD-1996}}"]
            result["confidence"] = _worst("medium", vlm)
            return result
        return _settle(result, current_year, pd_reason="",
                       npd_reason=f"Fotografia semplice del {ref_year}{' (anno stimato)' if is_estimate else ''}; scadenza 70 anni nel {pd_year}.",
                       confidence=_worst("high", vlm))

    if photo_class == "artistic":
        # Determine if author is a printing works/publisher (same logic as simple photo)
        author_is_printing_work = False
        if author_role in ("printer", "publisher"):
            author_is_printing_work = True
        elif author and author_role not in ("photographer", "illustrator"):
            # No role or unknown role: use marker detection (current behaviour)
            for marker in _PRINTING_WORK_MARKERS:
                if marker in author:
                    author_is_printing_work = True
                    break
        
        # Detect contradiction: is_anonymous=True but visible_text contains a publisher imprint
        anonymous_contradiction = False
        if is_anon and visible_text:
            for marker in _PRINTING_WORK_MARKERS:
                if marker in visible_text:
                    anonymous_contradiction = True
                    break
        
        # Add anonymous contradiction warning for artistic photos too
        notes_list = []
        if anonymous_contradiction:
            notes_list.append(
                "ATTENZIONE: is_anonymous=True ma il testo visibile contiene un'impronta editoriale; "
                "il verdetto potrebbe essere errato."
            )
        if author_is_printing_work:
            notes_list.append(
                f"ATTENZIONE: il campo 'author' ('{author}') sembra essere uno stabilimento di stampa, non una persona; "
                "non usare questo valore per Art. 25."
            )
        
        if is_anon or holder in ("company", "publisher", "public_body"):
            if ref_year is None:
                return _result(reason="Opera fotografica anonima/collettiva senza data di pubblicazione.",
                               legal_basis=[_ART_27],
                               missing_data=["Anno di pubblicazione."], confidence=_worst("low", vlm))
            pd_year = _pd_year(ref_year, 70)
            is_estimate = year_source == "visual_estimate"
            
            # Check estimate margin before returning public_domain
            margin_check = _check_estimate_margin(ref_year, year_source, current_year, pd_year, [_ART_27])
            if margin_check is not None:
                return margin_check
            
            # Check no-evidence guard: pure visual guess with no textual evidence
            no_evidence_check = _check_no_evidence(facts, ref_year, year_source, current_year, pd_year, [_ART_27])
            if no_evidence_check is not None:
                return no_evidence_check
            
            result = _result(pd_year=pd_year, legal_basis=[_ART_27], notes=notes_list)
            return _settle(result, current_year,
                           pd_reason=f"Opera fotografica anonima/collettiva pubblicata nel {ref_year}: "
                                     f"70 anni (Art. 27) scaduti nel {pd_year}.",
                           npd_reason=f"Opera anonima/collettiva del {ref_year}; scadenza nel {pd_year}.",
                            # Anonymous artistic photo: PD-anon-70-EU (not PD-Italy which is simple-photo tag) + US rationale
                            templates=["{{PD-anon-70-EU}}", "{{PD-1996}}"], confidence=_worst("medium", vlm))
        if author and death and not author_is_printing_work:
            pd_year = _pd_year(death, 70)
            is_estimate = year_source == "visual_estimate"
            
            # Check estimate margin before returning public_domain
            margin_check = _check_estimate_margin(death, year_source, current_year, pd_year, [_ART_25])
            if margin_check is not None:
                return margin_check
            
            result = _result(pd_year=pd_year, legal_basis=[_ART_25], notes=notes_list)
            return _settle(result, current_year,
                           pd_reason=f"Opera fotografica di {author} (morto nel {death}): vita+70 "
                                     f"scaduto nel {pd_year}.",
                           npd_reason=f"Opera di {author} (morto nel {death}); scadenza nel {pd_year}.",
                            # Author death >70y: PD-old-70 + PD-1996 (US tag for pre-1989 works)
                            templates=["{{PD-old-70}}", "{{PD-1996}}"], confidence=_worst("high", vlm))
        if author_is_printing_work:
            # Author looks like a printing works, not a person - fall through to Art. 27 (anonymous/collective)
            if ref_year is None:
                return _result(reason="Opera fotografica con autore che sembra uno stabilimento di stampa, senza data di pubblicazione.",
                               legal_basis=[_ART_27],
                               missing_data=["Anno di pubblicazione."],
                               notes=[f"Il campo 'author' ('{author}') sembra essere uno stabilimento di stampa, non una persona."],
                               confidence=_worst("low", vlm))
            pd_year = _pd_year(ref_year, 70)
            is_estimate = year_source == "visual_estimate"
            
            # Check estimate margin before returning public_domain
            margin_check = _check_estimate_margin(ref_year, year_source, current_year, pd_year, [_ART_27])
            if margin_check is not None:
                return margin_check
            
            # Check no-evidence guard: pure visual guess with no textual evidence
            no_evidence_check = _check_no_evidence(facts, ref_year, year_source, current_year, pd_year, [_ART_27])
            if no_evidence_check is not None:
                return no_evidence_check
            
            result = _result(pd_year=pd_year, legal_basis=[_ART_27],
                             notes=notes_list + [f"Il campo 'author' ('{author}') sembra essere uno stabilimento di stampa; si usa Art. 27 (opera collettiva)."])
            return _settle(result, current_year,
                           pd_reason=f"Opera fotografica collettiva pubblicata nel {ref_year}: "
                                     f"70 anni (Art. 27) scaduti nel {pd_year}.",
                           npd_reason=f"Opera collettiva del {ref_year}; scadenza nel {pd_year}.",
                            templates=["{{PD-anon-70-EU}}", "{{PD-1996}}"], confidence=_worst("medium", vlm))
        return _result(
            reason="Opera fotografica con autore noto ma senza data di morte: impossibile calcolare vita+70.",
            legal_basis=[_ART_25],
            missing_data=[f"Data di morte di '{author}'." if author else "Identificare l'autore."],
            confidence=_worst("low", vlm),
        )

    return _result(
        reason="Fotografia 'unclear': semplice (Art. 92) o artistica (Artt. 2+25)?",
        legal_basis=[_ART_92, _ART_25],
        missing_data=["Confermare la classificazione semplice/artistica."],
        confidence=_worst("low", vlm),
    )


def _it_letter(facts: dict, current_year: int) -> dict:
    vlm = facts.get("vlm_confidence") or "low"
    author = facts.get("author")
    death = facts.get("author_death_year")
    result = _result(
        status=UNCERTAIN,
        legal_basis=[_ART_25, _ART_93],
        missing_data=["Identificare mittente e destinatario.",
                      "Verificare privacy e consenso degli eredi."],
        confidence=_worst("medium" if (author and death) else "low", vlm),
    )
    if author and death:
        result["pd_year"] = _pd_year(death, 70)
        state = "scaduto" if current_year >= result["pd_year"] else f"scade nel {result['pd_year']}"
        result["reason"] = (f"Lettera di {author} (morto nel {death}); copyright {state}. "
                            "MA resta il diritto di consenso dell'Art. 93, senza scadenza fissa.")
    else:
        result["reason"] = ("Lettera privata: sempre 'uncertain' per il diritto di consenso "
                            "dell'Art. 93, che non ha scadenza fissa.")
    return result


def _it_periodical(facts: dict, current_year: int) -> dict:
    vlm = facts.get("vlm_confidence") or "low"
    ref_year = facts.get("reference_year")
    result = _result(legal_basis=[_ART_26, _ART_30],
                     notes=["Il numero e' PD come opera collettiva, ma i singoli articoli firmati "
                            "da autori morti da meno di 70 anni restano protetti (Art. 25)."])
    if ref_year is None:
        result["reason"] = "Pubblicazione periodica senza anno."
        result["missing_data"] = ["Anno del numero o fascicolo."]
        result["confidence"] = _worst("low", vlm)
        return result
    result["pd_year"] = _pd_year(ref_year, 70)
    return _settle(result, current_year,
                   pd_reason=f"Numero del {ref_year}: opera collettiva periodica, 70 anni "
                             f"(Artt. 26/30) scaduti nel {result['pd_year']}.",
                   npd_reason=f"Numero del {ref_year}; scadenza nel {result['pd_year']}.",
                    # Anonymous periodical: PD-anon-70-EU + US rationale (not PD-Italy)
                    templates=["{{PD-anon-70-EU}}", "{{PD-1996}}"], confidence=_worst("medium", vlm))


def _it_text(facts: dict, current_year: int) -> dict:
    vlm = facts.get("vlm_confidence") or "low"
    author = facts.get("author")
    death = facts.get("author_death_year")
    ref_year = facts.get("reference_year")
    is_anon = bool(facts.get("is_anonymous"))
    if is_anon:
        if ref_year is None:
            return _result(reason="Manoscritto anonimo senza anno di pubblicazione.",
                           legal_basis=[_ART_27], missing_data=["Anno di pubblicazione."],
                           confidence=_worst("low", vlm))
        result = _result(pd_year=_pd_year(ref_year, 70), legal_basis=[_ART_27])
        return _settle(result, current_year,
                       pd_reason=f"Manoscritto anonimo del {ref_year}: 70 anni scaduti nel {result['pd_year']}.",
                       npd_reason=f"Manoscritto anonimo del {ref_year}; scadenza nel {result['pd_year']}.",
                        # Anonymous manuscript: PD-anon-70-EU + US rationale (not PD-Italy)
                        templates=["{{PD-anon-70-EU}}", "{{PD-1996}}"], confidence=_worst("medium", vlm))
    if author and death:
        result = _result(pd_year=_pd_year(death, 70), legal_basis=[_ART_25])
        return _settle(result, current_year,
                       pd_reason=f"Manoscritto di {author} (morto nel {death}): vita+70 scaduto nel {result['pd_year']}.",
                       npd_reason=f"Manoscritto di {author} (morto nel {death}); scadenza nel {result['pd_year']}.",
                        # Author death >70y: PD-old-70 + PD-1996 (US tag for pre-1989 works)
                        templates=["{{PD-old-70}}", "{{PD-1996}}"], confidence=_worst("high", vlm))
    return _result(reason="Manoscritto senza autore identificato ne' data.",
                   legal_basis=[_ART_25, _ART_27],
                   missing_data=["Identificare l'autore (e la data di morte) o l'anno di pubblicazione."],
                   confidence=_worst("low", vlm))


# --------------------------------------------------------------------------- GB

def _gb(facts: dict, current_year: int) -> dict:
    doc = (facts.get("document_type") or "unknown").lower()
    vlm = facts.get("vlm_confidence") or "low"
    ref_year = facts.get("reference_year")
    author = facts.get("author")
    death = facts.get("author_death_year")
    is_anon = bool(facts.get("is_anonymous"))
    holder = (facts.get("rights_holder") or "").lower()
    notes = []
    if holder == "company":
        notes.append("Titolare e' una societa': secondo il CDPA s.11(2) il termine resta "
                     "quello dell'autore persona fisica (vita+70).")

    if doc in ("photo", "postcard", "manuscript", "letter") or doc == "unknown":
        if death and (author or not is_anon):
            result = _result(pd_year=_pd_year(death, 70), legal_basis=[_CDPA_12], notes=notes)
            return _settle(result, current_year,
                           pd_reason=f"Opera britannica di {author or 'autore'} (morto nel {death}): "
                                     f"vita+70 scaduto nel {result['pd_year']}.",
                           npd_reason=f"Opera britannica di {author or 'autore'} (morto nel {death}); "
                                      f"scadenza nel {result['pd_year']}.",
                           templates=["{{PD-old-70}}"], confidence=_worst("high", vlm))
        if ref_year is not None and (is_anon or holder in ("company", "public_body")):
            if ref_year < 1957:
                # UK CDPA 1988 s.12(3) (not s.12(2), which is life+70): UNKNOWN authorship
                # is PD 70 years from end of year of creation; pre-1957 photographs' 50-year
                # term (1911 Act s.21, preserved by Sch.1 para 12(2)(c)) expired 31 Dec 1960.
                # Therefore a UK photo taken in 1910 with unknown author IS PD in 2026.
                pd_year = _pd_year(ref_year, 50)
                result = _result(pd_year=pd_year, legal_basis=[_CDPA_SCH1], notes=notes)
                return _settle(result, current_year,
                               pd_reason=f"Foto britannica anonima del {ref_year}: regola dei 50 anni (CDPA Sch.1) "
                                         f"scaduta nel {pd_year}.",
                               npd_reason=f"Foto anonima del {ref_year}; scadenza nel {pd_year}.",
                               templates=["{{PD-anon-70}}", "{{PD-1996}}"], confidence=_worst("high", vlm))
            result = _result(pd_year=_pd_year(ref_year, 70), legal_basis=[_CDPA_12_3], notes=notes)
            return _settle(result, current_year,
                           pd_reason=f"Opera britannica con autore ignoto, realizzata nel {ref_year}: "
                                     f"70 anni scaduti nel {result['pd_year']}.",
                           npd_reason=f"Autore ignoto, realizzata nel {ref_year}; scadenza nel {result['pd_year']}.",
                           templates=["{{PD-anon-70}}"], confidence=_worst("medium", vlm))
        return _result(reason="Opera britannica senza autore (data di morte) ne' data di realizzazione.",
                       legal_basis=[_CDPA_12],
                       missing_data=["Data di morte dell'autore o anno di realizzazione."],
                       confidence=_worst("low", vlm))

    if doc in ("newspaper", "magazine"):
        if ref_year is None:
            return _result(reason="Periodico britannico senza anno.", legal_basis=[_CDPA_12_3],
                           missing_data=["Anno di pubblicazione."], confidence=_worst("low", vlm))
        result = _result(pd_year=_pd_year(ref_year, 70), legal_basis=[_CDPA_12_3])
        return _settle(result, current_year,
                       pd_reason=f"Periodico britannico del {ref_year}: 70 anni scaduti nel {result['pd_year']}.",
                       npd_reason=f"Periodico britannico del {ref_year}; scadenza nel {result['pd_year']}.",
                       templates=["{{PD-anon-70}}"], confidence=_worst("medium", vlm))

    return _result(reason="Tipo di documento non gestito per il Regno Unito.", legal_basis=[_CDPA_12])


# --------------------------------------------------------------------------- VA

def _va(facts: dict, current_year: int) -> dict:
    vlm = facts.get("vlm_confidence") or "low"
    ref_year = facts.get("reference_year")
    death = facts.get("author_death_year")
    holder = (facts.get("rights_holder") or "").lower()
    is_anon = bool(facts.get("is_anonymous"))

    # Vatican has no country-specific Commons tag; use generic fallbacks
    _VA_GENERIC_TEMPLATES_ANON = ["{{PD-anon-70-EU}}", "{{PD-1996}}"]
    _VA_GENERIC_TEMPLATES_OLD = ["{{PD-old-70}}", "{{PD-1996}}"]

    if holder == "holy_see" or facts.get("is_vatican_asset"):
        basis = _VA_5 if holder == "holy_see" else _VA_4
        # VA known author death: use PD-old-70 when author is known and deceased
        # (not just when rights_holder == 'holy_see')
        if death and not is_anon:
            result = _result(pd_year=_pd_year(death, 70), legal_basis=[basis])
            return _settle(result, current_year,
                           pd_reason=f"Opera vaticana (Santa Sede), autore morto nel {death}: scaduta nel {result['pd_year']}.",
                           npd_reason=f"Opera vaticana (Santa Sede), autore morto nel {death}; scadenza nel {result['pd_year']}.",
                           # VA has no country-specific tag; use generic old-70 + US rationale
                           templates=_VA_GENERIC_TEMPLATES_OLD,
                           notes=["La Santa Sede non ha un tag Commons dedicato: si usa il generico PD-old-70."],
                           confidence=_worst("medium", vlm))
        if ref_year is None:
            return _result(reason="Opera vaticana senza anno di pubblicazione/realizzazione.",
                           legal_basis=[basis], missing_data=["Anno di pubblicazione o fissazione."],
                           confidence=_worst("low", vlm))
        result = _result(pd_year=_pd_year(ref_year, 70), legal_basis=[basis])
        return _settle(result, current_year,
                       pd_reason=f"Opera vaticana pubblicata nel {ref_year}: 70 anni scaduti nel {result['pd_year']}.",
                       npd_reason=f"Opera vaticana pubblicata nel {ref_year}; scadenza nel {result['pd_year']}.",
                       # VA has no country-specific Commons tag; use generic EU anonymous + US rationale
                       templates=_VA_GENERIC_TEMPLATES_ANON,
                       notes=["La Santa Sede non ha un tag Commons dedicato: si usa il generico PD-anon-70-EU."],
                       confidence=_worst("medium", vlm))

    # Non-Holy See Vatican cases: fall back to Italian law but use VA generic templates
    result = _it(facts, current_year)
    result["legal_basis"] = [_VA_1] + result.get("legal_basis", [])
    result["notes"] = result.get("notes", []) + [
        "La legge vaticana non disciplina espressamente questo caso: si applica in via "
        "suppletiva la legge italiana (Art. 1, Legge CXCVII/2017)."
    ]
    # Replace Italian templates with VA generic ones (no country-specific tag for VA)
    if result.get("commons_templates"):
        if is_anon:
            result["commons_templates"] = _VA_GENERIC_TEMPLATES_ANON
        elif death:
            result["commons_templates"] = _VA_GENERIC_TEMPLATES_OLD
    return result


# --------------------------------------------------------------------------- entry

_ASSESSORS = {"IT": _it, "GB": _gb, "VA": _va}


def assess(country: str, facts: dict, current_year: int) -> dict:
    assessor = _ASSESSORS.get((country or "IT").upper())
    if assessor is None:
        return _result(reason=f"Giurisdizione '{country}' non supportata.",
                       missing_data=["Giurisdizione non coperta."])
    return assessor(facts, current_year)


# ============================================================================
# ENGINE ORCHESTRATION (from engine.py)
# ============================================================================

RULE_TABLE_VERSION = "multi@2026-02 [IT L.633/1941+L.182/2025 | GB CDPA 1988 | VA L.CXCVII/2017]"

# Upload flag constants
UPLOAD_READY = "ready"
UPLOAD_REVIEW = "review"
UPLOAD_NO = "no"

# Upload policy constants
UPLOAD_POLICY_STRICT = "strict"
UPLOAD_POLICY_PD_ONLY = "pd-only"
UPLOAD_POLICY_ALL = "all"

_ART_96 = "Artt. 96-97 - L. 633/1941 (diritto all'immagine della persona ritratta)"
_ART_188 = "Art. 188 - L. 633/1941 (rule of the shorter term, per le opere straniere)"

_FACTS_FIELDS = (
    "document_type", "country_of_origin", "rights_holder", "author", "author_nationality",
    "author_death_year", "is_anonymous", "author_role", "photo_classification", "reference_year",
    "year_source", "visible_text", "visible_text_translation", "visible_text_lang",
    "translation_lang", "has_identifiable_people", "vlm_confidence", "set_id", "part", "series_id",
    "date_evidence", "images", "postmark", "publisher", "is_vatican_asset",
)


def _set_upload_flag(verdict: dict, policy: str = UPLOAD_POLICY_STRICT) -> None:
    """Set the upload flag and reason (advisory evidence for manual step).
    
    Criteria (transparent, ordered):
    - flag = "no" when status == "not_public_domain".
    - strict policy: flag = "ready" when status == "public_domain" AND confidence in ("high","medium") 
      AND blockers_non_copyright is empty AND missing_data is empty.
    - pd-only policy: flag = "ready" when status == "public_domain" AND confidence in ("high","medium")
      (non-copyright blockers are reported but do not prevent ready).
    - all policy: flag = "ready" for every item; reason keeps caveats for informed consent.
    - otherwise flag = "review".
    """
    status = verdict.get("status")
    confidence = verdict.get("confidence", "low")
    blockers = verdict.get("blockers_non_copyright", [])
    missing = verdict.get("missing_data", [])
    
    if status == NOT_PUBLIC_DOMAIN:
        verdict["upload"] = {
            "flag": UPLOAD_NO,
            "reason": "Non in pubblico dominio."
        }
    elif status == PUBLIC_DOMAIN and confidence in ("high", "medium"):
        # Check blockers based on policy
        has_non_copyright_blockers = bool(blockers)
        has_missing_data = bool(missing)
        
        if policy == UPLOAD_POLICY_ALL:
            # all: every item is ready, but reason keeps caveats for informed consent
            if not has_non_copyright_blockers and not has_missing_data:
                verdict["upload"] = {
                    "flag": UPLOAD_READY,
                    "reason": "In pubblico dominio, nessun vincolo: candidato per l'import manuale."
                }
            else:
                blocker_parts = []
                if has_non_copyright_blockers:
                    blocker_parts.append("non-copyright: " + ", ".join(blockers))
                if has_missing_data:
                    blocker_parts.append("dati mancanti: " + ", ".join(missing))
                verdict["upload"] = {
                    "flag": UPLOAD_READY,
                    "reason": f"Politica 'all': caricamento voluto dall'utente; rimangono i blocchi: {'; '.join(blocker_parts)}."
                }
        elif policy == UPLOAD_POLICY_STRICT:
            # Strict: both non-copyright blockers and missing data prevent ready
            if not has_non_copyright_blockers and not has_missing_data:
                verdict["upload"] = {
                    "flag": UPLOAD_READY,
                    "reason": "In pubblico dominio, nessun vincolo: candidato per l'import manuale."
                }
            else:
                verdict["upload"] = {
                    "flag": UPLOAD_REVIEW,
                    "reason": "In pubblico dominio ma con vincoli o confidenza bassa: rivedere prima."
                }
        else:  # pd-only
            # pd-only: only missing data prevents ready; non-copyright blockers are reported but ignored
            if not has_missing_data:
                verdict["upload"] = {
                    "flag": UPLOAD_READY,
                    "reason": "In pubblico dominio (criterio solo copyright): candidato per l'import manuale."
                }
            else:
                verdict["upload"] = {
                    "flag": UPLOAD_REVIEW,
                    "reason": "In pubblico dominio ma con dati mancanti: rivedere prima."
                }
    else:
        # uncertain or low confidence
        if policy == UPLOAD_POLICY_ALL:
            # all: uncertain/low-confidence items are ready, but reason keeps the original caveat
            if status == UNCERTAIN:
                original_reason = verdict.get("reason", "verdetto incerto")
                verdict["upload"] = {
                    "flag": UPLOAD_READY,
                    "reason": f"Politica 'all': caricamento voluto dall'utente; verdetto incerto: {original_reason}",
                }
            else:  # public_domain with low confidence
                verdict["upload"] = {
                    "flag": UPLOAD_READY,
                    "reason": "Politica 'all': caricamento voluto dall'utente; confidenza bassa.",
                }
        elif policy == UPLOAD_POLICY_STRICT:
            verdict["upload"] = {
                "flag": UPLOAD_REVIEW,
                "reason": "In pubblico dominio ma con vincoli o confidenza bassa: rivedere prima.",
            }
        else:  # pd-only
            verdict["upload"] = {
                "flag": UPLOAD_REVIEW,
                "reason": "In pubblico dominio ma con confidenza bassa: rivedere prima.",
            }


def _base(item: dict) -> dict:
    return {
        "file": item.get("file"),
        "status": UNCERTAIN,
        "confidence": "low",
        "reason": "",
        "legal_basis": [],
        "commons_templates": [],
        "commons_categories": [],
        "missing_data": [],
        "pd_year": None,
        "years_until_pd": None,
        "human_review_required": True,
        "blockers_non_copyright": [],
        "notes": [],
        "rule_table_version": RULE_TABLE_VERSION,
        "country_of_origin": (item.get("country_of_origin") or "IT").upper(),
        "shorter_term_applied": False,
        "jurisdictions": {},
        "extracted_facts": {field: item.get(field) for field in _FACTS_FIELDS},
        "upload": {"flag": UPLOAD_REVIEW, "reason": "Da determinare."},
        # Commons upload metadata (required by Commons for every file)
        "source_template": "{{Self-scanned}}",
        "caveat_templates": [],
    }


def _non_copyright_blockers(verdict: dict, item: dict) -> None:
    doc_type = (item.get("document_type") or "unknown").lower()
    has_identifiable_people = item.get("has_identifiable_people") is not False
    
    if doc_type in ("photo", "postcard") and has_identifiable_people:
        verdict["blockers_non_copyright"].append(
            f"{_ART_96}: se la foto ritrae persone riconoscibili serve il consenso della "
            "persona (o degli eredi), anche se l'opera e' in pubblico dominio."
        )
        # Add personality rights caveat template when identifiable people are present
        verdict["caveat_templates"] = ["{{Personality rights}}"]
    elif doc_type in ("photo", "postcard"):
        # No identifiable people detected - empty caveat list
        verdict["caveat_templates"] = []
    
    if doc_type == "letter":
        verdict["blockers_non_copyright"].append(
            "Lettera privata: oltre al copyright vale il diritto di consenso dell'Art. 93 "
            "(o l'equivalente straniero) e la privacy degli eredi."
        )


def _cap_italy_term(italy: dict, origin: dict, origin_country: str, current_year: int) -> None:
    """Art. 188: a foreign work is protected in Italy no longer than at origin."""
    if origin.get("pd_year") and italy.get("pd_year"):
        capped = min(origin["pd_year"], italy["pd_year"])
        if capped != italy["pd_year"]:
            italy["pd_year"] = capped
            if current_year >= capped:
                italy["status"] = PUBLIC_DOMAIN
                italy["years_until_pd"] = None
            else:
                italy["status"] = NOT_PUBLIC_DOMAIN
                italy["years_until_pd"] = capped - current_year
            italy["notes"] = italy.get("notes", []) + [
                f"{_ART_188}: termine italiano limitato a quello del Paese d'origine ({origin_country})."
            ]


def evaluate(item: dict, current_year: int, upload_policy: str = UPLOAD_POLICY_STRICT) -> dict:
    verdict = _base(item)
    origin_country = verdict["country_of_origin"]
    verdict["upload_policy"] = upload_policy

    # Generate country candidates when country_of_origin is not declared
    declared_country = item.get("country_of_origin")
    if not declared_country:
        # Extract language from the item (translation_lang or infer from visible_text)
        language = item.get("translation_lang") or item.get("visible_text_lang")
        text = item.get("visible_text")
        postmark = item.get("postmark", {}).get("city") if isinstance(item.get("postmark"), dict) else None
        publisher = item.get("publisher")
        
        candidates = country_suggest.suggest(
            language=language,
            text=text,
            postmark=postmark,
            publisher=publisher,
        )
        verdict["country_candidates"] = candidates
    else:
        verdict["country_candidates"] = []

    _non_copyright_blockers(verdict, item)

    origin = assess(origin_country, item, current_year)
    italy = assess("IT", item, current_year)
    shorter = origin_country != "IT"
    if shorter:
        _cap_italy_term(italy, origin, origin_country, current_year)
    verdict["shorter_term_applied"] = shorter
    verdict["jurisdictions"] = {"origin": origin, "italy": italy}

    statuses = {origin["status"], italy["status"]}
    if UNCERTAIN in statuses:
        verdict["status"] = UNCERTAIN
    elif statuses == {PUBLIC_DOMAIN}:
        verdict["status"] = PUBLIC_DOMAIN
    else:
        verdict["status"] = NOT_PUBLIC_DOMAIN

    primary = italy if origin_country == "IT" else origin
    verdict["reason"] = primary.get("reason") or italy.get("reason") or ""
    verdict["pd_year"] = primary.get("pd_year")
    verdict["years_until_pd"] = primary.get("years_until_pd")
    verdict["confidence"] = _worst(origin.get("confidence", "low"), italy.get("confidence", "low"))
    verdict["legal_basis"] = sorted(set(primary.get("legal_basis", [])) | set(italy.get("legal_basis", [])))
    # Commons tags describe the SOURCE country. Never fall back to Italy's tags:
    # a non-Italian work must not be tagged with an Italian PD template.
    verdict["commons_templates"] = list(origin.get("commons_templates") or [])
    verdict["missing_data"] = sorted(set(origin.get("missing_data", [])) | set(italy.get("missing_data", [])))
    verdict["notes"] = (
        origin.get("notes", []) + italy.get("notes", [])
        if origin_country != "IT" else origin.get("notes", [])
    )
    verdict["human_review_required"] = bool(verdict["blockers_non_copyright"]) or verdict["status"] == UNCERTAIN

    # A licence tag asserts a legal basis we do not hold while the verdict is uncertain.
    if verdict["status"] == UNCERTAIN and verdict["commons_templates"]:
        verdict["commons_templates"] = []
        verdict["notes"] = verdict["notes"] + [
            "Tag di pubblico dominio non emessi: il verdetto e' incerto."
        ]
    
    # Cap confidence at "low" when reference_year is set but year_source is visual_estimate
    if item.get("reference_year") is not None and item.get("year_source") == "visual_estimate":
        if _CONF_RANK.get(verdict["confidence"], 0) > _CONF_RANK["low"]:
            verdict["confidence"] = "low"
        verdict["notes"] = verdict.get("notes", []) + [
            "Anno stimato visivamente: la confidenza e' limitata."
        ]
    
    # Compute upload flag and reason (advisory for manual review, never automatic trigger)
    _set_upload_flag(verdict, upload_policy)
    
    return verdict


def evaluate_many(items: list[dict], current_year: int, upload_policy: str = UPLOAD_POLICY_STRICT) -> list[dict]:
    return [evaluate(item, current_year, upload_policy) for item in items]


def _load_items(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data.get("items", [])
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic multi-jurisdiction public-domain evaluator.")
    parser.add_argument("facts", help="JSON file with extracted facts (a list, or {items: [...]}).")
    parser.add_argument("-o", "--output", help="Write the verdicts JSON here (default: stdout).")
    parser.add_argument("--current-year", type=int, default=date.today().year)
    parser.add_argument(
        "--upload-policy",
        choices=[UPLOAD_POLICY_STRICT, UPLOAD_POLICY_PD_ONLY, UPLOAD_POLICY_ALL],
        default=UPLOAD_POLICY_STRICT,
        help="Upload policy: 'strict' (default), 'pd-only' (copyright-only reading), or 'all' (ready all items, including uncertain)."
    )
    args = parser.parse_args(argv)

    verdicts = evaluate_many(_load_items(args.facts), args.current_year, args.upload_policy)
    payload = json.dumps(
        {
            "rule_table_version": RULE_TABLE_VERSION,
            "current_year": args.current_year,
            "items": verdicts,
        },
        ensure_ascii=False,
        indent=2,
    )
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
