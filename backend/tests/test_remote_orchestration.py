import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).parents[2]


@pytest.fixture
def edge(monkeypatch):
    js = ModuleType('js')
    for key in ('AbortSignal', 'Headers', 'Object', 'Response'):
        setattr(js, key, SimpleNamespace())
    js.fetch = None
    ffi = ModuleType('pyodide.ffi')
    ffi.to_js = lambda obj, **kwargs: obj
    workers = ModuleType('workers')
    workers.WorkerEntrypoint = object
    workers.asgi = None
    monkeypatch.setitem(sys.modules, 'js', js)
    monkeypatch.setitem(sys.modules, 'pyodide', ModuleType('pyodide'))
    monkeypatch.setitem(sys.modules, 'pyodide.ffi', ffi)
    monkeypatch.setitem(sys.modules, 'workers', workers)
    for name in ('remote', 'edge_main'):
        path = ROOT / 'worker/src' / ('main.py' if name == 'edge_main' else name + '.py')
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
    return sys.modules['edge_main']


class Query:
    def __init__(self, conn, sql):
        self.conn, self.sql, self.args = conn, sql, ()
    def bind(self, *args):
        self.args = args
        return self
    async def first(self):
        row = self.conn.execute(self.sql, self.args).fetchone()
        return dict(row) if row else None
    async def run(self):
        self.conn.execute(self.sql, self.args)


class D1:
    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        for migration in sorted((ROOT / 'worker/migrations').glob('*.sql')):
            self.conn.executescript(migration.read_text())
    def prepare(self, sql):
        return Query(self.conn, sql)


class R2:
    def __init__(self):
        self.objects = {}
    async def put(self, key, value):
        self.objects[key] = value
    async def get(self, key):
        if key not in self.objects:
            return None
        value = self.objects[key]
        async def text():
            return value
        async def array_buffer():
            return SimpleNamespace(to_bytes=lambda: value)
        return SimpleNamespace(text=text, arrayBuffer=array_buffer)


def test_poll_persist_and_lazy_observation_evidence(edge, monkeypatch):
    env = SimpleNamespace(DB=D1(), MEDIA=R2())
    ident = 'abcdef123456'
    remote_id = '123456abcdef'
    env.DB.conn.execute("INSERT INTO jobs(id,state,filename,size_bytes,input_path,created_at,remote_job_id) VALUES(?,?,?,?,?,?,?)",
                        (ident, 'running', 'clip.mp4', 100, 'uploads/input.mp4', time.time(), remote_id))
    evidence_calls = []
    class Remote:
        async def status(self, job):
            assert job == remote_id
            return {'status': 'done'}
        async def results(self, job):
            return {'verdict': 'insufficient_evidence', 'violations': [],
                    'analysis': {'is_mock': True, 'frames': [{'frame_ref': 'e0-0001.000.jpg', 'timestamp_s': 1}]}}
        async def evidence(self, job, name):
            evidence_calls.append((job, name))
            return b'jpeg-content'
    monkeypatch.setattr(edge, 'remote_client', lambda _: Remote())
    async def run():
        job = await edge.load_job(env, ident)
        assert job['state'] == 'done'
        assert job['verdict'] is None
        result = await edge.load_result(env, job)
        assert result['verdict'] == 'insufficient_evidence'
        assert result['analysis']['is_mock'] is True
        assert result['analysis']['frames'][0]['url'] == f'/api/jobs/{ident}/evidence/e0-0001.000.jpg'
        request = SimpleNamespace(scope={'env': env})
        response = await edge.ui_evidence(ident, 'e0-0001.000.jpg', request)
        assert response.body == b'jpeg-content'
        response = await edge.ui_evidence(ident, 'e0-0001.000.jpg', request)
        assert response.body == b'jpeg-content'
        assert len(evidence_calls) == 1
        with pytest.raises(HTTPException) as error:
            await edge.ui_evidence(ident, 'e0-9999.000.jpg', request)
        assert error.value.status_code == 404
    asyncio.run(run())


def test_remote_deadline_marks_job_failed(edge, monkeypatch):
    env = SimpleNamespace(DB=D1(), MEDIA=R2())
    ident = 'abcdef123456'
    env.DB.conn.execute("INSERT INTO jobs(id,state,filename,size_bytes,input_path,created_at,remote_job_id) VALUES(?,?,?,?,?,?,?)",
                        (ident, 'running', 'clip.mp4', 100, 'uploads/input.mp4', time.time() - 3601, '123456abcdef'))
    job = asyncio.run(edge.load_job(env, ident))
    assert job['state'] == 'failed'
    assert job['error'] == 'Remote inference deadline exceeded'
