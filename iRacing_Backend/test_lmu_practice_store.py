from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from lmu_practice_store import LmuPracticeStore, MAX_PAYLOAD_BYTES


class LmuPracticeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'lazy'/'practice.sqlite3'
        self.store = LmuPracticeStore(self.path)

    def test_reads_and_failed_nonexistent_writes_do_not_create_files(self):
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.store.list_tasks(), [])
        for operation in (lambda: self.store.get('missing'), lambda: self.store.comparisons('missing'),
                          lambda: self.store.add_comparison('missing', {'schema_version': 1})):
            with self.assertRaises(KeyError):
                operation()
        self.assertFalse(self.path.parent.exists())

    def test_persistence_and_json_isolation_for_baseline_and_comparison_history(self):
        baseline = {'schema_version': 1, 'definition_id': 'fixed', 'laps': [1, 2], 'focus': {'number': 3}}
        task = self.store.create(baseline)
        uuid.UUID(hex=task['task_id'])
        self.assertEqual(len(task['task_id']), 32)
        baseline['focus']['number'] = 99
        task['baseline']['laps'].append(3)
        fresh = LmuPracticeStore(self.path)
        task = fresh.get(task['task_id'])
        self.assertEqual(task['baseline']['focus']['number'], 3)
        self.assertEqual(task['baseline']['laps'], [1, 2])
        report = {'schema_version': 1, 'delta': {'duration_s': -.25}, 'comparison_id': 'untrusted', 'created_at': 'untrusted'}
        comparison = fresh.add_comparison(task['task_id'], report)
        self.assertNotEqual(comparison['comparison_id'], 'untrusted')
        self.assertNotEqual(comparison['created_at'], 'untrusted')
        report['delta']['duration_s'] = 99
        comparison['delta']['duration_s'] = 100
        self.assertEqual(self.store.comparisons(task['task_id'])[0]['delta']['duration_s'], -.25)
        self.assertEqual(self.store.list(), [task])
        summary = self.store.list_tasks()[0]
        self.assertEqual((summary['definition_id'], summary['lap_count'], summary['focus']), ('fixed', 2, {'number': 3}))
        self.assertNotIn('path', task)
        json.dumps(task, allow_nan=False)

    def test_concurrent_writers_keep_every_task_and_comparison(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            tasks = list(executor.map(lambda i: LmuPracticeStore(self.path).create({'index': i}), range(12)))
        self.assertEqual(len({task['task_id'] for task in tasks}), 12)
        self.assertEqual(len(self.store.list()), 12)
        task_id = tasks[0]['task_id']
        with ThreadPoolExecutor(max_workers=8) as executor:
            reports = list(executor.map(lambda i: LmuPracticeStore(self.path).add_comparison(task_id, {'index': i}), range(12)))
        self.assertEqual(len({report['comparison_id'] for report in reports}), 12)
        self.assertEqual(sorted(report['index'] for report in self.store.comparisons(task_id)), list(range(12)))
        self.assertEqual(self.store.comparisons(tasks[1]['task_id']), [])

    def test_invalid_json_payloads_fail_before_creation(self):
        deep = {}; cursor = deep
        for _ in range(66):
            cursor['next'] = {}; cursor = cursor['next']
        invalid = [None, [], {'x': float('nan')}, {'x': float('inf')}, {'x': (1, 2)},
                   {1: 'nonstring'}, {'schema_version': 2}, {'schema_version': True},
                   {'text': 'x'*MAX_PAYLOAD_BYTES}, deep]
        for payload in invalid:
            with self.subTest(payload_type=type(payload)), self.assertRaises(ValueError):
                self.store.create(payload)
        self.assertFalse(self.path.parent.exists())

    def test_unknown_task_does_not_insert_a_comparison(self):
        task = self.store.create({'schema_version': 1})
        with self.assertRaises(KeyError):
            self.store.add_comparison('missing', {'schema_version': 1})
        self.assertEqual(self.store.comparisons(task['task_id']), [])

    def test_duplicate_identity_rolls_back_without_replacing_baseline(self):
        fixed = uuid.UUID('00000000-0000-0000-0000-000000000001')
        with patch('lmu_practice_store.uuid.uuid4', return_value=fixed):
            task = self.store.create({'label': 'original'})
            with self.assertRaises(ValueError):
                self.store.create({'label': 'replacement'})
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(self.store.get(task['task_id'])['baseline']['label'], 'original')

    def test_future_version_is_rejected_for_reads_and_writes_without_modification(self):
        self.store.create({'schema_version': 1})
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute('PRAGMA user_version=2')
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        for operation in (self.store.list, lambda: self.store.create({'schema_version': 1})):
            with self.assertRaisesRegex(ValueError, 'schema_version'):
                operation()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_bad_database_schema_or_json_is_rejected_and_not_repaired(self):
        task = self.store.create({'schema_version': 1})
        for payload in ('not json', '{"x":NaN}', '{"x":1,"x":2}', '[]', '{"schema_version":9}'):
            with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
                connection.execute('UPDATE tasks SET payload=?', (payload,))
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, 'JSON'):
                self.store.get(task['task_id'])
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute('DROP TABLE comparisons')
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '스키마'):
            self.store.list()
        self.assertEqual(self.path.read_bytes(), before)

    def test_fake_schema_with_matching_field_names_is_rejected(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute('CREATE TABLE tasks(task_id TEXT,created_at TEXT,payload TEXT)')
            connection.execute('CREATE TABLE comparisons(comparison_id TEXT,task_id TEXT,created_at TEXT,payload TEXT)')
            connection.execute('PRAGMA user_version=1')
        with self.assertRaisesRegex(ValueError, '스키마'):
            self.store.list()


if __name__ == '__main__':
    unittest.main()
