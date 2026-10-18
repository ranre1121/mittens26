"""Layout of media files on the local data volume.

data/
  app.sqlite3
  tmp/                                  partial uploads, never referenced by a job
  uploads/<job_id>/input.<ext>
  results/<job_id>/result.json
  evidence/<job_id>/<event_id>-<timestamp>.jpg
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path


class Storage:
    def __init__(self, data_dir: Path):
        self.root = data_dir

    def ensure(self) -> None:
        for name in ("tmp", "uploads", "results", "evidence"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    def input_path(self, job_id: str, ext: str) -> Path:
        return self.root / "uploads" / job_id / f"input{ext}"

    def result_path(self, job_id: str) -> Path:
        return self.root / "results" / job_id / "result.json"

    def evidence_dir(self, job_id: str) -> Path:
        return self.root / "evidence" / job_id

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def resolve(self, relative: str) -> Path:
        """Map a stored relative path back to disk, refusing anything outside the data dir."""
        path = (self.root / relative).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"path escapes data dir: {relative}")
        return path

    def finalize_upload(self, tmp_file: Path, job_id: str, ext: str) -> Path:
        """Move a fully received, validated upload into place. os.replace is
        atomic on the same filesystem, so a job never sees a half-written file."""
        target = self.input_path(job_id, ext)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp_file, target)
        return target

    def clear_attempt(self, job_id: str) -> None:
        """Remove artifacts of a previous (interrupted) attempt before retrying,
        so a result never mixes events from two attempts."""
        shutil.rmtree(self.evidence_dir(job_id), ignore_errors=True)
        shutil.rmtree(self.result_path(job_id).parent, ignore_errors=True)

    def write_json_atomic(self, path: Path, data: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
