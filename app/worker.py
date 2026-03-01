# app/worker.py
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .db import DB
from .models import Settings
from .fs import stable_check, move_file, next_duplicate_path, safe_mkdir
from .extract import (
    extract_text,
    has_meaningful_text,
    extract_shipping_date,
    guess_sender_from_header,
    extract_pdf_metadata_year,
)
from .ocr import run_ocr_to_temp, cleanup_temp_ocr
from .normalize import normalize_filename


@dataclass
class ProcessResult:
    counts: Dict[str, int]
    errors: List[str]
    review_reasons: Dict[str, int]
    review_samples: List[Dict[str, str]]


# ============================================================
# NEW: list normalization & sorting (after each run)
# ============================================================

def _normalize_list_block(raw: str) -> str:
    """
    - trim whitespace
    - remove empty lines
    - remove duplicates (case-insensitive)
    - sort alphabetically (case-insensitive)
    """
    lines = [ln.strip() for ln in (raw or "").splitlines()]
    lines = [ln for ln in lines if ln]

    seen: Dict[str, str] = {}
    for ln in lines:
        key = ln.lower()
        if key not in seen:
            seen[key] = ln

    sorted_lines = sorted(seen.values(), key=lambda x: x.lower())
    return "\n".join(sorted_lines)


def _auto_sort_sender_lists(db: DB, settings: Settings, logger) -> None:
    """
    Ensures both sender lists are always sorted and deduplicated.
    Writes only if changed.
    """
    try:
        current_sender = (settings.sender_candidates or "").strip()
        current_bank = (settings.bank_sender_candidates or "").strip()

        sorted_sender = _normalize_list_block(current_sender)
        sorted_bank = _normalize_list_block(current_bank)

        if sorted_sender != current_sender:
            db.set_setting("sender_candidates", sorted_sender)
        if sorted_bank != current_bank:
            db.set_setting("bank_sender_candidates", sorted_bank)
    except Exception as e:
        logger.warning("Auto-sort sender lists failed: %s", e)


# -----------------------------
# Invoice detection + invoice no
# -----------------------------

_INVOICE_PATTERNS_STRONG = [
    re.compile(r"\brechnung\b", re.IGNORECASE),
    re.compile(r"\brechnungs[-\s]*nr\.?\b", re.IGNORECASE),
    re.compile(r"\brechnungsnummer\b", re.IGNORECASE),
    re.compile(r"\bre\.\s*nr\.?\b", re.IGNORECASE),
    re.compile(r"\binvoice\b", re.IGNORECASE),
    re.compile(r"\binvoice\s*no\.?\b", re.IGNORECASE),
]

_INVOICE_PATTERNS_FIN = [
    re.compile(r"\biban\b", re.IGNORECASE),
    re.compile(r"\bbic\b", re.IGNORECASE),
    re.compile(r"\bmwst\b", re.IGNORECASE),
    re.compile(r"\bust\b", re.IGNORECASE),
    re.compile(r"\bumsatzsteuer\b", re.IGNORECASE),
    re.compile(r"\bmehrwertsteuer\b", re.IGNORECASE),
    re.compile(r"\bnetto\b", re.IGNORECASE),
    re.compile(r"\bbrutto\b", re.IGNORECASE),
    re.compile(r"\bgesamtbetrag\b", re.IGNORECASE),
    re.compile(r"\bzahlbetrag\b", re.IGNORECASE),
    re.compile(r"\bzu\s+zahlen\b", re.IGNORECASE),
    re.compile(r"\bfällig\b", re.IGNORECASE),
    re.compile(r"\bzahlungsziel\b", re.IGNORECASE),
]

_MONEY_EUR = re.compile(r"(\d{1,3}(\.\d{3})*,\d{2}\s*€|\d+,\d{2}\s*€)", re.IGNORECASE)

