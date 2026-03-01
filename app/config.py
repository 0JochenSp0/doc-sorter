# app/config.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

@dataclass(frozen=True)
class AppEnv:
    app_port: int
    db_path: Path
    media_root: Path

def get_env() -> AppEnv:
    port = int(os.getenv("APP_PORT", "5434"))
    db_path = Path(os.getenv("DB_PATH", "/data/app.db"))
    media_root = Path(os.getenv("MEDIA_ROOT", "/media"))
    return AppEnv(app_port=port, db_path=db_path, media_root=media_root)