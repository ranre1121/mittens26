"""Conservative visual associations; these IDs never establish physical identity."""
import math


def iou(a, b):
    area = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - area
    return area / union if union > 0 else 0


class VisualHypotheses:
    def __init__(self, max_gap_seconds=1.0):
        self.previous = []
        self.last_time = None
        self.next_id = 0
        self.max_gap = max_gap_seconds

    def update(self, timestamp, detections):
        if not math.isfinite(timestamp) or (self.last_time is not None and timestamp <= self.last_time):
            raise ValueError('Visual hypotheses require increasing source timestamps')
        previous = self.previous if self.last_time is not None and timestamp - self.last_time <= self.max_gap else []
        if len(detections) > 128:
            raise ValueError('At most 128 detections per source frame are supported')
        edges = {(i, j): iou(old['box'], new['box']) for i, old in enumerate(previous)
                 for j, new in enumerate(detections) if old['class_name'] == new['class_name']}
        edges = {key: value for key, value in edges.items() if value >= .3}
        objects = []
        for j, detection in enumerate(detections):
            candidates = [(i, score) for (i, k), score in edges.items() if k == j]
            matched = None
            if len(candidates) == 1:
                i, score = candidates[0]
                if sum(key[0] == i for key in edges) == 1:
                    matched = (previous[i]['track_id'], score)
            if matched is None:
                self.next_id += 1
                matched = (f'visual-{self.next_id:06d}', 0.0)
            identity, score = matched
            objects.append({**detection, 'display_id': identity, 'track_id': identity,
                'association_confidence': score, 'identity_scope': 'visual_hypothesis_only',
                'identity_uncertainty': ['Unreviewed IoU association; not permanent physical identity',
                                         'Ambiguity or a sampling gap starts a new hypothesis']})
        self.previous, self.last_time = objects, timestamp
        return objects
