# app/db.py
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Any, List


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    """
    settings: key/value
    runs: run bookkeeping (used by scheduler)
    audit: per-file audit records
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            c = conn.cursor()

            # settings KV
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            # runs (migration-safe)
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT,
                    finished_at TEXT,
                    reason TEXT,
                    duration_sec REAL,
                    counts_json TEXT,
                    errors_json TEXT
                )
                """
            )

            # audit
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    status TEXT NOT NULL,
                    original_path TEXT,
                    original_name TEXT,
                    extracted_date TEXT,
                    sender TEXT,
                    sender_confidence REAL,
                    target_path TEXT,
                    new_name TEXT,
                    duplicate_n INTEGER,
                    error_message TEXT
                )
                """
            )

            conn.commit()

    # ----------------------------
    # settings KV (compat main.py)
    # ----------------------------

    def get_setting(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (str(key), "" if value is None else str(value)),
            )
            conn.commit()

    def get_all_settings(self) -> Dict[str, str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {str(r["key"]): str(r["value"]) for r in rows}

    def set_all_settings(self, data: Dict[str, Any]) -> None:
        if not data:
            return
        with self._connect() as conn:
            for k, v in data.items():
                if k is None:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (str(k), "" if v is None else str(v)),
                )
            conn.commit()

    # ----------------------------
    # runs (used by scheduler.py)
    # ----------------------------

    def start_run(self, *, reason: str, counts_json: str, errors_json: str) -> int:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO runs (started_at, reason, counts_json, errors_json)
                VALUES (?, ?, ?, ?)
                """,
                (_utc_now_iso(), reason, counts_json, errors_json),
            )
            conn.commit()
            return int(cur.lastrowid)

    def finish_run(self, *, run_id: int, duration_sec: float, counts_json: str, errors_json: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET finished_at = ?, duration_sec = ?, counts_json = ?, errors_json = ?
                WHERE id = ?
                """,
                (_utc_now_iso(), float(duration_sec), counts_json, errors_json, int(run_id)),
            )
            conn.commit()

    # ----------------------------
    # audit (used by worker.py)
    # ----------------------------

    def add_audit(
        self,
        *,
        status: str,
        original_path: Optional[str],
        original_name: Optional[str],
        extracted_date: Optional[str],
        sender: Optional[str],
        sender_confidence: Optional[float],
        target_path: Optional[str],
        new_name: Optional[str],
        duplicate_n: Optional[int],
        error_message: Optional[str],
        ts: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit (ts, status, original_path, original_name, extracted_date, sender, sender_confidence,
                                   target_path, new_name, duplicate_n, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts or _utc_now_iso(),
                    status,
                    original_path,
                    original_name,
                    extracted_date,
                    sender,
                    sender_confidence,
                    target_path,
                    new_name,
                    duplicate_n,
                    error_message,
                ),
            )
            conn.commit()

    # ----------------------------
    # review helpers (UI)
    # ----------------------------

    def list_recent_review_audits(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Returns newest review audit rows first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, status, original_name, extracted_date, sender, sender_confidence, new_name, error_message
                FROM audit
                WHERE status = 'review'
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "ts": r["ts"],
                    "original_name": r["original_name"],
                    "new_name": r["new_name"],
                    "extracted_date": r["extracted_date"],
                    "sender": r["sender"],
                    "sender_confidence": r["sender_confidence"],
                    "error_message": r["error_message"],
                }
            )
        return out
