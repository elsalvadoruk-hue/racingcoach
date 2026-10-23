"""The startup analysis shares progress without changing results or hiding failures."""
import asyncio
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
import analysis_progress
from analysis_routes import register_routes


class StartupProgressTests(unittest.TestCase):
    def client(self, load):
        app = FastAPI()
        register_routes(app, SimpleNamespace(load_analysis=load))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_progress_reaches_worker_and_success_preserves_result(self):
        async def load(payload):
            self.assertLess(analysis_progress.status('startup-success')['percent'], 100)
            await asyncio.to_thread(analysis_progress.stage, 'handling', laps=12)
            self.assertEqual(analysis_progress.status('startup-success')['stage'], 'handling')
            return {'comparison': {'delta_s': .123}, 'filename': payload.filename}

        result = self.client(load).post('/api/analyze', json={'filename': 'fixture.ibt', 'progress_id': 'startup-success'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {'comparison': {'delta_s': .123}, 'filename': 'fixture.ibt'})
        self.assertEqual(analysis_progress.status('startup-success')['percent'], 100)

    def test_failure_keeps_error_status_and_never_completes(self):
        from fastapi import HTTPException

        async def load(_):
            analysis_progress.stage('handling', laps=4)
            raise HTTPException(422, 'No comparable lap')

        result = self.client(load).post('/api/analyze', json={'progress_id': 'startup-failure'})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json()['detail'], 'No comparable lap')
        status = analysis_progress.status('startup-failure')
        self.assertEqual(status['stage'], 'error')
        self.assertLess(status['percent'], 100)

    def test_old_clients_need_no_progress_id(self):
        async def load(payload):
            self.assertIsNone(payload.progress_id)
            analysis_progress.stage('handling', laps=4)
            return {'unchanged': True}

        self.assertEqual(self.client(load).post('/api/analyze', json={}).json(), {'unchanged': True})
