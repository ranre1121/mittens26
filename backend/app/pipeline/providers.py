"""Explicit stub or operator-configured local Qwen process. No weight deserialization.

Command contract: one request JSON on stdin; one response JSON on stdout.
The operator owns the executable/runtime. No shell, auto-download or fallback.
"""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile

from app.cashrules.contracts import EVENT_TYPES


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def capabilities(settings):
    mock = settings.qwen_provider == 'mock'
    return {
        'provider': settings.qwen_provider, 'model_id': settings.qwen_model_id,
        'model_version': settings.qwen_model_version, 'is_mock': mock,
        'status': 'mock_not_evidence' if mock else 'configured_requires_runtime_verification',
        'semantic_accuracy_validated': False, 'equipment_context_enabled': False,
        'yolo': 'configured_cpu_only' if settings.yolo_config else 'disabled',
        'rules': 'cashrules.sequence.v1; candidate evidence cannot self-confirm',
    }


class QwenProvider:
    def __init__(self, settings):
        self.settings = settings

    def infer(self, request):
        cfg = self.settings
        if cfg.qwen_provider == 'mock':
            return {'events': [], 'checklist': [], 'is_mock': True, 'limitations': ['Stub provider: no Qwen weights loaded; no semantic evidence produced.']}
        # Temporary files bound memory even if a broken executable floods stdout.
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            import sys
            process = subprocess.Popen([sys.executable, "-m", "app.pipeline.provider_exec", *cfg.qwen_command], stdin=subprocess.PIPE, stdout=out, stderr=err)
            try:
                process.communicate(json.dumps(request, allow_nan=False).encode(), timeout=cfg.job_timeout_seconds * .8)
            except BaseException:
                process.kill()
                process.wait()
                raise
            if process.returncode:
                raise RuntimeError('Qwen provider failed (stderr withheld from public results)')
            if out.tell() > 2 * 1024 * 1024:
                raise ValueError('Qwen response exceeds 2 MiB')
            out.seek(0)
            response = json.load(out)
        required = {'schema_version', 'model_id', 'model_version', 'events'}
        if not isinstance(response, dict) or not required <= set(response) <= required | {'checklist'}:
            raise ValueError('Invalid Qwen response fields')
        if (response['schema_version'], response['model_id'], response['model_version']) != ('datathon.qwen.v1', cfg.qwen_model_id, cfg.qwen_model_version):
            raise ValueError('Qwen model/version contract mismatch')
        events = response['events']
        if not isinstance(events, list) or len(events) > 200:
            raise ValueError('Qwen events must be a list of at most 200 items')
        for event in events:
            if not isinstance(event, dict) or set(event) != {'event_type', 'start_s', 'end_s', 'frame_refs', 'entity_ids', 'data'}:
                raise ValueError('Invalid event fields')
            entities = event['entity_ids']
            if not isinstance(entities, list) or not entities or any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in entities) or len(set(entities)) != len(entities):
                raise ValueError('Explicit pseudonymous subject/object hypotheses required')
            a, b = event['start_s'], event['end_s']
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, b)) or not 0 <= a <= b <= request['duration_sec']:
                raise ValueError('Invalid event interval')
            if event['event_type'] not in EVENT_TYPES:
                raise ValueError('Unknown event type')
            refs = event['frame_refs']
            available = {f['frame_ref']: f['timestamp_s'] for f in request['frames']}
            if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or x not in available for x in refs):
                raise ValueError('Event must cite sampled source frames')
            if not min(available[x] for x in refs) <= a <= b <= max(available[x] for x in refs):
                raise ValueError('Event interval must be bracketed by cited frame timestamps')
        checklist = validate_checklist(response.get('checklist', []), request)
        return {'events': events, 'checklist': checklist, 'is_mock': False, 'limitations': ['Model observations require reviewed identity, transaction linkage and coverage before rules can support a finding.']}



def validate_checklist(rows, request):
    """Per-window visual checklist from qwen_runner.checklist: values are
    true/false/null and cited frames must be frames the model was given."""
    if not isinstance(rows, list) or len(rows) > 200:
        raise ValueError('Checklist must be a list of at most 200 windows')
    available = {f['frame_ref'] for f in request['frames']}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('items'), dict):
            raise ValueError('Invalid checklist window')
        a, b = row.get('start_s'), row.get('end_s')
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, b)) or not 0 <= a <= b <= request['duration_sec'] + 1:
            raise ValueError('Invalid checklist window interval')
        for item in row['items'].values():
            if not isinstance(item, dict) or item.get('value') not in (True, False, None):
                raise ValueError('Invalid checklist value')
            if item.get('frame_ref') is not None and item['frame_ref'] not in available:
                raise ValueError('Checklist cites a frame that was not supplied')
            if not isinstance(item.get('note', ''), str) or len(item.get('note', '')) > 300:
                raise ValueError('Invalid checklist note')
    return rows
