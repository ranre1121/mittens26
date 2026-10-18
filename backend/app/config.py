"""Runtime settings, read from environment variables.

The API and the worker read the same variables, so both processes agree on
where the database, media files, rule registry and frontend build live.
"""

from __future__ import annotations

import os
import json
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
    # Legacy setting: only none is accepted; demo events are retired.
    stub_mode: str
    # Attach rule references whose registry entry is not yet verified.
    allow_unverified_rules: bool

    qwen_provider: str = "mock"
    qwen_command: tuple[str, ...] = ()
    qwen_model_id: str = "qwen-stub"
    qwen_model_version: str = "mock-v1"
    environment: str = "development"
    job_timeout_seconds: float = 120
    max_video_seconds: float = 1800
    max_video_pixels: int = 3840 * 2160
    max_pending_jobs: int = 20
    yolo_config: Path | None = None
    yolo_device: str = "cpu"

    def __post_init__(self):
        import math
        if self.yolo_device not in {"cpu", "cuda", "cuda:0"}:
            raise ValueError("YOLO_DEVICE must be cpu, cuda or cuda:0")
        if self.qwen_provider not in {"mock", "command"}:
            raise ValueError("QWEN_PROVIDER must be mock or command")
        if self.environment not in {"development", "production", "test"}:
            raise ValueError("APP_ENV must be development, test or production")
        if any(not isinstance(x, str) or not x for x in self.qwen_command):
            raise ValueError("QWEN_COMMAND_JSON must contain nonempty string arguments")
        if self.environment == "production" and self.qwen_provider == "mock":
            raise ValueError("Mock Qwen is forbidden in production")
        if self.qwen_provider == "command" and (not self.qwen_command or not Path(self.qwen_command[0]).is_absolute()):
            raise ValueError("QWEN_COMMAND_JSON requires an absolute executable path")
        if self.qwen_provider == "command" and not os.access(self.qwen_command[0], os.X_OK):
            raise ValueError("Qwen executable is missing or not executable")
        if self.stub_mode != "none":
            raise ValueError("STUB_MODE=demo is retired: synthetic compliance events are forbidden")
        for value in (self.sample_fps, self.job_timeout_seconds, self.max_video_seconds, self.worker_poll_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Runtime limits must be finite and positive")
        if not 0 < self.sample_fps <= 30 or min(self.max_upload_bytes, self.max_pending_jobs, self.max_video_pixels, self.max_attempts) <= 0:
            raise ValueError("Invalid resource limits")
        if not self.qwen_model_id or not self.qwen_model_version:
            raise ValueError("Model ID and version required")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.sqlite3"


def load_settings() -> Settings:
    return Settings(
        yolo_device=os.environ.get("YOLO_DEVICE", "cpu"),
        qwen_provider=os.environ.get("QWEN_PROVIDER", "mock"),
        qwen_command=tuple(json.loads(os.environ.get("QWEN_COMMAND_JSON", "[]"))),
        qwen_model_id=os.environ.get("QWEN_MODEL_ID", "qwen-stub"),
        qwen_model_version=os.environ.get("QWEN_MODEL_VERSION", "mock-v1"),
        environment=os.environ.get("APP_ENV", "development"),
        job_timeout_seconds=float(os.environ.get("JOB_TIMEOUT_SECONDS", "120")),
        max_video_seconds=float(os.environ.get("MAX_VIDEO_SECONDS", "1800")),
        max_pending_jobs=int(os.environ.get("MAX_PENDING_JOBS", "20")),
        yolo_config=Path(os.environ["YOLO_CONFIG"]).resolve() if os.environ.get("YOLO_CONFIG") else None,
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