_INVOICE_NO_PATTERNS = [
    re.compile(r"\b(RE[-\s]?\d{4}[-/]\d{2,6})\b", re.IGNORECASE),
    re.compile(r"\b(R[-\s]?\d{4}[-/]\d{2,6})\b", re.IGNORECASE),
    re.compile(r"\b(\d{4}[-/]\d{3,10})\b", re.IGNORECASE),
    re.compile(r"\brechnungs(?:nummer|nr)\.?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{3,})\b", re.IGNORECASE),
    re.compile(r"\binvoice\s*(?:no|number)\.?\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{3,})\b", re.IGNORECASE),
]

# keyword that overrides bank routing rule (your requirement)
_INVOICE_KEYWORD = re.compile(r"\b(rechnung|invoice)\b", re.IGNORECASE)


def is_invoice(text: str) -> bool:
    t = text or ""
    if len(t) < 30:
        return False

    strong_hits = sum(1 for p in _INVOICE_PATTERNS_STRONG if p.search(t))
    fin_hits = sum(1 for p in _INVOICE_PATTERNS_FIN if p.search(t))
    money = bool(_MONEY_EUR.search(t))

    if strong_hits >= 1 and (fin_hits >= 1 or money):
        return True
    if fin_hits >= 3 and money:
        return True
    if re.search(r"\biban\b", t, re.IGNORECASE) and re.search(
        r"\bfällig\b|\bzu\s+zahlen\b|\bgesamtbetrag\b|\bzahlbetrag\b", t, re.IGNORECASE
    ) and money:
        return True

    return False


