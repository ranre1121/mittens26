from __future__ import annotations

from app import jobs
from app.db import connect
from tests.conftest import API_KEY

KEY = {'X-API-Key': API_KEY}


def upload(client, video, path='/api/v1/analyze', headers=KEY, name='clip.mov'):
    with open(video, 'rb') as fh:
        return client.post(path, headers=headers, files={'file': (name, fh, 'video/quicktime')})


def test_rejects_missing_and_wrong_key(env, video):
    client, _, _ = env()
    assert upload(client, video, headers={"X-API-Key": ""}).status_code == 401
    assert upload(client, video, headers={'X-API-Key': 'nope'}).status_code == 401
    for path in ('/api/jobs', '/api/jobs/abc/video', '/api/v1/jobs/abc'):
        assert client.get(path, headers={'X-API-Key': ''}).status_code == 401


def test_rejects_non_video_and_wrong_extension(env, tmp_path, video):
    client, _, settings = env()
    junk = tmp_path / 'junk.mov'
    junk.write_bytes(b'not a video' * 100)
    assert upload(client, junk).status_code == 400
    assert upload(client, video, name='clip.jpg').status_code == 400
    assert not any((settings.data_dir / 'tmp').iterdir())
    assert not any((settings.data_dir / 'uploads').iterdir())


def test_mock_is_not_an_all_clear_or_fabricated_event(env, video):
    client, worker, _ = env()
    job_id = upload(client, video).json()['job_id']
    assert client.get(f'/api/v1/jobs/{job_id}').json()['status'] == 'processing'
    assert client.get(f'/api/v1/jobs/{job_id}/results').status_code == 409
    assert worker.run_once()
    assert not worker.run_once()
    body = client.get(f'/api/v1/jobs/{job_id}').json()
    assert body['status'] == 'done' and body['verdict'] == 'insufficient_evidence'
    assert body['violations'] == []
    analysis = body['analysis']
    assert analysis['is_mock'] and analysis['events'] == analysis['evidence'] == []
    assert analysis['models']['model_version'] == 'mock-v1'
    assert analysis['rule_evaluation']['decision'] == 'insufficient_evidence'
    assert all(x['decision'] == 'insufficient_evidence' for x in analysis['rule_evaluation']['results'])
    assert analysis['rule_evaluation']['results'][0]['sources']
    assert client.get(f'/api/v1/jobs/{job_id}/results').json()['analysis'] == analysis


def test_interface_media_and_path_safety(env, video):
    client, worker, _ = env()
    job_id = upload(client, video, path='/api/jobs').json()['job_id']
    assert client.get(f'/api/jobs/{job_id}').json()['queue_position'] == 0
    worker.run_once()
    result = client.get(f'/api/jobs/{job_id}').json()
    frame = result['analysis']['frames'][0]['frame_ref']
    assert client.get(f'/api/jobs/{job_id}/evidence/{frame}').status_code == 200
    assert client.get(f'/api/jobs/{job_id}/video').status_code == 200
    assert client.get(f'/api/jobs/{job_id}/video', headers={'Range': 'bytes=0-99'}).status_code == 206
    assert client.get(f'/api/jobs/{job_id}/evidence/../../app.sqlite3').status_code == 404
    assert client.get('/api/jobs/doesnotexist').status_code == 404


def test_processing_failure_is_failed_not_no_violation(env, video):
    client, worker, settings = env()
    job_id = upload(client, video).json()['job_id']
    with connect(settings.db_path) as conn:
        path = settings.data_dir / jobs.get_job(conn, job_id)['input_path']
    path.write_bytes(b'garbage')
    worker.run_once()
    body = client.get(f'/api/v1/jobs/{job_id}').json()
    assert body['status'] == 'failed' and 'verdict' not in body


def test_restart_recovery_requeues_then_gives_up(env, video):
    client, _, settings = env()
    job_id = upload(client, video).json()['job_id']
    conn = connect(settings.db_path)
    jobs.claim_next(conn)
    assert jobs.recover_interrupted(conn, settings.max_attempts) == (1, 0)
    conn.execute("UPDATE jobs SET state = 'running', attempts = ? WHERE id = ?", (settings.max_attempts, job_id))
    assert jobs.recover_interrupted(conn, settings.max_attempts) == (0, 1)
    assert jobs.get_job(conn, job_id)['state'] == 'failed'
    conn.close()


def test_retry_replaces_previous_attempt_artifacts(env, video):
    client, worker, settings = env()
    job_id = upload(client, video).json()['job_id']
    stale = settings.data_dir / 'evidence' / job_id / 'e9-0001.000.jpg'
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b'old attempt')
    worker.run_once()
    assert not stale.exists()
    with connect(settings.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM events WHERE job_id = ?', (job_id,)).fetchone()[0] == 0
