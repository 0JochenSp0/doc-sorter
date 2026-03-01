# app/main.py
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_env
from .db import DB
from .models import (
    Settings,
    BrowseResponse,
    BrowseEntry,
    RunRequest,
    RunResponse,
    ReviewListResponse,
    ReviewItem,
    ReviewApplyRequest,
    ReviewApplyResponse,
)
from .fs import ensure_within_root, safe_mkdir, move_file, next_duplicate_path
from .logging_setup import setup_logging
from .scheduler import AppScheduler
from .normalize import normalize_filename

env = get_env()
db = DB(env.db_path)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))

_logger = None
_logger_cfg = {"dir": None, "retention": None}


def get_settings() -> Settings:
    raw = db.get_all_settings()
    return Settings(
        inbox_dir=raw.get("inbox_dir", ""),
        output_dir=raw.get("output_dir", ""),
        review_dir=raw.get("review_dir", ""),
        log_dir=raw.get("log_dir", ""),
        interval_minutes=int(raw.get("interval_minutes", "60") or "60"),
        sender_candidates=raw.get("sender_candidates", ""),
        log_retention_days=int(raw.get("log_retention_days", "7") or "7"),
        bank_sender_candidates=raw.get("bank_sender_candidates", ""),
        bank_folder_name=raw.get("bank_folder_name", "Bank") or "Bank",
        year_policy=(raw.get("year_policy", "strict") or "strict"),
        year_relaxed_years=int(raw.get("year_relaxed_years", "2") or "2"),
    )


def get_logger():
    global _logger, _logger_cfg
    s = get_settings()
    log_dir = Path(s.log_dir) if s.log_dir else (env.media_root / "doc_sorter_logs")
    retention = int(s.log_retention_days or 7)

    if _logger is None or _logger_cfg["dir"] != str(log_dir) or _logger_cfg["retention"] != retention:
        _logger = setup_logging(log_dir, retention_days=retention)
        _logger_cfg = {"dir": str(log_dir), "retention": retention}

    return _logger


