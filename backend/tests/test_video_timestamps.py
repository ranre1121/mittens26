from fractions import Fraction

import av
from PIL import Image
import pytest

from app.pipeline.inference import InferenceDetector
from app.pipeline.providers import QwenProvider
from app.pipeline.qwen_runtime import build_windows
from app.pipeline.run import Pipeline
from app.rules import Rulebook
from app.video import probe, sample


@pytest.fixture
def offset_video(tmp_path):
    path = tmp_path / "offset.mp4"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = stream.height = 64
        stream.pix_fmt = "yuv420p"
        for index in range(10):
            frame = av.VideoFrame.from_image(Image.new("RGB", (64, 64), (index * 20, 0, 0)))
            frame.pts = 50 + index
            frame.time_base = Fraction(1, 10)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def test_probe_and_sample_preserve_nonzero_source_pts(offset_video):
    info = probe(offset_video)
    assert info.duration_sec == pytest.approx(1)
    assert info.source_start_s == pytest.approx(5)
    assert [timestamp for timestamp, _ in sample(offset_video, 2)] == pytest.approx([5, 5.5])


def test_offset_video_pipeline_and_qwen_windows_share_source_timeline(offset_video, make_settings, monkeypatch):
    settings = make_settings(qwen_provider="mock", sample_fps=2)
    detector = InferenceDetector(settings)
    pipeline = Pipeline(detector, Rulebook(settings.rules_dir, False), settings.sample_fps)
    captured = {}
    original_infer = QwenProvider.infer

    def capture_request(provider, request):
        captured.update(request)
        return original_infer(provider, request)

    monkeypatch.setattr(QwenProvider, "infer", capture_request)
    evidence_dir = settings.data_dir / "evidence" / "offset"
    info, findings = pipeline.analyze(offset_video, evidence_dir)
    analysis = detector.last_analysis
    assert findings == [] and analysis["is_mock"] is True
    assert info.source_start_s == analysis["source_start_s"] == captured["source_start_s"] == 5
    assert analysis["frames"][0]["timestamp_s"] == 5
    assert analysis["frames"][0]["frame_ref"] == "e0-0005.000.jpg"
    assert (evidence_dir / analysis["frames"][0]["frame_ref"]).is_file()
    assert captured["duration_sec"] == 1
    windows, coverage = build_windows(captured, settings)
    assert windows[0]["start_s"] == 5
    assert windows[0]["end_s"] == 5.5
    assert coverage["analyzed_frames"] == 2
    captured["frames"][-1]["timestamp_s"] = 6.5
    with pytest.raises(ValueError, match="source PTS"):
        build_windows(captured, settings)
