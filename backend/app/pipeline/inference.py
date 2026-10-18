"""Inference observations and evidence-gated deterministic decisions."""
import json
from pathlib import Path
from app.cashrules import evaluate
from app.cashrules.contracts import Sequence
from app.pipeline.providers import QwenProvider, capabilities, file_hash
from app.pipeline.tracks import VisualHypotheses


class InferenceDetector:
    def __init__(self, settings):
        self.settings = settings
        self.name = 'qwen:' + settings.qwen_provider
        self.provider = QwenProvider(settings)
        self.last_analysis = {}

    def analyze_video(self, path, info, samples, evidence_dir):
        settings = self.settings
        if info.duration_sec > settings.max_video_seconds or info.width * info.height > settings.max_video_pixels:
            raise ValueError('Video exceeds configured duration or pixel limits')
        evidence_dir.mkdir(parents=True, exist_ok=True)
        detector = None
        if settings.yolo_config:
            from app.pipeline.yolo import YoloAdapter
            detector = YoloAdapter(settings.yolo_config, settings.yolo_device)
        frames = []
        hypotheses = VisualHypotheses()
        # Bounded samples; JPEGs are private and served only through authenticated routes.
        for n, (ts, image) in enumerate(samples):
            if n >= 3600:
                raise ValueError('Video exceeds 3600 sampled frames')
            target = evidence_dir / f'e0-{ts:08.3f}.jpg'
            image.thumbnail((960, 960))
            image.convert('RGB').save(target, 'JPEG', quality=80)
            detections = detector.detect(image) if detector else []
            frames.append({'frame_ref': target.name, 'timestamp_s': ts,
                           'path': str(target.resolve()),
                           'detections': detections, 'objects': hypotheses.update(ts, detections)})
        if not frames:
            raise ValueError('No timestamped frames decoded')
        digest = file_hash(path)
        request = {'schema_version': 'datathon.qwen.request.v1', 'video_path': str(path.resolve()),
                   'video_sha256': digest, 'duration_sec': info.duration_sec, 'frames': frames,
                   'source_start_s': info.source_start_s,
                   'equipment_context_enabled': False}
        yolo_provenance = detector.provenance if detector else None
        if detector is not None:
            detector = None
            import gc
            gc.collect()
            if settings.yolo_device.startswith('cuda'):
                import torch
                torch.cuda.empty_cache()
        response = self.provider.infer(request)
        sequence = {'schema_version': 'cashrules.sequence.v1', 'synthetic': False, 'video_sha256': digest,
                    'events': [], 'evidence': [], 'transactions': [], 'coverage': [], 'facts': []}
        timestamps = {f['frame_ref']: f['timestamp_s'] for f in frames}
        for n, event in enumerate(response['events']):
            ident = f'q{n}'
            sequence['events'].append({'event_id': ident, 'event_type': event['event_type'],
                'start_s': event['start_s'], 'end_s': event['end_s'], 'entity_ids': event['entity_ids'], 'evidence_ids': [ident],
                'identity': 'hypothesis', 'visibility': 'partial', 'data': event['data']})
            sequence['evidence'].append({'evidence_id': ident, 'source_type': 'model', 'review_status': 'candidate',
                'source_pointer': 'uploaded-video', 'source_sha256': digest,
                'refs': [{'frame_ref': x, 'timestamp_s': timestamps[x]} for x in dict.fromkeys(event['frame_refs'])]})
        rules = evaluate(Sequence.parse(sequence), json.loads((settings.rules_dir / 'cashrules-catalog.json').read_text()))
        self.last_analysis = {'models': capabilities(settings), 'is_mock': response['is_mock'],
            'limitations': response['limitations'], 'rule_evaluation': rules, 'events': sequence['events'],
            'evidence': sequence['evidence'], 'video_sha256': digest,
            'source_start_s': info.source_start_s, 'timestamp_basis': 'source_presentation_time',
            'frames': [{'frame_ref': f['frame_ref'], 'timestamp_s': f['timestamp_s']} for f in frames],
            'yolo_provenance': yolo_provenance,
            'qwen_provenance': response.get('provenance'),
            'qwen_windows': response.get('windows', []),
            'annotated_output': None}
        if response.get('provenance', {}).get('actual_inference') is True:
            self.last_analysis['models']['status'] = 'inference_completed'
        # Only reviewed rule candidates belong in legacy violation findings.
        return []