scheduler = AppScheduler(
    db=db,
    media_root=env.media_root,
    get_settings_callable=get_settings,
    get_logger_callable=get_logger,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    defaults = {
        "inbox_dir": "",
        "output_dir": "",
        "review_dir": "",
        "log_dir": "",
        "interval_minutes": "60",
        "sender_candidates": "",
        "log_retention_days": "7",
        "bank_sender_candidates": "",
        "bank_folder_name": "Bank",
        "year_policy": "strict",
        "year_relaxed_years": "2",
    }
    for k, v in defaults.items():
        if db.get_setting(k) is None:
            db.set_setting(k, v)

    get_logger()
    scheduler.start()

    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="Dokumenten-Sorter", version="1.1.2", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


def _validate_media_path(p: str) -> Path:
    if not p:
        raise HTTPException(status_code=400, detail="Path required")
    path = Path(p)
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute")
    if not ensure_within_root(env.media_root, path):
        raise HTTPException(status_code=400, detail="Path must be within /media")
    return path


@app.get("/api/settings", response_model=Settings)
def api_get_settings():
    return get_settings()


@app.put("/api/settings", response_model=Settings)
def api_put_settings(s: Settings):
    for key, val in [
        ("inbox_dir", s.inbox_dir),
        ("output_dir", s.output_dir),
        ("review_dir", s.review_dir),
        ("log_dir", s.log_dir),
    ]:
        if val:
            _ = _validate_media_path(val)
            if not Path(val).exists():
                raise HTTPException(status_code=400, detail=f"{key} does not exist: {val}")
            if not Path(val).is_dir():
                raise HTTPException(status_code=400, detail=f"{key} is not a directory: {val}")

    if s.log_retention_days < 1 or s.log_retention_days > 365:
        raise HTTPException(status_code=400, detail="log_retention_days must be between 1 and 365")

    # bank folder name: keep it simple and safe
    bank_folder = (s.bank_folder_name or "Bank").strip()
    if not bank_folder:
        bank_folder = "Bank"
    if len(bank_folder) > 40:
        raise HTTPException(status_code=400, detail="bank_folder_name too long (max 40)")
    if any(ch in bank_folder for ch in "/\\"):
        raise HTTPException(status_code=400, detail="bank_folder_name must not contain slashes")

    # year policy validation
    policy = (s.year_policy or "strict").strip().lower()
    if policy not in {"strict", "relaxed", "off"}:
        raise HTTPException(status_code=400, detail="year_policy must be one of: strict, relaxed, off")

    if s.year_relaxed_years < 0 or s.year_relaxed_years > 10:
        raise HTTPException(status_code=400, detail="year_relaxed_years must be between 0 and 10")

    db.set_setting("inbox_dir", s.inbox_dir)
    db.set_setting("output_dir", s.output_dir)
    db.set_setting("review_dir", s.review_dir)
    db.set_setting("log_dir", s.log_dir)
    db.set_setting("interval_minutes", str(s.interval_minutes))
    db.set_setting("sender_candidates", s.sender_candidates or "")
    db.set_setting("log_retention_days", str(int(s.log_retention_days)))

    db.set_setting("bank_sender_candidates", s.bank_sender_candidates or "")
    db.set_setting("bank_folder_name", bank_folder)

    db.set_setting("year_policy", policy)
    db.set_setting("year_relaxed_years", str(int(s.year_relaxed_years)))

    get_logger()
    return get_settings()


@app.get("/api/status")
def api_status():
    return scheduler.status().model_dump()


@app.post("/api/run", response_model=RunResponse)
def api_run(req: RunRequest):
    ok = scheduler.trigger_manual(reason=req.reason or "manual")
    return RunResponse(ok=ok, message=("Run scheduled." if ok else "Could not schedule run."))


@app.get("/api/browse", response_model=BrowseResponse)
def api_browse(path: Optional[str] = None):
    base = env.media_root.resolve()
    current = base if not path else _validate_media_path(path).resolve()

    if not ensure_within_root(base, current):
        raise HTTPException(status_code=400, detail="Path must be within /media")
    if not current.exists() or not current.is_dir():
        raise HTTPException(status_code=400, detail="Directory does not exist")

    entries: List[BrowseEntry] = []
    try:
        for p in sorted(current.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.is_dir():
                entries.append(BrowseEntry(name=p.name, path=str(p), is_dir=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Browse failed: {e}")

    return BrowseResponse(cwd=str(current), entries=entries)


# ------------------------------------------------------------
# Review queue (UI)
# ------------------------------------------------------------


def _iso_from_mtime(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


@app.get("/api/review/list", response_model=ReviewListResponse)
def api_review_list(limit: int = 50):
    s = get_settings()
    if not s.review_dir:
        return ReviewListResponse(items=[])

    review_dir = Path(s.review_dir)
    if not review_dir.exists() or not review_dir.is_dir():
        return ReviewListResponse(items=[])

    # Map filename -> latest audit info
    audits = db.list_recent_review_audits(limit=500)
    audit_by_new: dict[str, dict] = {}
    for a in audits:
        nn = (a.get("new_name") or "").strip()
        if nn and nn not in audit_by_new:
            audit_by_new[nn] = a

    items: List[ReviewItem] = []
    try:
        files = [p for p in review_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        for p in files[: max(1, min(int(limit), 200))]:
            a = audit_by_new.get(p.name)
            items.append(
                ReviewItem(
                    filename=p.name,
                    path=str(p),
                    mtime_ts=_iso_from_mtime(p.stat().st_mtime),
                    size_bytes=int(p.stat().st_size),
                    reason=(a.get("error_message") if a else None),
                    extracted_date=(a.get("extracted_date") if a else None),
                    sender=(a.get("sender") if a else None),
                    sender_confidence=(a.get("sender_confidence") if a else None),
                    audit_ts=(a.get("ts") if a else None),
                )
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Review list failed: {e}")

    return ReviewListResponse(items=items)


@app.get("/api/review/file/{filename}")
def api_review_file(filename: str):
    s = get_settings()
    if not s.review_dir:
        raise HTTPException(status_code=400, detail="review_dir not configured")

    review_dir = Path(s.review_dir)
    p = (review_dir / filename).resolve()

    if not ensure_within_root(env.media_root, p):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if p.suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Only PDF")

    return FileResponse(str(p), media_type="application/pdf", filename=p.name)


def _parse_override_date(s: str) -> tuple[int, int, Optional[int]]:
    raw = (s or "").strip()
    m1 = __import__("re").match(r"^(\d{4})-(\d{2})-(\d{2})$", raw)
    if m1:
        y, mo, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        # validate via datetime
        _ = datetime(y, mo, d)
        return y, mo, d

    m2 = __import__("re").match(r"^(\d{4})-(\d{2})$", raw)
    if m2:
        y, mo = int(m2.group(1)), int(m2.group(2))
        _ = datetime(y, mo, 1)
        return y, mo, None

    raise ValueError("override_date must be YYYY-MM or YYYY-MM-DD")


@app.post("/api/review/apply", response_model=ReviewApplyResponse)
def api_review_apply(req: ReviewApplyRequest):
    s = get_settings()
    if not (s.review_dir and s.output_dir):
        return ReviewApplyResponse(ok=False, message="review_dir/output_dir not configured")

    review_dir = Path(s.review_dir)
    output_dir = Path(s.output_dir)

    src = (review_dir / (req.filename or "")).resolve()
    if not ensure_within_root(env.media_root, src):
        return ReviewApplyResponse(ok=False, message="Invalid file path")
    if not src.exists() or not src.is_file() or src.suffix.lower() != ".pdf":
        return ReviewApplyResponse(ok=False, message="File not found (or not a PDF)")

    try:
        y, mo, d = _parse_override_date(req.override_date)
    except Exception as e:
        return ReviewApplyResponse(ok=False, message=str(e))

    year = f"{y:04d}"
    month = f"{mo:02d}"
    if d is None:
        date_part = f"{y:04d}-{mo:02d}"
    else:
        date_part = f"{y:04d}-{mo:02d}-{d:02d}"

    sender = (req.override_sender or "Dokument").strip() or "Dokument"

    target_dir = (output_dir / year / month)
    safe_mkdir(target_dir)

    stem = normalize_filename(f"{date_part}_{sender}")
    dst = (target_dir / f"{stem}.pdf")
    dup_n = 0
    if dst.exists():
        dst, dup_n = next_duplicate_path(dst)

    try:
        move_file(src, dst)
        db.add_audit(
            status="success",
            original_path=str(review_dir),
            original_name=req.filename,
            extracted_date=date_part,
            sender=sender,
            sender_confidence=None,
            target_path=str(dst.parent),
            new_name=dst.name,
            duplicate_n=(dup_n if dup_n else None),
            error_message="manual_apply",
        )
        return ReviewApplyResponse(ok=True, message="Moved.", new_path=str(dst))
    except Exception as e:
        db.add_audit(
            status="error",
            original_path=str(review_dir),
            original_name=req.filename,
            extracted_date=date_part,
            sender=sender,
            sender_confidence=None,
            target_path=str(target_dir),
            new_name=dst.name,
            duplicate_n=(dup_n if dup_n else None),
            error_message=f"manual_apply_failed:{e}",
        )
        return ReviewApplyResponse(ok=False, message=f"Move failed: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=env.app_port,
        reload=False,
    )
