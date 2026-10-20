from pathlib import Path
import tempfile
import threading
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from lmu_routes import LmuRoutesServices, register_routes, _file_id
from lmu_practice_store import LmuPracticeStore
from test_lmu_corners import lap


class PracticeRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root/'sample.duckdb'
        self.path.write_bytes(b'synthetic recording A')
        self.key = _file_id(self.path)
        self.store = LmuPracticeStore(self.root/'lmu-only'/'practice.sqlite3')
        parent = self
        class Reader:
            def __init__(self, path): self.path = path
            def analysis_lap(self, number):
                parent.assertNotIn('asyncio-portal', threading.current_thread().name)
                if parent.mutate:
                    Path(str(self.path)+'.wal').write_bytes(b'changed')
                return lap(number=number)
        self.mutate = False
        self.services = LmuRoutesServices(discover_dirs=lambda: (self.root,),
            list_files=lambda _: (self.path,), dependency_check=lambda: None,
            reader_factory=Reader, practice_store=self.store)
        self.client = self.new_client()
        self.body = {'selections': [{'file_id': self.key, 'lap_number': n} for n in (1, 2, 3)]}

    def new_client(self):
        app = FastAPI()
        register_routes(app, self.services)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_lazy_read_save_restart_compare_history_and_no_source_reopen(self):
        route = '/api/lmu/practice/tasks'
        self.assertEqual(self.client.get(route).json()['tasks'], [])
        self.assertFalse(self.store.path.exists())
        response = self.client.post(route, json=self.body)
        self.assertEqual(response.status_code, 200, response.text)
        task = response.json()['task']
        task_route = route+'/'+task['task_id']
        again = self.new_client()
        self.assertEqual(again.get(route).json()['tasks'][0], task)
        # Current numbers differ; original file no longer needs baseline content.
        self.path.write_bytes(b'synthetic recording B')
        current = {'selections': [s | {'lap_number': s['lap_number']+3} for s in self.body['selections']]}
        response = again.post(task_route+'/compare', json=current)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['comparison']['status'], 'AVAILABLE')
        loaded = again.get(task_route).json()
        self.assertEqual(loaded['task'], task)
        self.assertEqual(len(loaded['comparisons']), 1)
        self.assertNotIn(str(self.root), response.text)
        self.assertIsNone(response.json()['comparison']['automatic_best_lap'])

    def test_invalid_and_reused_selection_does_not_write(self):
        route = '/api/lmu/practice/tasks'
        for body in ({'selections': self.body['selections'][:2]},
                     {'selections': [self.body['selections'][0]]*3},
                     {'selections': [s | {'file_id': 'a'*24} for s in self.body['selections']]}):
            response = self.client.post(route, json=body)
            self.assertIn(response.status_code, (422, 404))
            self.assertFalse(self.store.path.exists())
        task = self.client.post(route, json=self.body).json()['task']
        task_route = route+'/'+task['task_id']
        self.assertEqual(self.client.post(task_route+'/compare', json=self.body).status_code, 422)
        self.assertEqual(self.client.get(task_route).json()['comparisons'], [])
        self.assertEqual(self.client.get(route+'/unknown').status_code, 404)

    def test_wal_change_before_save_never_creates_task(self):
        self.mutate = True
        response = self.client.post('/api/lmu/practice/tasks', json=self.body)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn('파일이 변경', response.json()['detail'])
        self.assertFalse(self.store.path.exists())


if __name__ == '__main__':
    unittest.main()
