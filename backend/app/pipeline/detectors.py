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



def build_detector(settings: Settings) -> Detector:
    from app.pipeline.inference import InferenceDetector
    return InferenceDetector(settings)