def extract_invoice_number(text: str) -> Optional[str]:
    t = text or ""
    if not t:
        return None

    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    window = "\n".join(lines[:200])

    candidates: List[Tuple[int, str]] = []

    def add(score: int, val: str):
        v = (val or "").strip()
        if not v:
            return
        v = v.replace(" ", "").strip(".,;:()[]{}<>")
        if 3 <= len(v) <= 40:
            candidates.append((score, v))

    for pat in _INVOICE_NO_PATTERNS:
        for m in pat.finditer(window):
            add(90, m.group(1))

    if not candidates:
        for pat in _INVOICE_NO_PATTERNS[:3]:
            m = pat.search(t)
            if m:
                add(60, m.group(1))

    if not candidates:
        return None

    def rank(val: str) -> int:
        v = val.upper()
        score = 0
        if v.startswith("RE"):
            score += 40
        if re.search(r"\d{4}", v):
            score += 20
        if "-" in v or "/" in v:
            score += 10
        score += min(10, len(v) // 5)
        return score

    candidates.sort(key=lambda x: (x[0], rank(x[1])), reverse=True)
    best = candidates[0][1]
    best = re.sub(r"[^A-Za-z0-9\-_\/]", "", best).replace("/", "-")
    return best or None


# -----------------------------
# Sender candidate list matching on FULL FIRST PAGE
# -----------------------------

def _norm(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_sender_candidates(raw: str) -> List[str]:
    lines = [ln.strip() for ln in (raw or "").splitlines()]
    out: List[str] = []
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        out.append(ln)
    return out


def match_sender_candidates_first_page(page1_text: str, candidates: List[str]) -> Tuple[Optional[str], float]:
    """
    General sender matching (substring OR fuzzy).
    Good for picking a sender name.
    """
    if not candidates:
        return (None, 0.0)

    t_norm = _norm(page1_text)
    if not t_norm:
        return (None, 0.0)

    best_cand: Optional[str] = None
    best_score: float = 0.0

    for cand in candidates:
        c_norm = _norm(cand)
        if not c_norm:
            continue

        if c_norm in t_norm:
            score = min(1.0, 0.92 + min(0.08, len(c_norm) / 120.0))
            if score > best_score:
                best_cand, best_score = cand, score
            continue

        ratio = SequenceMatcher(a=c_norm, b=t_norm).ratio()
        if len(c_norm) < 4:
            ratio *= 0.85
        if ratio > best_score:
            best_cand, best_score = cand, ratio

    if best_cand is not None and best_score >= 0.74:
        return (best_cand, best_score)
    return (None, 0.0)


# --- strict bank sender matching (NO fuzzy!) ---
_BANK_GENERIC_BLOCK = {
    "bank",
    "konto",
    "depot",
    "wertpapier",
    "giro",
    "onlinebanking",
    "rechnung",
    "invoice",
}


def _word_boundary_pat(norm_phrase: str) -> re.Pattern:
    esc = re.escape(norm_phrase)
    return re.compile(rf"(?:^|\s){esc}(?:\s|$)")


def match_bank_sender_strict(page1_text: str, candidates: List[str]) -> Tuple[Optional[str], float]:
    """
    Bank sender list must be TRUE sender-matching.
    Strict matching only (normalized word/phrase boundaries), no fuzzy.
    """
    if not candidates:
        return (None, 0.0)

    t_norm = _norm(page1_text)
    if not t_norm:
        return (None, 0.0)

    best: Optional[str] = None
    best_score: float = 0.0

    for cand in candidates:
        c_norm = _norm(cand)
        if not c_norm:
            continue

        # guard: block overly generic entries
        if c_norm in _BANK_GENERIC_BLOCK:
            continue

        toks = c_norm.split()

        # single token: require exact-word match and length >= 3 (ING, DKB, FNZ)
        if len(toks) == 1:
            if len(c_norm) < 3:
                continue
            pat = re.compile(rf"(?:^|\s){re.escape(c_norm)}(?:\s|$)")
            if pat.search(t_norm):
                score = min(0.98, 0.85 + min(0.13, len(c_norm) / 40.0))
                if score > best_score:
                    best, best_score = cand, score
            continue

        # multi-token phrase
        pat = _word_boundary_pat(c_norm)
        if pat.search(t_norm):
            score = min(0.99, 0.90 + min(0.09, len(c_norm) / 80.0))
            if score > best_score:
                best, best_score = cand, score

    if best:
        return (best, best_score)
    return (None, 0.0)


_CORP_STOPWORDS = {
    "krankenversicherung", "versicherung", "versicherungen",
    "a.g.", "ag", "gmbh", "mbh", "kg", "ohg", "se", "eg", "ev", "e.v.",
    "krankenkasse", "kasse", "bank", "sparkasse",
    "vorstand", "aufsichtsrat",
    "kundenservice", "servicecenter", "kundendienst",
    "abteilung", "verwaltung", "service", "info",
}

_UPPER_TOKEN = re.compile(r"\b[A-ZÄÖÜ]{2,6}\b")
_BRANDLIKE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{2,20}\b")

_GENERIC_BAD = {
    "rechnung", "invoice", "angebot", "mahnung", "kontoauszug", "vertrag", "bestellung",
    "kundennummer", "kunden", "referenz", "ref", "nummer", "nr",
    "deutschland", "germany",
}


def shorten_sender(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    s = s.split(",")[0].strip()
    s = re.sub(r"\s+", " ", s)
    tokens = [t.strip(" .,-;:/()[]{}") for t in s.split(" ") if t.strip(" .,-;:/()[]{}")]
    if not tokens:
        return s

    first = tokens[0]
    if len(first) >= 3 and any(ch.isupper() for ch in first[1:]) and any(ch.islower() for ch in first):
        return first

    kept: List[str] = []
    for t in tokens:
        low = t.lower()
        if low in _CORP_STOPWORDS:
            break
        kept.append(t)
        if len(kept) >= 2:
            break
    return " ".join(kept) if kept else first


def _fallback_sender_from_text(page1_text: str) -> Optional[str]:
    if not page1_text:
        return None
    lines = [ln.strip() for ln in page1_text.splitlines() if ln.strip()]
    head = " \n".join(lines[:120])

    banned = {"IBAN", "BIC", "UST", "MWST", "SEPA", "PDF", "EUR"}
    for m in _UPPER_TOKEN.finditer(head):
        tok = m.group(0)
        if tok in banned:
            continue
        return tok

    for m in _BRANDLIKE.finditer(head):
        tok = m.group(0)
        low = tok.lower()
        if low in _GENERIC_BAD:
            continue
        if any(ch.isalpha() for ch in tok) and len(tok) >= 3:
            return tok

    return None


def _fallback_sender_from_filename(filename: str) -> Optional[str]:
    name = (filename or "").rsplit(".", 1)[0]
    if not name:
        return None
    parts = re.split(r"[_\-\s]+", name)
    parts = [p for p in parts if p]

    def good(tok: str) -> bool:
        if len(tok) < 2:
            return False
        low = tok.lower()
        if low in _GENERIC_BAD:
            return False
        if re.fullmatch(r"\d{4}", tok) or re.fullmatch(r"\d{1,2}", tok):
            return False
        if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", tok):
            return False
        return any(ch.isalpha() for ch in tok)

    candidates = [p for p in parts if good(p)]
    if not candidates:
        return None

    for c in candidates:
        if any(ch.isupper() for ch in c[1:]) and any(ch.islower() for ch in c):
            return c
    for c in candidates:
        if c.isupper() and 2 <= len(c) <= 6:
            return c
    return candidates[0]


def pick_sender_for_filename(
    settings: Settings,
    sender_guess,
    page1_text: str,
    original_filename: str,
) -> Tuple[str, str, float]:
    cand_list = parse_sender_candidates(settings.sender_candidates)
    best_cand, best_score = match_sender_candidates_first_page(page1_text, cand_list)
    if best_cand:
        return (best_cand, best_cand, best_score)

    if sender_guess and getattr(sender_guess, "sender", None):
        short = shorten_sender(sender_guess.sender)
        if short:
            conf = float(getattr(sender_guess, "confidence", 0.30) or 0.30)
            return (short, sender_guess.sender, conf)

    s2 = _fallback_sender_from_text(page1_text)
    if s2:
        return (s2, s2, 0.35)

    s3 = _fallback_sender_from_filename(original_filename)
    if s3:
        return (s3, s3, 0.30)

    return ("Dokument", "Dokument", 0.20)


# -----------------------------
# Year cross-check (STRICT)
# -----------------------------

def scan_dt_utc(p: Path) -> datetime:
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)


def scan_date_prefix(p: Path) -> str:
    return scan_dt_utc(p).strftime("%Y%m%d")


def meta_year_candidates(p: Path) -> List[int]:
    years = [scan_dt_utc(p).year]
    py = extract_pdf_metadata_year(p)
    if py and py not in years:
        years.append(py)
    return years


def year_matches(ship_year: int, meta_years: List[int], *, policy: str, relaxed_years: int) -> bool:
    """Decide whether ship_year is acceptable compared to scan/metadata years.

    policy:
      - strict: ship_year == meta_year OR ship_year == meta_year-1
      - relaxed: ship_year within +/- relaxed_years of ANY meta year
      - off: always accept
    """

    pol = (policy or "strict").strip().lower()
    if pol == "off":
        return True

    if pol == "strict":
        for y in meta_years:
            if ship_year == y or ship_year == (y - 1):
                return True
        return False

    # relaxed
    tol = max(0, min(int(relaxed_years), 10))
    for y in meta_years:
        if (y - tol) <= ship_year <= (y + tol):
            return True
    return False


# -----------------------------
# Worker
# -----------------------------

def _validate_settings(s: Settings) -> Optional[str]:
    if not (s.inbox_dir and s.output_dir and s.review_dir and s.log_dir):
        return "missing_paths"
    return None


def process_inbox(*, db: DB, settings: Settings, media_root: Path, logger) -> ProcessResult:
    counts = {"success": 0, "review": 0, "ignored": 0, "error": 0}
    errors: List[str] = []
    review_reasons: Dict[str, int] = {}
    review_samples: List[Dict[str, str]] = []

    def add_review(reason_key: str, filename: str, detail: Optional[str] = None) -> None:
        review_reasons[reason_key] = int(review_reasons.get(reason_key, 0)) + 1
        reason = reason_key if not detail else f"{reason_key}:{detail}"
        review_samples.append({"file": filename, "reason": reason})
        if len(review_samples) > 20:
            del review_samples[:-20]

    err = _validate_settings(settings)
    if err:
        errors.append(f"Settings invalid: {err}")
        return ProcessResult(counts=counts, errors=errors, review_reasons=review_reasons, review_samples=review_samples)

    inbox = Path(settings.inbox_dir)
    output = Path(settings.output_dir)
    review = Path(settings.review_dir)

    safe_mkdir(output)
    safe_mkdir(review)

    try:
        entries = sorted(inbox.iterdir(), key=lambda p: p.name.lower())
    except Exception as e:
        errors.append(f"Inbox not accessible: {e}")
        return ProcessResult(counts=counts, errors=errors, review_reasons=review_reasons, review_samples=review_samples)

    logger.info("Run start. Inbox=%s | entries=%d", str(inbox), len(entries))
    for h in getattr(logger, "handlers", []):
        try:
            h.flush()
        except Exception:
            pass

    for p in entries:
        if not p.is_file():
            continue

        if p.suffix.lower() != ".pdf":
            counts["ignored"] += 1
            db.add_audit(
                status="ignored",
                original_path=str(p.parent),
                original_name=p.name,
                extracted_date=None,
                sender=None,
                sender_confidence=None,
                target_path=None,
                new_name=None,
                duplicate_n=None,
                error_message=None,
            )
            continue

        try:
            sc = stable_check(p, logger)
            if not sc.stable:
                dst = review / p.name
                reason_key = "unstable"
                detail = sc.reason
                try:
                    move_file(p, dst)
                    counts["review"] += 1
                    add_review(reason_key, p.name, detail=detail)
                    db.add_audit(
                        status="review",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=None,
                        sender=None,
                        sender_confidence=None,
                        target_path=str(dst.parent),
                        new_name=dst.name,
                        duplicate_n=None,
                        error_message=f"{reason_key}:{detail}",
                    )
                except Exception as me:
                    counts["error"] += 1
                    errors.append(f"Move to review failed for {p.name}: {me}")
                    db.add_audit(
                        status="error",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=None,
                        sender=None,
                        sender_confidence=None,
                        target_path=str(review),
                        new_name=p.name,
                        duplicate_n=None,
                        error_message=f"unstable_move_failed:{me}",
                    )
                continue

            # Extract: full first page + first 2 pages
            page1_text = ""
            text_2pages = ""
            try:
                page1_text = extract_text(p, max_pages=1)
                text_2pages = extract_text(p, max_pages=2)
            except Exception as e:
                logger.warning("Text extraction failed, trying OCR: %s (%s)", p.name, e)
                page1_text = ""
                text_2pages = ""

            ocr_tmp: Optional[Path] = None
            if not has_meaningful_text(text_2pages):
                try:
                    ocr_tmp = run_ocr_to_temp(p, logger)
                    page1_text = extract_text(ocr_tmp, max_pages=1)
                    text_2pages = extract_text(ocr_tmp, max_pages=2)
                except Exception as e:
                    dst = review / p.name
                    reason_key = "ocr_failed"
                    try:
                        move_file(p, dst)
                        counts["review"] += 1
                        add_review(reason_key, p.name, detail=str(e))
                        db.add_audit(
                            status="review",
                            original_path=str(p.parent),
                            original_name=p.name,
                            extracted_date=None,
                            sender=None,
                            sender_confidence=None,
                            target_path=str(dst.parent),
                            new_name=dst.name,
                            duplicate_n=None,
                            error_message=f"{reason_key}:{e}",
                        )
                    except Exception as me:
                        counts["error"] += 1
                        errors.append(f"OCR failed and move to review failed for {p.name}: {me}")
                        db.add_audit(
                            status="error",
                            original_path=str(p.parent),
                            original_name=p.name,
                            extracted_date=None,
                            sender=None,
                            sender_confidence=None,
                            target_path=str(review),
                            new_name=p.name,
                            duplicate_n=None,
                            error_message=f"ocr_failed_move_failed:{me}",
                        )
                    finally:
                        if ocr_tmp:
                            cleanup_temp_ocr(ocr_tmp)
                    continue
                finally:
                    if ocr_tmp:
                        cleanup_temp_ocr(ocr_tmp)


            # Guess sender as early as possible so Review items can prefill it
            sender_guess = guess_sender_from_header(page1_text)
            sender_for_name, sender_for_audit, sender_conf = pick_sender_for_filename(
                settings, sender_guess, page1_text, p.name
            )
            ship_date = extract_shipping_date(text_2pages)
            if not ship_date:
                dst = review / p.name
                reason_key = "date_not_found"
                try:
                    move_file(p, dst)
                    counts["review"] += 1
                    add_review(reason_key, p.name)
                    db.add_audit(
                        status="review",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=None,
                        sender=None,
                        sender_confidence=None,
                        target_path=str(dst.parent),
                        new_name=dst.name,
                        duplicate_n=None,
                        error_message=reason_key,
                    )
                except Exception as me:
                    counts["error"] += 1
                    errors.append(f"Date missing and move to review failed for {p.name}: {me}")
                    db.add_audit(
                        status="error",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=None,
                        sender=sender_for_audit,
                        sender_confidence=sender_conf,
                        target_path=str(review),
                        new_name=p.name,
                        duplicate_n=None,
                        error_message=f"date_missing_move_failed:{me}",
                    )
                continue

            date_part = ship_date.format_for_filename()  # YYYY-MM or YYYY-MM-DD
            ship_year = int(date_part.split("-")[0])
            meta_years = meta_year_candidates(p)

            if not year_matches(ship_year, meta_years, policy=settings.year_policy, relaxed_years=settings.year_relaxed_years):
                pref = scan_date_prefix(p)  # yyyymmdd of scan
                dst = review / f"{pref}_{p.name}"
                reason_key = "year_mismatch"
                detail = f"ship={ship_year},meta={meta_years}"
                try:
                    move_file(p, dst)
                    counts["review"] += 1
                    add_review(reason_key, p.name, detail=detail)
                    db.add_audit(
                        status="review",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=date_part,
                        sender=sender_for_audit,
                        sender_confidence=sender_conf,
                        target_path=str(dst.parent),
                        new_name=dst.name,
                        duplicate_n=None,
                        error_message=f"{reason_key}:{detail}",
                    )
                except Exception as me:
                    counts["error"] += 1
                    errors.append(f"Year mismatch and move to review failed for {p.name}: {me}")
                    db.add_audit(
                        status="error",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=date_part,
                        sender=sender_for_audit,
                        sender_confidence=sender_conf,
                        target_path=str(review),
                        new_name=p.name,
                        duplicate_n=None,
                        error_message=f"year_mismatch_move_failed:{me}",
                    )
                continue

            parts = date_part.split("-")
            year = parts[0]
            month = parts[1]
            date_for_db = date_part

            # sender_guess already computed above for Review prefilling

            # ---- bank vs invoice routing rules ----
            bank_list = parse_sender_candidates(settings.bank_sender_candidates)

            # IMPORTANT: strict bank sender matching only (NO fuzzy)
            bank_sender_match, _bank_score = match_bank_sender_strict(page1_text, bank_list)

            has_invoice_keyword = bool(_INVOICE_KEYWORD.search(text_2pages or ""))
            invoice_heuristic = is_invoice(text_2pages)

            # strongest rule: explicit keyword wins
            if has_invoice_keyword:
                route = "invoice"
            elif bank_sender_match:
                route = "bank"
            elif invoice_heuristic:
                route = "invoice"
            else:
                route = "month"

            invoice_no = extract_invoice_number(text_2pages) if route == "invoice" else None

            bank_folder = (settings.bank_folder_name or "Bank").strip() or "Bank"
            if route == "invoice":
                target_dir = Path(output) / year / "Rechnung"
            elif route == "bank":
                target_dir = Path(output) / year / bank_folder
            else:
                target_dir = Path(output) / year / month

            safe_mkdir(target_dir)

            # naming schema: date + sender (+ invoice no if present)
            if route == "invoice" and invoice_no:
                stem_raw = f"{date_part}_{sender_for_name}_{invoice_no}"
            else:
                stem_raw = f"{date_part}_{sender_for_name}"

            file_stem = normalize_filename(stem_raw, max_len=120)
            base_name = f"{file_stem}.pdf"

            dst = target_dir / base_name
            dst_final, dup_n = next_duplicate_path(dst)

            try:
                move_file(p, dst_final)
                counts["success"] += 1

                route_info = route
                if bank_sender_match:
                    route_info += f":bank_sender={bank_sender_match}"
                if has_invoice_keyword:
                    route_info += ":keyword_invoice"
                elif invoice_heuristic:
                    route_info += ":heuristic_invoice"

                db.add_audit(
                    status="success",
                    original_path=str(p.parent),
                    original_name=p.name,
                    extracted_date=date_for_db,
                    sender=sender_for_audit,
                    sender_confidence=sender_conf,
                    target_path=str(dst_final.parent),
                    new_name=dst_final.name,
                    duplicate_n=(dup_n if dup_n > 0 else None),
                    error_message=route_info,
                )
            except Exception as me:
                reason_key = "move_failed"
                try:
                    dst_rev = review / p.name
                    move_file(p, dst_rev)
                    counts["review"] += 1
                    add_review(reason_key, p.name, detail=str(me))
                    db.add_audit(
                        status="review",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=date_for_db,
                        sender=sender_for_audit,
                        sender_confidence=sender_conf,
                        target_path=str(dst_rev.parent),
                        new_name=dst_rev.name,
                        duplicate_n=None,
                        error_message=f"{reason_key}:{me}",
                    )
                except Exception as me2:
                    counts["error"] += 1
                    errors.append(f"Move failed and review move failed for {p.name}: {me2}")
                    db.add_audit(
                        status="error",
                        original_path=str(p.parent),
                        original_name=p.name,
                        extracted_date=date_for_db,
                        sender=sender_for_audit,
                        sender_confidence=sender_conf,
                        target_path=str(review),
                        new_name=p.name,
                        duplicate_n=None,
                        error_message=f"move_failed_review_failed:{me2}",
                    )

        except Exception as e:
            counts["error"] += 1
            errors.append(f"Unhandled error for {p.name}: {e}")
            db.add_audit(
                status="error",
                original_path=str(p.parent),
                original_name=p.name,
                extracted_date=None,
                sender=None,
                sender_confidence=None,
                target_path=None,
                new_name=None,
                duplicate_n=None,
                error_message=f"unhandled:{e}",
            )

    # After each run: sort & dedupe both lists
    _auto_sort_sender_lists(db, settings, logger)

    logger.info("Run end. Counts=%s | review_reasons=%s", counts, review_reasons)
    for h in getattr(logger, "handlers", []):
        try:
            h.flush()
        except Exception:
            pass

    return ProcessResult(
        counts=counts,
        errors=errors,
        review_reasons=review_reasons,
        review_samples=review_samples,
    )
