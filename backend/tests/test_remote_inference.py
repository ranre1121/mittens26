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


@pytest.mark.parametrize('url', ['http://gpu.example.test', 'https://user:pass@host', 'file:///tmp/model', 'https://host?a=1', 'https://host/arbitrary/path'])
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


@pytest.mark.parametrize('frame', [
    {'frame_ref': '../credentials', 'timestamp_s': 1},
    {'frame_ref': 'e0-0001.000.jpg', 'timestamp_s': float('nan')},
    {'frame_ref': 'e0-0001.000.jpg', 'timestamp_s': -1},
])
def test_remote_rejects_invalid_evidence_metadata(frame):
    async def request(*args, **kwargs):
        return {'violations': [], 'verdict': 'insufficient_evidence',
                'analysis': {'is_mock': False, 'frames': [frame]}}
    client = module.RemoteInference('https://gpu.example.test', 'secret', request)
    with pytest.raises(module.RemoteError, match='evidence reference'):
        asyncio.run(client.results('abcdef123456'))


def test_remote_readiness_and_model_provenance():
    provenance = {'model_id': 'Qwen/Qwen3-VL-8B-Instruct', 'model_version': 'base', 'is_mock': False}
    async def request(url, **kwargs):
        assert kwargs['headers'] == {'X-API-Key': 'secret'}
        return {'ready': True, 'models': provenance} if url.endswith('/ready') else provenance
    client = module.RemoteInference('https://gpu.example.test', 'secret', request)
    assert asyncio.run(client.ready())['ready'] is True
    assert asyncio.run(client.models()) == provenance


@pytest.mark.parametrize('operation', ['submit', 'status', 'results', 'ready', 'models'])
def test_remote_rejects_non_object_responses(operation):
    async def request(*args, **kwargs):
        return []
    client = module.RemoteInference('https://gpu.example.test', 'secret', request)
    args = () if operation in ('ready', 'models') else ('abcdef123456',)
    with pytest.raises(module.RemoteError):
        asyncio.run(getattr(client, operation)(*args))
