"""The session log file: what goes in, what is left out, and that it reads back. Isolated storage."""
import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import session_log
from voice_notes import VoiceNoteStore

RUN = '998d7ab7-24b8-45e8-bece-dc9455977405'


def context(run=RUN, **extra):
    return {'identity': {'track_name': 'Circuit de Test', 'track_path': 'test gp', 'car_name': 'Test GT3', 'car_path': 'testgt3',
                         'session_id': '123', 'subsession_id': '456'},
            'snapshot': {'run_id': run, 'session_time': 600., 'lap_number': 4, 'fuel_l': 20.5},
            'pit_wall': {'session_type': 'Race', 'fuel_laps': 2.4, 'stops_needed': 1,
                         'behind': {'idx': 38, 'name': 'Real Person', 'gap_estimate_s': 1.2},
                         'class_cars': [{'idx': 0, 'name': 'The Driver', 'class_position': 1}, {'idx': 7, 'name': 'Someone Else'}]},
            'radio': {'history': [{'text': '왼쪽 차량.'}]}, 'recent': [{'speed_mps': 50}],
            'file': 'C:\\Users\\someone\\Documents\\iRacing\\telemetry\\lap.ibt', **extra}


class SessionLogTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(folder.cleanup)
        self.store = VoiceNoteStore(str(Path(folder.name) / 'notes.sqlite3'))
        event = lambda number, key, status, reason='': {
            'event_id': f'{RUN}:{number}', 'run_id': RUN, 'key': key, 'engineer': 'spotter', 'status': status, 'reason': reason,
            'text': '1분 23초 6입니다.', 'session_time': 100. + number, 'lap_number': 2, 'distance_pct': .2, 'at': 1.}
        self.store.record_radio([event(1, 'lap_time', 'started'), event(1, 'lap_time', 'completed'),
                                 event(2, 'close_behind', 'expired', 'no_playback_window'),
                                 {**event(4, 'answer', 'completed'), 'text': '타이어는 괜찮습니다.'},
                                 {**event(3, 'left', 'started'), 'run_id': 'another-run-000', 'event_id': 'another-run-000:3'}])
        self.store.save('자동 보고 · 전략 엔지니어', context(), source='AUTO_REPORT')
        self.store.save('타이어 어때?', context(), source='VOICE')
        self.store.save('자동 보고 · 다른 세션', context(run='another-run-000'), source='AUTO_REPORT')

    def test_a_drive_without_reports_still_names_its_game_track_and_car(self):
        quiet = 'quiet-run-0000-0000'
        session = {'game': 'lmu', 'track_name': 'Monza', 'track_path': 'lmu-track-monza', 'track_config': '',
                   'car_name': 'Team WRT 2026 #32:LM', 'car_path': 'lmu-car-wrt-32', 'session_type': 'Practice'}
        self.store.record_radio([{'event_id': f'{quiet}:1', 'run_id': quiet, 'key': 'left', 'status': 'completed',
                                  'session': session, 'at': 1.}])
        listed = next(row for row in session_log.sessions(self.store) if row['run_id'] == quiet)
        self.assertEqual((listed['track'], listed['car'], listed['session_type']), ('Monza', 'Team WRT 2026 #32:LM', 'Practice'))
        name, data = session_log.export(self.store, quiet, '1.4.3')
        manifest = session_log.read(data)[0]
        self.assertEqual((manifest['game'], manifest['track'], manifest['session_type']), ('lmu', 'Monza', 'Practice'))
        self.assertTrue(name.startswith('coach-log-lmu-track-monza-'))

    def test_sessions_are_listed_with_their_track_and_counts(self):
        found = {item['run_id']: item for item in session_log.sessions(self.store)}
        self.assertEqual(set(found), {RUN, 'another-run-000'})
        self.assertEqual((found[RUN]['track'], found[RUN]['car'], found[RUN]['session_type']), ('Circuit de Test', 'Test GT3', 'Race'))
        self.assertEqual((found[RUN]['calls'], found[RUN]['completed'], found[RUN]['reports'], found[RUN]['questions']), (4, 2, 1, 1))

    def test_the_file_holds_one_session_without_names_paths_or_questions(self):
        name, data = session_log.export(self.store, RUN, '1.0.0')
        self.assertRegex(name, r'^coach-log-test-gp-\d{8}-\d{4}\.zip$')
        self.assertEqual(set(zipfile.ZipFile(io.BytesIO(data)).namelist()), {'manifest.json', 'radio.jsonl', 'reports.jsonl'})
        manifest, radio, reports = session_log.read(data)
        self.assertEqual((manifest['format'], manifest['app_version'], manifest['track'], manifest['radio_events']),
                         ('iracing-coach-log', '1.0.0', 'Circuit de Test', 4))
        self.assertEqual(manifest['calls'], {'lap_time': {'started': 1, 'completed': 1}, 'close_behind': {'expired:no_playback_window': 1},
                                             'answer': {'completed': 1}})
        self.assertEqual((manifest['questions_in_session'], manifest['questions_included'], manifest['reports']), (1, False, 1))
        self.assertEqual([event['key'] for event in radio], ['lap_time', 'lap_time', 'close_behind', 'answer'])   # the other session is not in it
        self.assertIsNone(radio[-1]['text'])                                                                # that an answer played, not what it said
        self.assertEqual([report['source'] for report in reports], ['AUTO_REPORT'])
        wall = reports[0]['context']['pit_wall']
        self.assertEqual((wall['behind']['name'], wall['behind']['gap_estimate_s'], wall['fuel_laps']), ('Driver 38', 1.2, 2.4))
        self.assertEqual([car['name'] for car in wall['class_cars']], ['Driver 0', 'Driver 7'])
        text = data.decode('latin-1') + json.dumps([manifest, radio, reports], ensure_ascii=False)
        for private in ('Real Person', 'The Driver', 'Someone Else', 'someone', 'telemetry', '타이어 어때', '타이어는 괜찮', 'subsession', '"recent"'):
            self.assertNotIn(private, json.dumps([manifest, radio, reports], ensure_ascii=False), private)
        self.assertIsNone(reports[0]['context']['file'])
        self.assertTrue(text)

    def test_questions_are_only_in_it_when_asked_for(self):
        _, data = session_log.export(self.store, RUN, '1.0.0', include_questions=True)
        manifest, radio, reports = session_log.read(data)
        self.assertTrue(manifest['questions_included'])
        self.assertEqual(radio[-1]['text'], '타이어는 괜찮습니다.')
        self.assertEqual([(report['source'], report['text']) for report in reports], [('AUTO_REPORT', None), ('VOICE', '타이어 어때?')])
        self.assertEqual(reports[1]['context']['pit_wall']['behind']['name'], 'Driver 38')       # names are replaced there too

    def test_an_unknown_session_and_a_wrong_file_are_refused(self):
        self.assertIsNone(session_log.export(self.store, 'no-such-run-0000', '1.0.0'))
        for wrong in (b'not a zip', self.zip({'manifest.json': '{"format": "something else"}', 'radio.jsonl': '', 'reports.jsonl': ''})):
            with self.assertRaises(ValueError):
                session_log.read(wrong)

    def test_sending_posts_the_file_and_tells_the_driver_when_it_did_not_arrive(self):
        name, data = session_log.export(self.store, RUN, '1.0.0')
        seen = {}

        class Reply:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return self.body

        def opened(body):
            def urlopen(request, timeout):
                seen.update(url=request.full_url, sent=json.loads(request.data), timeout=timeout)
                return Reply(body)
            return urlopen
        url = 'https://script.google.com/macros/s/EXAMPLE/exec'
        with patch('urllib.request.urlopen', opened(b'{"ok": true}')):
            self.assertTrue(session_log.send(url, name, data, '  12랩쯤 연료 안내가 달랐음  ', '1.0.0'))
        self.assertEqual((seen['url'], seen['sent']['format'], seen['sent']['name'], seen['sent']['note']),
                         (url, 'iracing-coach-log-upload', name, '12랩쯤 연료 안내가 달랐음'))
        self.assertEqual(session_log.read(base64.b64decode(seen['sent']['data']))[0]['track'], 'Circuit de Test')
        for body in (b'{"ok": false, "error": "daily limit"}', b'<html>sign in</html>'):
            with patch('urllib.request.urlopen', opened(body)), self.assertRaises(ValueError):
                session_log.send(url, name, data)
        with patch('urllib.request.urlopen', side_effect=OSError('offline')), self.assertRaises(ValueError):
            session_log.send(url, name, data)
        with self.assertRaises(ValueError):
            session_log.send('', name, data)                                   # not set up: nothing is sent anywhere
        with self.assertRaises(ValueError):
            session_log.send(url, name, b'x' * (session_log.UPLOAD_LIMIT + 1))

    def test_only_a_google_script_address_is_accepted_as_the_destination(self):
        import app_settings
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'log-upload-url.txt'
            with patch.object(app_settings, 'UPLOAD_FILE', target), patch.dict('os.environ', {'IRACING_LOG_UPLOAD_URL': ''}):
                self.assertEqual(app_settings.log_upload_url(), '')            # no file
                good = 'https://script.google.com/macros/s/AKfycb_example/exec'
                target.write_text('\n'.join(['# where logs go', '', good, '']), encoding='utf-8')
                self.assertEqual(app_settings.log_upload_url(), good)
                for other in ('https://example.com/upload', 'http://script.google.com/macros/s/x/exec',
                              'https://script.google.com/macros/s/x/dev', 'https://script.google.com.evil.example/macros/s/x/exec'):
                    target.write_text(other, encoding='utf-8')
                    self.assertEqual(app_settings.log_upload_url(), '', other)

    @staticmethod
    def zip(files):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        return buffer.getvalue()


if __name__ == '__main__':
    unittest.main()
