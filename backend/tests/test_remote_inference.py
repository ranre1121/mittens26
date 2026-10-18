import importlib.util
from pathlib import Path
import asyncio
import pytest

spec = importlib.util.spec_from_file_location('edge_remote', Path(__file__).parents[2] / 'worker/src/remote.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_authenticated_remote_lifecycle():
    calls = []
    async def request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith('/analyze'):
            return {'job_id': 'abcdef123456'}
        if url.endswith('/results'):
            return {'violations': [], 'verdict': 'insufficient_evidence', 'analysis': {'is_mock': True}}
        return {'status': 'done'}
    client = module.RemoteInference('https://gpu.example.test', 'secret', request)
    async def run():
        job = await client.submit('multipart')
        assert (await client.status(job))['status'] == 'done'
        result = await client.results(job)
        assert result['verdict'] == 'insufficient_evidence'
        assert result['analysis']['is_mock'] is True
    asyncio.run(run())
    assert all(call[1]['headers'] == {'X-API-Key': 'secret'} for call in calls)
    assert calls[0][1]['body'] == 'multipart'
    assert all(call[1]['timeout_ms'] == 30000 for call in calls)


@pytest.mark.parametrize('url', ['http://gpu.example.test', 'https://user:pass@host', 'file:///tmp/model', 'https://host?a=1'])
def test_reject_unsafe_service_urls(url):
    with pytest.raises(module.RemoteError):
        module.RemoteInference(url, 'secret', None)


def test_no_fallback_on_remote_error():
    async def request(*args, **kwargs):
        raise TimeoutError('remote timeout')
    client = module.RemoteInference('https://gpu.example.test', 'secret', request)
    with pytest.raises(TimeoutError):
        asyncio.run(client.submit('video'))


def test_reject_remote_path_injection():
    client = module.RemoteInference('https://gpu.example.test', 'secret', None)
    with pytest.raises(module.RemoteError):
        asyncio.run(client.status('../../secret'))
    with pytest.raises(module.RemoteError):
        asyncio.run(client.evidence('abcdef123456', '../secret'))


def test_remote_rejects_mock_compliance_findings():
    async def request(*args, **kwargs):
        return {'violations': [], 'verdict': 'no_violation_observed', 'analysis': {'is_mock': True}}
    client = module.RemoteInference('https://gpu.example.test', 'secret', request)
    with pytest.raises(module.RemoteError, match='Mock inference'):
        asyncio.run(client.results('abcdef123456'))
