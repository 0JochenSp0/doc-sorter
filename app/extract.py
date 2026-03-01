# app/extract.py
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple, List, Iterable

from pypdf import PdfReader

GER_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

# ----------
# Date regexes (OCR-robust)
# ----------

# D.M.YYYY / DD-MM-YY / DD/MM/YYYY etc. Allow spaces/newlines around separators.
_NUM_DMY = re.compile(
    r"(?<!\d)(\d{1,2})\s*[\.\-/]\s*(\d{1,2})\s*[\.\-/]\s*(\d{2,4})(?!\d)"
)

# YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD with variable month/day width.
_NUM_YMD = re.compile(
    r"(?<!\d)(\d{4})\s*[\.\-/]\s*(\d{1,2})\s*[\.\-/]\s*(\d{1,2})(?!\d)"
)

# Allow spaces around dot and make dot optional:
# - "8. Mai 2025"
# - "8 . Mai 2025"
# - "8 Mai 2025"
# - with newlines between tokens
_GER_LONG = re.compile(r"(?<!\d)(\d{1,2})\s*(?:\.)?\s*([^\W\d_]{3,})\s+(\d{2,4})(?!\d)", re.IGNORECASE)

# "Im November 2025", "November.2025", "im\nNovember\n2025"
_GER_MONTH_YEAR = re.compile(
    r"(?:\b(?:im|in|vom|zum|ab)\s+)?([^\W\d_]{3,})\s*[\.,:]?\s*(\d{4})\b",
    re.IGNORECASE,
)

# Marker priorities: we prefer dates close to these markers.
# Note: "datum" is intentionally weaker.
_MARKERS: List[Tuple[re.Pattern, int]] = [
    (re.compile(r"\b(versanddatum|versandt\s+am|versand\s*am)\b", re.IGNORECASE), 90),
    (re.compile(r"\b(rechnungsdatum|belegdatum|buchungsdatum)\b", re.IGNORECASE), 75),
    (re.compile(r"\b(ausgestellt\s+am|erstellt\s+am|druckdatum)\b", re.IGNORECASE), 55),
    (re.compile(r"\b(lieferdatum|leistungsdatum)\b", re.IGNORECASE), 45),
    (re.compile(r"\b(datum)\b", re.IGNORECASE), 20),
]

ADDRESS_HINT = re.compile(r"(An|An:\s|Herrn|Frau|z\.?\s*Hd\.?|z\.?\s*H\.)")


@dataclass(frozen=True)
class ShippingDate:
    year: int
    month: int
    day: Optional[int]  # None => month precision

    @property
    def precision(self) -> str:
        return "day" if self.day is not None else "month"

    def format_for_filename(self) -> str:
        if self.day is None:
            return f"{self.year:04d}-{self.month:02d}"
        return f"{self.year:04d}-{self.month:02d}-{self.day:02d}"


@dataclass
class SenderGuess:
    sender: Optional[str]
    confidence: float


def extract_text(pdf_path, max_pages: int = 2) -> str:
    reader = PdfReader(str(pdf_path))
    out: List[str] = []
    pages = min(len(reader.pages), max_pages)
    for i in range(pages):
        t = reader.pages[i].extract_text() or ""
        out.append(t)
    return "\n".join(out).strip()


def has_meaningful_text(text: str) -> bool:
    t = re.sub(r"\s+", "", text or "")
    return len(t) >= 30


# ------------------
# OCR / normalization helpers
# ------------------

def _normalize_month_token(mon_raw: str) -> str:
    # Normalize unicode (fix "März" => "März"), lowercase, map umlauts and ß
    s = unicodedata.normalize("NFC", (mon_raw or "").strip()).lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return s


