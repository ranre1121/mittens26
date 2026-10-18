from app.config import REPO_ROOT
from app.pipeline.run import consolidate, evidence_times
from app.pipeline.types import CandidateEvent
from app.rules import Rulebook
from app.video import frames_at, probe, sample


def ev(t, start, end, conf=0.5, kind="cash_left_unattended"):
    return CandidateEvent(kind, start, end, t, conf)


def test_consolidate_merges_adjacent_same_type_only():
    merged = consolidate(
        [
            ev(2, 1, 3, 0.4),
            ev(4, 3.5, 5, 0.9),  # within the merge gap: same event
            ev(20, 19, 21),  # far apart: a distinct event
            ev(4, 3, 5, kind="safe_left_open"),  # different type: kept separate
        ]
    )
    assert [(e.event_type, e.start_sec, e.end_sec, e.representative_sec) for e in merged] == [
        ("cash_left_unattended", 1, 5, 4),
        ("safe_left_open", 3, 5, 4),
        ("cash_left_unattended", 19, 21, 20),
    ]


def test_evidence_times_are_unique_bounded_and_at_most_three():
    assert evidence_times(ev(5, 4.8, 9), 10) == [5, 9]
    assert evidence_times(ev(5, 0, 12), 10) == [5, 0, 9.95]


def test_video_probe_sample_and_seek(video):
    info = probe(video)
    assert 3.5 < info.duration_sec <= 4.1 and (info.width, info.height) == (160, 120)
    times = [t for t, _ in sample(video, fps=2)]
    assert len(times) >= 7 and all(b - a >= 0.49 for a, b in zip(times, times[1:]))
    [(t, img)] = frames_at(video, [2.0])
    assert abs(t - 2.0) < 0.11 and img.size == (160, 120)


def test_vocabulary_and_registry_are_consistent():
    book = Rulebook(REPO_ROOT / "rules")
    assert book.event("previous_client_cash_exposed").rule_ids == ("nbrk231_p20",)
    # Rules checked against the team's regulatory research are cited.
    ref = book.references(["nbrk231_p20"])[0]
    assert ref.verified and ref.provision == "п. 20" and ref.url.startswith("https://adilet.zan.kz/")
