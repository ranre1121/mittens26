from __future__ import annotations

import dataclasses
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import REPO_ROOT, load_settings
from app.worker import Worker

API_KEY = "test-key"


def make_video(path: Path, seconds: float = 4.0, fps: int = 10, size=(160, 120)) -> Path:
    """Write a small .mov whose frames change colour over time."""
    with av.open(str(path), "w", format="mov") as container:
        stream = container.add_stream("mpeg4", rate=fps)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, fps)
        for i in range(int(seconds * fps)):
            img = np.zeros((size[1], size[0], 3), dtype=np.uint8)
            img[..., 0] = (i * 7) % 255
            img[..., 1] = 255 - (i * 5) % 255
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts = i
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.fixture
def video(tmp_path) -> Path:
    return make_video(tmp_path / "clip.mov")


@pytest.fixture
def make_settings(tmp_path):
    def _make(**overrides):
        values = {
            "data_dir": tmp_path / "data",
            "rules_dir": REPO_ROOT / "rules",
            "frontend_dir": tmp_path / "no-frontend",
            "api_keys": (API_KEY,),
            "stub_mode": "none",
            "allow_unverified_rules": False,
        }
        return dataclasses.replace(load_settings(), **{**values, **overrides})

    return _make


@pytest.fixture
def env(make_settings):
    """A client and a worker sharing one data dir, like the two deployed processes."""

    def _env(**overrides):
        settings = make_settings(**overrides)
        client = TestClient(create_app(settings))
        client.__enter__()  # run lifespan: create dirs and schema
        return client, Worker(settings), settings

    return _env
