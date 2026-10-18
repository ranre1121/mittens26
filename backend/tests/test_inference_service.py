import pytest
from fastapi.testclient import TestClient
from app.inference_api import create_inference_app


def test_gpu_service_requires_secret(monkeypatch):
    monkeypatch.delenv('INFERENCE_API_KEY', raising=False)
    with pytest.raises(ValueError, match='INFERENCE_API_KEY'):
        create_inference_app()


def test_service_auth_and_device_status(monkeypatch, tmp_path):
    monkeypatch.setenv('INFERENCE_API_KEY', 'test-inference-key')
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('YOLO_DEVICE', 'cuda:0')
    with TestClient(create_inference_app()) as client:
        assert client.get('/api/v1/models').status_code == 401
        response = client.get('/api/v1/models', headers={'X-API-Key':'test-inference-key'})
        assert response.status_code == 200
        assert response.json()['yolo_device'] == 'cuda:0'
        assert response.json()['is_mock'] is True


def test_cloudflare_client_to_inference_http_contract(monkeypatch, tmp_path, video):
    import asyncio
    import httpx
    from app.config import load_settings
    from app.worker import Worker
    from tests.test_remote_inference import module as remote

    monkeypatch.setenv('INFERENCE_API_KEY', 'test-inference-key')
    monkeypatch.setenv('DATA_DIR', str(tmp_path / 'service'))
    app = create_inference_app()

    async def run():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://gpu.example.test') as http:
                async def request(url, *, method, body, headers, timeout_ms, binary):
                    kwargs = {'files': body} if body is not None else {}
                    response = await http.request(method, url, headers=headers, timeout=timeout_ms / 1000, **kwargs)
                    response.raise_for_status()
                    return response.content if binary else response.json()
                client = remote.RemoteInference('https://gpu.example.test', 'test-inference-key', request)
                job = await client.submit({'file': ('test.mov', video.read_bytes(), 'video/quicktime')})
                assert (await client.status(job))['status'] == 'processing'
                worker = Worker(load_settings())
                try:
                    assert await asyncio.to_thread(worker.run_once)
                finally:
                    worker.conn.close()
                assert (await client.status(job))['status'] == 'done'
                result = await client.results(job)
                assert result['verdict'] == 'insufficient_evidence'
                assert result['analysis']['is_mock'] is True
                frame = result['analysis']['frames'][0]['frame_ref']
                assert (await client.evidence(job, frame)).startswith(b'\xff\xd8')
    asyncio.run(run())
