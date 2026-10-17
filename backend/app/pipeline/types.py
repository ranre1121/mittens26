from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.rules import RuleRef


@dataclass
class CandidateEvent:
    """Common output of every detector. `confidence` is an internal score,
    not a calibrated probability unless calibration has been evaluated."""

    event_type: str
    start_sec: float
    end_sec: float
    representative_sec: float
    confidence: float
    observed_facts: list[str] = field(default_factory=list)
    visibility_limits: list[str] = field(default_factory=list)


@dataclass
class EvidenceFrame:
    timestamp_sec: float
    path: Path


@dataclass
class Finding:
    event_id: str
    event_type: str
    label: str
    start_sec: float
    end_sec: float
    representative_sec: float
    confidence: float
    explanation: str
    rules: list[RuleRef]
    evidence: list[EvidenceFrame]