def _fix_ocr_digit_confusions(s: str) -> str:
    """Fix typical OCR confusions *only* in digit-ish contexts."""
    if not s:
        return ""

    # Replace O/o with 0 when adjacent to digits or separators
    s = re.sub(r"(?<=\d)[Oo](?=\d)", "0", s)
    s = re.sub(r"(?<=\d)[Oo](?=[\.\-/\s])", "0", s)
    s = re.sub(r"(?<=[\.\-/\s])[Oo](?=\d)", "0", s)

    # Replace I/l/| with 1 when adjacent to digits or separators
    s = re.sub(r"(?<=\d)[Il\|](?=\d)", "1", s)
    s = re.sub(r"(?<=\d)[Il\|](?=[\.\-/\s])", "1", s)
    s = re.sub(r"(?<=[\.\-/\s])[Il\|](?=\d)", "1", s)

    return s


def _pivot_year(y: int, pivot: int = 70) -> int:
    """Convert 2-digit years to 4-digit (config-free, safe default)."""
    if y >= 100:
        return y
    # 00..69 => 2000..2069, 70..99 => 1970..1999
    return 2000 + y if y < pivot else 1900 + y


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(int(y), int(m), int(d))
    except Exception:
        return None


def _parse_numeric_match(m: re.Match, kind: str) -> Optional[date]:
    try:
        if kind == "ymd":
            y = int(m.group(1))
            mo = int(m.group(2))
            d = int(m.group(3))
            return _safe_date(y, mo, d)

        # dmy
        d = int(m.group(1))
        mo = int(m.group(2))
        y = int(m.group(3))
        y = _pivot_year(y)
        return _safe_date(y, mo, d)
    except Exception:
        return None


def _parse_ger_long(m: re.Match) -> Optional[date]:
    try:
        d = int(m.group(1))
        mon_raw = _normalize_month_token(m.group(2))
        mon = GER_MONTHS.get(mon_raw)
        y = int(m.group(3))
        y = _pivot_year(y)
        if mon is None:
            return None
        return _safe_date(y, mon, d)
    except Exception:
        return None


def _parse_ger_month_year(text: str) -> Optional[Tuple[int, int]]:
    m = _GER_MONTH_YEAR.search(text or "")
    if not m:
        return None
    mon_raw = _normalize_month_token(m.group(1))
    mon = GER_MONTHS.get(mon_raw)
    if not mon:
        return None
    try:
        y = int(m.group(2))
    except Exception:
        return None
    return (y, mon)


def _iter_marker_hits(text: str) -> Iterable[Tuple[int, int]]:
    """Yield (pos, weight) for marker matches."""
    for pat, w in _MARKERS:
        for m in pat.finditer(text):
            yield (m.start(), w)


def _closest_marker_weight_and_dist(markers: List[Tuple[int, int]], pos: int) -> Tuple[int, int]:
    if not markers:
        return (0, 10_000_000)
    best_dist = 10_000_000
    best_w = 0
    # markers list is small; linear scan is fine
    for mp, mw in markers:
        d = abs(pos - mp)
        if d < best_dist:
            best_dist = d
            best_w = mw
    return best_w, best_dist


def extract_shipping_date(text: str) -> Optional[ShippingDate]:
    """Extract the most likely date with a marker-proximity score.

    Strategy:
    - Normalize text (NFC) and apply limited OCR digit fixes.
    - Collect all marker hits and all date candidates (with character positions).
    - Score candidates by proximity to markers, then by document position.
    - Fallback to month-year if no full date exists.
    """

    text = unicodedata.normalize("NFC", text or "")
    text = _fix_ocr_digit_confusions(text)

    if not text.strip():
        return None

    # Use first ~12k chars (2 pages) – enough context, less noise.
    window_text = text[:12000]

    markers = list(_iter_marker_hits(window_text))

    candidates: List[Tuple[float, date]] = []

    def add_candidate(dt: date, pos: int):
        mw, dist = _closest_marker_weight_and_dist(markers, pos)

        # Distance score: within 120 chars is "very close".
        # Clamp distance effect so far-away dates don't dominate.
        dist_c = min(dist, 600)
        prox = max(0.0, 80.0 - (dist_c / 7.5))  # ~80..0

        # Earlier in document slightly preferred (letterhead). Clamp at ~6k.
        pos_c = min(pos, 6000)
        early = max(0.0, 18.0 - (pos_c / 350.0))

        # Base + marker weight
        score = prox + early + (mw * 0.55)

        # Very weak guard: avoid picking obvious far footer dates unless no better exists
        candidates.append((score, dt))

    for m in _NUM_YMD.finditer(window_text):
        dt = _parse_numeric_match(m, "ymd")
        if dt:
            add_candidate(dt, m.start())

    for m in _NUM_DMY.finditer(window_text):
        dt = _parse_numeric_match(m, "dmy")
        if dt:
            add_candidate(dt, m.start())

    for m in _GER_LONG.finditer(window_text):
        dt = _parse_ger_long(m)
        if dt:
            add_candidate(dt, m.start())

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
        return ShippingDate(year=best.year, month=best.month, day=best.day)

    # Fallback month precision
    my = _parse_ger_month_year(window_text)
    if my:
        y, mon = my
        return ShippingDate(year=y, month=mon, day=None)

    # Absolute fallback: try to find *any* plausible numeric date anywhere
    m = _NUM_YMD.search(window_text) or _NUM_DMY.search(window_text)
    if m:
        kind = "ymd" if m.re is _NUM_YMD else "dmy"
        dt = _parse_numeric_match(m, kind)
        if dt:
            return ShippingDate(year=dt.year, month=dt.month, day=dt.day)

    return None


