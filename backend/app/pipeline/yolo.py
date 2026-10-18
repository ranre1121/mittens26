"""Optional accepted detector adapter; never load a client-supplied checkpoint."""
import json
import os
from pathlib import Path
from app.pipeline.providers import file_hash

# Fixed accepted cash/person checkpoints. Unknown pickle-based .pt files rejected
# BEFORE importing ultralytics or deserializing anything.
ACCEPTED = {
    'expanded182-person': '808715318da18d74180b990753b3f0b8fca26c1f4db1e7f2ad18c329e173a1c9',
    'expanded182-specialist': '5877295ace84457b11ac9cc616ab6154cb3f8c84bf5ad73296a7d9bd1c902532',
}


class YoloAdapter:
    def __init__(self, config):
        path = Path(config).resolve(strict=True)
        cfg = json.loads(path.read_text())
        if cfg.get('format') != 'datathon-detector-ensemble-v1' or len(cfg.get('models', [])) != 2:
            raise ValueError('Expected accepted two-model ensemble')
        verified = []
        for item in cfg['models']:
            expected = ACCEPTED.get(item['id'])
            weights = (path.parent / item['weights']).resolve(strict=True)
            if not expected or item.get('weights_sha256') != expected or file_hash(weights) != expected:
                raise ValueError('Untrusted YOLO checkpoint rejected before loading')
            verified.append((item, weights))
        if {x['id'] for x, _ in verified} != set(ACCEPTED):
            raise ValueError('Missing accepted detector')
        os.environ.update(YOLO_OFFLINE='true', YOLO_AUTOINSTALL='false', YOLO_VERBOSE='false')
        from ultralytics import YOLO
        self.models = [(item, YOLO(str(weights), task='detect')) for item, weights in verified]
        self.provenance = {'config_sha256': file_hash(path), 'models': ACCEPTED, 'independent_validation': False, 'scope': '182 training images; no true independent accuracy'}

    def detect(self, image):
        boxes = []
        for cfg, model in self.models:
            result = model.predict(image, device='cpu', verbose=False, save=False, imgsz=cfg.get('imgsz', 640), conf=.25, iou=cfg['nms_iou'])[0]
            for box, cls, score in zip(result.boxes.xyxyn.tolist(), result.boxes.cls.tolist(), result.boxes.conf.tolist()):
                name = result.names[int(cls)]
                if name in cfg['class_map']:
                    boxes.append({'class_name': cfg['class_map'][name], 'box': box, 'score': score, 'detector_id': cfg['id']})
        return boxes
