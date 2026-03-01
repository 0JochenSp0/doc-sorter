# app/normalize.py
from __future__ import annotations

import re

UMLAUT_MAP = {
    "ä": "ae", "ö": "oe", "ü": "ue",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "ß": "ss",
}

_ALLOWED = re.compile(r"[^A-Za-z0-9_]+")

def normalize_sender(sender: str) -> str:
    s = sender.strip()
    for k, v in UMLAUT_MAP.items():
        s = s.replace(k, v)
    s = s.replace(" ", "_")
    s = _ALLOWED.sub("", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return "Unbekannt"
    return s[:120]

def normalize_filename(stem: str, max_len: int = 120) -> str:
    s = stem.strip()
    for k, v in UMLAUT_MAP.items():
        s = s.replace(k, v)
    s = s.replace(" ", "_")
    s = _ALLOWED.sub("", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] if len(s) > max_len else s