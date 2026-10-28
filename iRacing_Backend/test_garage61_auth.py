"""Synthetic authentication failures must never launch an unexpected browser."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import garage61_api_collector as worker
from garage61_collector_manager import Garage61CollectorManager


class Garage61AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='garage61-auth-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = Garage61CollectorManager(
            Path(worker.__file__).parent, self.root / 'references', self.root / 'jobs')

    def test_auto_without_token_normally_skips_without_starting_process(self):
        with patch.object(self.manager, 'api_token_configured', return_value=False), \
                patch('garage61_collector_manager.subprocess.Popen') as process:
            result = self.manager.start({'collector_mode': 'AUTO'})
        self.assertEqual(result['state'], 'SKIPPED_NO_TOKEN')
        self.assertNotIn('error_code', result)
        self.assertIn('내 주행 데이터', result['message'])
        self.assertNotIn('Playwright', result['message'])
        process.assert_not_called()

    def test_auto_uses_api_with_or_without_token_but_manual_browser_is_preserved(self):
        for present in (False, True):
            with self.subTest(token=present), patch.object(self.manager, 'api_token_configured', return_value=present):
                self.assertEqual(self.manager.preferred_mode(), 'API')
                self.assertEqual(self.manager.preferred_mode('BROWSER'), 'BROWSER')

    def run_worker(self, token, response):
        request, status = self.root / 'request.json', self.root / 'status.json'
        request.write_text(json.dumps({'job_id': 'auth-test', 'filename': 'synthetic.ibt'}), encoding='utf-8')
        with patch.object(worker, '_token_from_sources', return_value=(token, 'synthetic')), \
                patch.object(worker, 'api_json', side_effect=response) as api:
            result = worker.run(request, status, self.root / 'references', None)
        return result, json.loads(status.read_text(encoding='utf-8')), api.call_count

    def test_worker_missing_token_normally_skips_without_network(self):
        result, status, calls = self.run_worker('', AssertionError('network must not run'))
        self.assertEqual(result, 0)
        self.assertEqual(calls, 0)
        self.assertEqual(status['state'], 'SKIPPED_NO_TOKEN')
        self.assertNotIn('error_code', status)
        self.assertIn('내 주행 데이터', status['message'])
        self.assertNotIn('.bat', status['message'])

    def test_invalid_token_and_forbidden_access_have_distinct_actionable_messages(self):
        for http, code, wording in [(401, 'API_TOKEN_INVALID', '새 토큰'),
                                     (403, 'API_PERMISSION_DENIED', '권한')]:
            with self.subTest(http=http):
                result, status, calls = self.run_worker('synthetic-token', worker.Garage61ApiError(http, 'private provider detail'))
                self.assertEqual(result, 4)
                self.assertEqual(calls, 1)
                self.assertEqual(status['error_code'], code)
                self.assertIn(wording, status['message'])
                self.assertIn('설정 → 설치 설정', status['message'])
                self.assertNotIn('private provider detail', status['message'])

    def test_missing_driving_permission_is_not_reported_as_invalid_token(self):
        result, status, calls = self.run_worker('synthetic-token', [{'apiPermissions': []}])
        self.assertEqual(status['error_code'], 'API_PERMISSION_DENIED')
        self.assertIn('driving_data', status['message'])
        self.assertEqual(calls, 1)

    def test_server_error_remains_a_server_error(self):
        result, status, calls = self.run_worker('synthetic-token', worker.Garage61ApiError(503, 'unavailable'))
        self.assertEqual(status['error_code'], 'GARAGE61_API_HTTP_ERROR')
        self.assertIn('HTTP 503', status['message'])


if __name__ == '__main__':
    unittest.main()
