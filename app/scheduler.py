# app/scheduler.py
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import DB
from .models import RunStatus, Settings
from .worker import process_inbox


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunnerState:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    running: bool = False
    last_run_started_at: Optional[str] = None
    last_run_finished_at: Optional[str] = None
    last_run_duration_sec: Optional[float] = None
    last_run_counts: Dict[str, int] = field(default_factory=dict)
    last_errors: List[str] = field(default_factory=list)

    # NEW:
    last_review_reasons: Dict[str, int] = field(default_factory=dict)
    last_review_samples: List[Dict[str, str]] = field(default_factory=list)


class AppScheduler:
    def __init__(self, db: DB, media_root, get_settings_callable, get_logger_callable):
        self.db = db
        self.media_root = media_root
        self.get_settings = get_settings_callable
        self.get_logger = get_logger_callable
        self.state = RunnerState()
        self._sched = BackgroundScheduler(timezone="Europe/Berlin")

    def start(self) -> None:
        trigger = CronTrigger(minute=0)
        self._sched.add_job(self._run_scheduled, trigger=trigger, id="hourly_run", replace_existing=True)
        self._sched.start()

    def shutdown(self) -> None:
        try:
            self._sched.shutdown(wait=False)
        except Exception:
            pass

    def status(self) -> RunStatus:
        return RunStatus(
            running=self.state.running,
            last_run_ts=self.state.last_run_finished_at,
            last_run_duration_ms=int((self.state.last_run_duration_sec or 0) * 1000) if self.state.last_run_duration_sec is not None else None,
            last_counts=self.state.last_run_counts,
            last_errors=self.state.last_errors[-10:],
            last_review_reasons=self.state.last_review_reasons,
            last_review_samples=self.state.last_review_samples,
        )

    def trigger_manual(self, reason: str = "manual") -> bool:
        try:
            self._sched.add_job(lambda: self._run_job(reason), id=f"manual_{time.time()}", replace_existing=False)
            return True
        except Exception:
            return False

    def _run_scheduled(self) -> None:
        self._run_job("scheduled")

    def _run_job(self, reason: str) -> None:
        if self.state.running:
            return

        self.state.running = True
        self.state.last_run_started_at = _utc_now_iso()
        self.state.last_errors = []
        self.state.last_review_reasons = {}
        self.state.last_review_samples = []

        logger = self.get_logger()
        settings: Settings = self.get_settings()

        run_id = self.db.start_run(reason=reason, counts_json=json.dumps({}), errors_json=json.dumps([]))
        t0 = time.time()
        try:
            res = process_inbox(db=self.db, settings=settings, media_root=self.media_root, logger=logger)
            dt = time.time() - t0
            self.state.last_run_duration_sec = dt
            self.state.last_run_counts = res.counts
            self.state.last_errors = res.errors
            self.state.last_review_reasons = getattr(res, "review_reasons", {}) or {}
            self.state.last_review_samples = getattr(res, "review_samples", []) or []

            self.db.finish_run(
                run_id=run_id,
                duration_sec=dt,
                counts_json=json.dumps(res.counts),
                errors_json=json.dumps(res.errors),
            )
        except Exception as e:
            dt = time.time() - t0
            self.state.last_run_duration_sec = dt
            self.state.last_run_counts = {"success": 0, "review": 0, "ignored": 0, "error": 1}
            self.state.last_errors = [str(e)]
            self.db.finish_run(
                run_id=run_id,
                duration_sec=dt,
                counts_json=json.dumps(self.state.last_run_counts),
                errors_json=json.dumps(self.state.last_errors),
            )
        finally:
            self.state.last_run_finished_at = _utc_now_iso()
            self.state.running = False
