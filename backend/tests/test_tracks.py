from app.pipeline.tracks import VisualHypotheses


def detection(box, kind='cash_stack'):
    return dict(box=box, class_name=kind, score=.9)


def test_unique_match_is_hypothesis_and_gap_breaks_identity():
    tracker = VisualHypotheses()
    box = detection([.1, .1, .5, .5])
    first = tracker.update(0, [box])[0]
    second = tracker.update(.5, [box])[0]
    assert first['display_id'] == second['display_id']
    assert second['identity_scope'] == 'visual_hypothesis_only'
    assert 'cash_lot_id' not in second
    assert tracker.update(3, [box])[0]['display_id'] != second['display_id']


def test_ambiguous_split_and_merge_break_identity():
    tracker = VisualHypotheses()
    box = detection([.1, .1, .5, .5])
    original = tracker.update(0, [box])[0]['display_id']
    split = tracker.update(.5, [box, box])
    assert original not in {x['display_id'] for x in split}
    assert len({x['display_id'] for x in split}) == 2
    merged = tracker.update(1, [box])[0]['display_id']
    assert merged not in {x['display_id'] for x in split}


def test_class_change_and_missing_frame_break_identity():
    tracker = VisualHypotheses()
    box = [.1, .1, .5, .5]
    a = tracker.update(0, [detection(box)])[0]['display_id']
    b = tracker.update(.5, [detection(box, 'document')])[0]['display_id']
    assert a != b
    tracker.update(1, [])
    assert tracker.update(1.5, [detection(box, 'document')])[0]['display_id'] != b