# ------------------
# Sender guessing (unchanged)
# ------------------

def _clean_sender_line(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"^[•\-\|]+", "", s).strip()
    return s


def guess_sender_from_header(text: str) -> SenderGuess:
    lines = [ln.strip() for ln in (text or "").splitlines()]
    cleaned = [_clean_sender_line(ln) for ln in lines if _clean_sender_line(ln)]
    header: List[str] = []

    for ln in cleaned[:90]:
        if ADDRESS_HINT.search(ln):
            break
        header.append(ln)
        if len(header) >= 35:
            break

    if not header:
        return SenderGuess(None, 0.0)

    def score(line: str) -> float:
        l = line.strip()
        if len(l) < 4:
            return 0.0
        low = l.lower()

        bad_tokens = [
            "www.",
            "http",
            "@",
            "telefon",
            "tel.",
            "fax",
            "email",
            "e-mail",
            "iban",
            "bic",
            "ust",
            "steuer",
            "kundennr",
            "rechnungsnr",
        ]
        if any(t in low for t in bad_tokens):
            return 0.0

        letters = sum(ch.isalpha() for ch in l)
        digits = sum(ch.isdigit() for ch in l)
        if letters < 4 or digits > letters:
            return 0.0

        org_hints = [
            "gmbh",
            "ag",
            "kg",
            "mbh",
            "ohg",
            "ev",
            "e.v.",
            "stadt",
            "gemeinde",
            "landratsamt",
            "ministerium",
            "versicherung",
            "bank",
            "sparkasse",
            "amt",
            "behörde",
            "klinikum",
        ]
        hint = 10.0 if any(h in low for h in org_hints) else 0.0

        base = min(20.0, 0.6 * len(l)) + hint
        if re.search(r"\b(str\.|straße|platz|weg|allee)\b", low) and letters < 25:
            base -= 5.0
        if digits >= 3 and re.search(r"\b\d{4,}\b", l):
            base -= 3.0

        return max(0.0, base)

    scored = [(score(ln), ln) for ln in header]
    scored.sort(key=lambda x: x[0], reverse=True)

    best_score, best = scored[0]
    if best_score >= 10.0:
        conf = min(0.85, 0.35 + best_score / 40.0)
        return SenderGuess(best, conf)

    return SenderGuess(None, 0.0)


def extract_pdf_metadata_year(pdf_path) -> Optional[int]:
    """Extract year from PDF metadata CreationDate/ModDate if present."""
    try:
        reader = PdfReader(str(pdf_path))
        md = reader.metadata or {}
        for key in ("/CreationDate", "/ModDate"):
            val = md.get(key)
            if not val:
                continue
            # Example: D:20250227123000+01'00'
            m = re.search(r"(19\d{2}|20\d{2})", str(val))
            if m:
                return int(m.group(1))
    except Exception:
        return None
    return None
