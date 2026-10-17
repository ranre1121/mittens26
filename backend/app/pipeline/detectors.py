"""Detectors turn sampled frames into candidate events.

A real detector loads its model weights once, in __init__, inside the worker
process. The API process never constructs a detector, so adding web workers
never loads extra model copies onto the GPU.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from PIL import Image

from app.config import Settings
from app.pipeline.types import CandidateEvent
from app.video import VideoInfo


class Detector(Protocol):
    name: str

    def detect(
        self, info: VideoInfo, samples: Iterable[tuple[float, Image.Image]]
    ) -> list[CandidateEvent]: ...


class StubDetector:
    """Placeholder until a local model is integrated (build-order step 4).

    mode="none" reports no events. mode="demo" reports one synthetic event in
    the middle of the clip so evidence extraction, rule mapping and the result
    format can be tested end to end. Never run "demo" for judging.
    """

    def __init__(self, mode: str = "none"):
        if mode not in ("none", "demo"):
            raise ValueError(f"unknown stub mode {mode!r}")
        self.mode = mode
        self.name = f"stub:{mode}"

    def detect(self, info, samples):
        # Consume the samples so decoding cost is representative of a real run.
        for _ in samples:
            pass
        if self.mode == "none":
            return []
        mid = info.duration_sec / 2
        return [
            CandidateEvent(
                event_type="cash_left_unattended",
                start_sec=max(0.0, mid - 1.0),
                end_sec=min(info.duration_sec, mid + 1.0),
                representative_sec=mid,
                confidence=0.0,
                observed_facts=["Синтетическое событие тестового детектора (STUB_MODE=demo)."],
                visibility_limits=["Детектор-заглушка не анализирует содержимое кадров."],
            )
        ]


def build_detector(settings: Settings) -> Detector:
    return StubDetector(settings.stub_mode)
