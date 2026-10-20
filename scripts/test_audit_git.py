"""Security checks use synthetic data and temporary repositories only."""
import json
import base64
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import audit_git


class GitAuditTests(unittest.TestCase):
    def test_private_files_and_audio_are_rejected(self):
        for name in ['data/settings.json', 'a/gcp-key.json', 'a/garage61_api_token.txt',
                     'x.mp3', 'a.sqlite3', 'session.duckdb', 'session.duckdb.wal',
                     '.env.production', 'artifacts/a.py', 'notes.log']:
            with self.subTest(name=name):
                self.assertIn('forbidden path', audit_git.findings(name, b'harmless'))

    def test_local_identifier_is_checked_even_inside_binary(self):
        self.assertEqual(audit_git.findings('manual/image.png', b'\x00ExamplePrivateUser\x00',
                         [b'ExamplePrivateUser']), ['local private identifier'])

    def test_secret_signatures_are_rejected_without_returning_their_value(self):
        value = ('AIza' + 'A' * 35).encode()
        result = audit_git.findings('src/example.js', b'const key="'+value+b'";')
        self.assertIn('Google API key', result)
        self.assertNotIn(value.decode(), json.dumps(result))

    def test_inline_raster_data_is_not_mistaken_for_a_token_but_metadata_is_checked(self):
        raster = base64.b64encode(b'ExamplePrivateUser')
        content = b'<image href="data:image/png;base64,'+raster+b'"/>'
        self.assertIn('local private identifier in embedded image',
                      audit_git.findings('public/map.svg', content, [b'ExamplePrivateUser']))
        self.assertFalse(audit_git.findings('public/map.svg', content))

    def test_private_key_placeholder_is_allowed_but_actual_key_header_is_blocked(self):
        header = b'-----BEGIN ' + b'PRIVATE KEY-----'
        self.assertTrue(audit_git.findings('example.py', header+b'\\n'+b'A'*100))
        placeholder = header+b'\\nSECRET\\n-----END '+b'PRIVATE KEY-----'
        self.assertFalse(audit_git.findings('example.py', placeholder))

    def test_examples_and_environment_lookups_are_allowed(self):
        self.assertFalse(audit_git.forbidden_path('src/data/officialTrackMaps.json'))
        self.assertFalse(audit_git.findings('iRacing_Backend/example.py',
                         b'key = os.getenv("GOOGLE_API_KEY")\npath="C:\\Users\\Driver\\Documents"'))

    def test_history_finds_a_secret_deleted_from_the_latest_commit(self):
        with tempfile.TemporaryDirectory(prefix='git-audit-') as folder:
            root = Path(folder)
            def run(*args):
                subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)
            run('init', '-b', 'main')
            run('config', 'user.name', 'Test')
            run('config', 'user.email', 'test@example.invalid')
            file = root / 'example.py'
            file.write_text('secret="' + 'ghp_' + 'A' * 36 + '"\n', encoding='utf-8')
            run('add', '--', 'example.py'); run('commit', '-m', 'Synthetic fixture')
            file.write_text('secret = None\n', encoding='utf-8')
            run('add', '--', 'example.py'); run('commit', '-m', 'Remove fixture')
            with patch.object(audit_git, 'local_markers', return_value=[]):
                self.assertFalse(audit_git.audit(root)['findings'])
                self.assertTrue(audit_git.audit(root, history=True)['findings'])


if __name__ == '__main__':
    unittest.main()
