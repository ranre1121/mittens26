import dataclasses
import json
import sys
import time
import pytest
from app.pipeline.providers import QwenProvider
from app.pipeline.yolo import YoloAdapter
from app.storage import Storage
from app.worker import Worker
from tests.test_api import upload


def command_settings(make_settings, tmp_path, body, **kwargs):
    script = tmp_path / 'provider.py'
    script.write_text(body)
    return make_settings(qwen_provider='command', qwen_command=(sys.executable, str(script)),
                         qwen_model_id='test-qwen', qwen_model_version='test-v1', **kwargs)


def response_script(events='[]', version='test-v1'):
    return f'''import json,sys
request=json.load(sys.stdin)
json.dump({{'schema_version':'datathon.qwen.v1','model_id':'test-qwen','model_version':{version!r},'events':{events}}},sys.stdout)
'''


def test_command_provider_produces_candidate_observations_not_violation(env, make_settings, tmp_path, video):
    script = tmp_path / 'provider.py'
    script.write_text(response_script("[{'event_type':'manual_cash_handling','entity_ids':['cash-hypothesis-1'],'data':{},'start_s':request['frames'][0]['timestamp_s'],'end_s':request['frames'][1]['timestamp_s'],'frame_refs':[x['frame_ref'] for x in request['frames'][:2]]}]"))
    client, worker, _ = env(qwen_provider='command', qwen_command=(sys.executable, str(script)), qwen_model_id='test-qwen', qwen_model_version='test-v1')
    job_id = upload(client, video).json()['job_id']
    assert worker.run_once()
    data = client.get(f'/api/v1/jobs/{job_id}/results').json()
    assert data['verdict'] == 'no_violation' and data['violations'] == []
    analysis = data['analysis']
    assert not analysis['is_mock']
    assert analysis['models']['model_id'] == 'test-qwen'
    assert analysis['events'][0]['event_type'] == 'manual_cash_handling'
    proof = analysis['evidence'][0]
    assert proof['source_type'] == 'model' and proof['review_status'] == 'candidate'
    assert proof['source_sha256'] == analysis['video_sha256']
    assert all('path' not in f for f in analysis['frames'])


@pytest.mark.parametrize('events', ["[{'event_type':'cash_bypass','start_s':0,'end_s':99,'frame_refs':['f']}]", "[{'event_type':'cash_bypass','start_s':0,'end_s':0,'frame_refs':['unknown']}]", "[{'event_type':'cash_bypass','start_s':float('nan'),'end_s':0,'frame_refs':['f']}]", "[{'event_type':'confirmed_legal_guilt','start_s':0,'end_s':0,'frame_refs':['f']}]"])
def test_invalid_provider_evidence_is_rejected(make_settings, tmp_path, events):
    settings = command_settings(make_settings, tmp_path, response_script(events))
    with pytest.raises(ValueError):
        QwenProvider(settings).infer({'duration_sec':4, 'frames':[{'frame_ref':'f','timestamp_s':0}]})


def test_wrong_model_version_fails_without_mock_fallback(env, tmp_path, video):
    script = tmp_path / 'bad.py'; script.write_text(response_script(version='wrong'))
    client, worker, _ = env(qwen_provider='command', qwen_command=(sys.executable,str(script)))
    job_id = upload(client, video).json()['job_id']
    worker.run_once()
    body = client.get(f'/api/v1/jobs/{job_id}').json()
    assert body['status'] == 'failed' and 'verdict' not in body
    assert str(tmp_path) not in body['error']


def test_hard_job_timeout_and_next_job_recovery(env, tmp_path, video):
    script = tmp_path / 'slow.py'; script.write_text('import time\ntime.sleep(60)\n')
    client, worker, _ = env(qwen_provider='command', qwen_command=(sys.executable,str(script)), job_timeout_seconds=.05)
    job_id = upload(client, video).json()['job_id']
    start = time.monotonic(); worker.run_once()
    assert time.monotonic() - start < 5
    assert client.get(f'/api/v1/jobs/{job_id}').json()['status'] == 'failed'
    assert not worker.run_once()


def test_readiness_requires_worker_and_models_are_explicit(env):
    client, worker, _ = env()
    assert client.get('/api/health').json() == {'ok':True}
    assert client.get('/api/ready').status_code == 503
    worker.run_once()
    body = client.get('/api/ready').json()
    assert body['ready'] and body['models']['is_mock']
    assert body['models']['equipment_context_enabled'] is False


def test_worker_concurrency_is_bounded(env):
    _, worker, settings = env()
    other = Worker(settings)
    with worker.exclusive():
        with pytest.raises(RuntimeError, match='Another inference worker'):
            other.run_once()


def test_production_mock_and_legacy_demo_are_rejected(make_settings):
    with pytest.raises(ValueError, match='production'):
        make_settings(environment='production')
    with pytest.raises(ValueError, match='retired'):
        make_settings(stub_mode='demo')


def test_type_size_duration_queue_and_storage_safety(env, video, tmp_path):
    client, _, _ = env(max_upload_bytes=10)
    assert upload(client, video).status_code == 413
    client, _, _ = env(max_video_seconds=1)
    assert upload(client, video).status_code == 400
    client, _, _ = env(max_pending_jobs=1)
    assert upload(client, video).status_code == 200
    assert upload(client, video).status_code == 429
    with open(video, 'rb') as source:
        assert client.post('/api/jobs', files={'file':('x.mp4',source,'text/html')}).status_code == 429
    with pytest.raises(ValueError):
        Storage(tmp_path).resolve('../outside')


def test_unknown_pickle_checkpoint_rejected_before_import(tmp_path):
    weights = tmp_path / 'last.pt'; weights.write_bytes(b'untrusted pickle')
    config = tmp_path / 'models.json'
    config.write_text(json.dumps({'format':'datathon-detector-ensemble-v1','models':[
        {'id':'expanded182-person','weights':'last.pt','weights_sha256':'0'*64},
        {'id':'expanded182-specialist','weights':'last.pt','weights_sha256':'0'*64}]}))
    with pytest.raises(ValueError, match='Untrusted YOLO'):
        YoloAdapter(config)


def test_chunked_body_is_bounded_before_spooling(env):
    client, _, _ = env(max_upload_bytes=32)
    def chunks():
        yield b'--bound\r\nContent-Disposition: form-data; name="file"; filename="clip.mov"\r\nContent-Type: video/quicktime\r\n\r\n'
        yield b'x' * (2 * 1024 * 1024)
        yield b'\r\n--bound--\r\n'
    response = client.post('/api/jobs', content=chunks(), headers={'Content-Type':'multipart/form-data; boundary=bound'})
    assert response.status_code == 413


def test_upload_mime_validation_before_decode(env, video):
    client, _, _ = env()
    with open(video, 'rb') as source:
        assert client.post('/api/jobs', files={'file':('x.mp4',source,'text/html')}).status_code == 400
