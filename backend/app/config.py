"""Runtime settings, read from environment variables.

The API and the worker read the same variables, so both processes agree on
where the database, media files, rule registry and frontend build live.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).resolve() if value else default


def _list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    rules_dir: Path
    models_dir: Path
    frontend_dir: Path
    # Keys accepted in X-API-Key by the jury API. Empty means every request is rejected.
    api_keys: tuple[str, ...]
    max_upload_bytes: int
    allowed_extensions: tuple[str, ...]
    # Analysis attempts per job before it is marked failed (restart recovery).
    max_attempts: int
    worker_poll_seconds: float
    # Frames per second the pipeline samples for detection.
    sample_fps: float
    # "none": stub detector reports nothing. "demo": one synthetic event per
    # video, to exercise evidence extraction and the result format end to end.
    stub_mode: str
    # Attach rule references whose registry entry is not yet verified.
    allow_unverified_rules: bool

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.sqlite3"


def load_settings() -> Settings:
    return Settings(
        data_dir=_path("DATA_DIR", REPO_ROOT / "data"),
        rules_dir=_path("RULES_DIR", REPO_ROOT / "rules"),
        models_dir=_path("MODELS_DIR", REPO_ROOT / "models"),
        frontend_dir=_path("FRONTEND_DIR", REPO_ROOT / "frontend" / "out"),
        api_keys=_list("API_KEYS"),
        max_upload_bytes=int(float(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024),
        allowed_extensions=_list("ALLOWED_EXTENSIONS") or (".mov", ".mp4", ".m4v"),
        max_attempts=int(os.environ.get("MAX_ATTEMPTS", "3")),
        worker_poll_seconds=float(os.environ.get("WORKER_POLL_SECONDS", "1.0")),
        sample_fps=float(os.environ.get("SAMPLE_FPS", "2")),
        stub_mode=os.environ.get("STUB_MODE", "none"),
        allow_unverified_rules=os.environ.get("ALLOW_UNVERIFIED_RULES", "false").lower()
        in ("1", "true", "yes"),
    )
