# app/models.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic.aliases import AliasChoices
from pydantic.config import ConfigDict


class Settings(BaseModel):
    inbox_dir: str = ""
    output_dir: str = ""
    review_dir: str = ""
    log_dir: str = ""
    interval_minutes: int = 60

    # Sender candidate list (one per line)
    sender_candidates: str = Field(
        default="",
        description="Newline-separated list of sender candidates to match against FULL first page OCR/text",
    )

    # Keep logs only for last N days
    log_retention_days: int = Field(
        default=7,
        ge=1,
        le=365,
        description="How many days of logs to keep (daily rotation).",
    )

    # Bank/Depot sender list (one per line)
    bank_sender_candidates: str = Field(
        default="",
        description="Newline-separated list of BANK/DEPOT senders. If matched on first page, route to bank folder (unless 'Rechnung'/'invoice' present).",
    )

    # Folder label/name for bank documents inside the year
    bank_folder_name: str = Field(
        default="Bank",
        description="Folder name under <output>/<year>/ for bank documents.",
    )

    # NEW: year mismatch policy
    year_policy: str = Field(
        default="strict",
        description="How to handle OCR date year mismatch vs scan/metadata year: strict|relaxed|off",
    )

    # NEW: relaxed tolerance (+/- N years) when year_policy=relaxed
    year_relaxed_years: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Allowed +/- years around scan/metadata year when year_policy=relaxed.",
    )


class BrowseEntry(BaseModel):
    name: str
    path: str
    is_dir: bool


class BrowseResponse(BaseModel):
    cwd: str
    entries: List[BrowseEntry]


class RunRequest(BaseModel):
    reason: str = Field(default="manual", description="Why the run was triggered (manual/scheduled/etc.)")


class RunResponse(BaseModel):
    ok: bool
    message: str = ""
    counts: Dict[str, int] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class RunStatus(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    is_running: bool = Field(
        default=False,
        validation_alias=AliasChoices("is_running", "running"),
    )
    last_run_ts: Optional[str] = None
    last_run_duration_ms: Optional[int] = None
    last_counts: Dict[str, int] = Field(default_factory=dict)
    last_errors: List[str] = Field(default_factory=list)

    last_review_reasons: Dict[str, int] = Field(default_factory=dict)
    last_review_samples: List[Dict[str, str]] = Field(default_factory=list)


class ReviewItem(BaseModel):
    filename: str
    path: str
    mtime_ts: str
    size_bytes: int

    reason: Optional[str] = None
    extracted_date: Optional[str] = None
    sender: Optional[str] = None
    sender_confidence: Optional[float] = None
    audit_ts: Optional[str] = None


class ReviewListResponse(BaseModel):
    items: List[ReviewItem] = Field(default_factory=list)


class ReviewApplyRequest(BaseModel):
    filename: str
    override_date: str = Field(
        description="YYYY-MM or YYYY-MM-DD",
        examples=["2026-03", "2026-03-01"],
    )
    override_sender: Optional[str] = None


class ReviewApplyResponse(BaseModel):
    ok: bool
    message: str = ""
    new_path: Optional[str] = None
