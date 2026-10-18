"""The analysis pipeline:

decode with source timestamps → sample frames → detect candidate events →
consolidate repeated detections → select 1–3 evidence frames per event →
attach verified rule references → build explanations.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Callable

from app.pipeline.detectors import Detector
from app.pipeline.types import CandidateEvent, EvidenceFrame, Finding
from app.rules import Rulebook
from app.video import VideoInfo, frames_at, probe, sample


class Stage(str, Enum):
    PROBING = "probing"
    DETECTING = "detecting"
    EVIDENCE = "evidence"
    SAVING = "saving"


# Events of one type closer than this are treated as one continuous event.
MERGE_GAP_SEC = 1.5


def fmt_time(sec: float) -> str:
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:04.1f}"


def consolidate(events: list[CandidateEvent]) -> list[CandidateEvent]:
    """Merge overlapping/adjacent windows of the same type; keep distinct events apart."""
    merged: list[CandidateEvent] = []
    for ev in sorted(events, key=lambda e: (e.event_type, e.start_sec)):
        last = merged[-1] if merged else None
        if last and last.event_type == ev.event_type and ev.start_sec <= last.end_sec + MERGE_GAP_SEC:
            if ev.confidence > last.confidence:
                last.representative_sec = ev.representative_sec
                last.confidence = ev.confidence
            last.end_sec = max(last.end_sec, ev.end_sec)
            last.observed_facts += [f for f in ev.observed_facts if f not in last.observed_facts]
            last.visibility_limits += [f for f in ev.visibility_limits if f not in last.visibility_limits]
        else:
            merged.append(ev)
    return sorted(merged, key=lambda e: e.representative_sec)


def evidence_times(ev: CandidateEvent, duration: float) -> list[float]:
    """Representative moment first, then window start and end when they add context."""
    times = [ev.representative_sec]
    for t in (ev.start_sec, ev.end_sec):
        if all(abs(t - other) >= 0.5 for other in times):
            times.append(t)
    return [min(max(0.0, t), max(0.0, duration - 0.05)) for t in times[:3]]


class Pipeline:
    def __init__(self, detector: Detector, rulebook: Rulebook, sample_fps: float):
        self.detector = detector
        self.rulebook = rulebook
        self.sample_fps = sample_fps

    def analyze(
        self,
        video_path: Path,
        evidence_dir: Path,
        on_stage: Callable[[Stage, int], None] = lambda stage, progress: None,
    ) -> tuple[VideoInfo, list[Finding]]:
        on_stage(Stage.PROBING, 5)
        info = probe(video_path)

        on_stage(Stage.DETECTING, 15)
        if hasattr(self.detector, "analyze_video"):
            findings = self.detector.analyze_video(video_path, info, sample(video_path, self.sample_fps), evidence_dir)
            return info, findings
        candidates = self.detector.detect(info, sample(video_path, self.sample_fps))
        events = consolidate(candidates)

        on_stage(Stage.EVIDENCE, 80)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        findings: list[Finding] = []
        for index, ev in enumerate(events, start=1):
            event_id = f"e{index}"
            definition = self.rulebook.event(ev.event_type)
            frames = []
            for ts, image in frames_at(video_path, evidence_times(ev, info.duration_sec)):
                path = evidence_dir / f"{event_id}-{ts:08.3f}.jpg"
                image.convert("RGB").save(path, "JPEG", quality=85)
                frames.append(EvidenceFrame(ts, path))
            if not frames:
                raise RuntimeError(f"could not extract evidence frames for {event_id}")
            refs = self.rulebook.references(definition.rule_ids)
            explanation = " ".join(
                [
                    definition.explanation.format(
                        start=fmt_time(ev.start_sec), end=fmt_time(ev.end_sec)
                    ),
                    *ev.observed_facts,
                    "Требуется проверка человеком.",
                    self.rulebook.citation_text(refs),
                ]
            )
            findings.append(
                Finding(
                    event_id=event_id,
                    event_type=ev.event_type,
                    label=definition.label,
                    start_sec=ev.start_sec,
                    end_sec=ev.end_sec,
                    representative_sec=frames[0].timestamp_sec,
                    confidence=ev.confidence,
                    explanation=explanation,
                    rules=refs,
                    evidence=frames,
                )
            )
        return info, findings
