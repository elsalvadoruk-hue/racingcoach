"""Offline review uses only supplied archives and a temporary output folder."""
import json
from pathlib import Path
import tempfile
import unittest

import review_coach_logs as cli
import log_pack
import session_log


def archive(count=4, run='one', title='테스트 오벌'):
    manifest = {'format': session_log.FORMAT, 'run_id': run, 'game': 'iracing', 'started_at': 10,
                'spotter': {'profile': 'ASPHALT_OVAL'}, 'track': title, 'collection_versions': {}}
    traffic = [{'t': round(i / 10, 1), 'own': [2 if i < 2 else 1, False], 'c': []} for i in range(count)]
    header = {'own_fields': ['car_left_right', 'on_pit_road']}
    return session_log._zip(manifest, [], [], [session_log.Extra('nearby_cars', {'traffic.packed.xz': log_pack.pack(header, traffic)})])


class ReviewCliTests(unittest.TestCase):
    def test_procedures_are_separate_wrong_run_rejected_and_coverage_is_aggregated(self):
        import io
        import zipfile
        for run_id in ['one','old']:
            with self.subTest(run_id=run_id), tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                data=io.BytesIO(archive())
                with zipfile.ZipFile(data,'a') as z:
                    z.writestr('oval-procedures.json',json.dumps([{'run_id':run_id,'session_time':.2,
                        'kind':'free_pass','proposed_text':'</script><script>bad()</script>'}]))
                (root/'one.zip').write_bytes(data.getvalue())
                result=cli.run([root/'one.zip'],root/'out')
                if run_id=='old':self.assertEqual(len(result['errors']),1)
                else:
                    target=root/'out'/result['runs'][0]['folder']
                    review_doc=json.loads((target/'review.json').read_text(encoding='utf-8'))
                    self.assertEqual(review_doc['coverage']['aligned_procedure_rows'],1)
                    self.assertEqual(review_doc['radio_counts'],{})
                    self.assertTrue((target/'regression.json').is_file())
                    self.assertTrue((root/'out/coverage.json').is_file())

    def test_shadow_document_is_retained_separately_and_wrong_run_is_rejected(self):
        import io
        import zipfile
        for run_id in ['one', 'old']:
            with self.subTest(run_id=run_id), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                data = io.BytesIO(archive())
                with zipfile.ZipFile(data, 'a') as handle:
                    handle.writestr('banter-shadow.json', json.dumps([{'run_id': run_id, 'session_time': .2,
                        'status': 'ELIGIBLE_SHADOW', 'selected': {'text': '</script><script>bad()</script>'}}]))
                (root/'one.zip').write_bytes(data.getvalue())
                result = cli.run([root], root/'out')
                if run_id == 'old':
                    self.assertEqual(len(result['errors']), 1)
                else:
                    review = json.loads((root/'out'/result['runs'][0]['folder']/'review.json').read_text(encoding='utf-8'))
                    self.assertEqual(review['radio_counts'], {})
                    self.assertEqual(review['coverage']['aligned_banter_rows'], 1)
                    self.assertEqual(review['banter_observations'][0]['status'], 'ELIGIBLE_SHADOW')

    def test_zip_and_run_duplicates_keep_the_fuller_alternative(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'a.zip').write_bytes(archive(4))
            (root / 'b.zip').write_bytes((root / 'a.zip').read_bytes())
            (root / 'c.zip').write_bytes(archive(10))
            result = cli.run([root], root / 'out')
            self.assertEqual(len(result['runs']), 1)
            self.assertEqual(result['runs'][0]['rows'], 10)
            self.assertEqual(len(result['duplicates']), 2)
            for name in ('review.json', 'report.md', 'replay.jsonl', 'replay.html'):
                self.assertTrue((root / 'out' / result['runs'][0]['folder'] / name).is_file())

    def test_bad_zip_does_not_hide_valid_results(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'good.zip').write_bytes(archive())
            (root / 'bad.zip').write_bytes(b'bad')
            result = cli.run([root], root / 'out')
            self.assertEqual(len(result['runs']), 1)
            self.assertEqual(len(result['errors']), 1)

    def test_invalid_number_does_not_stop_other_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'good.zip').write_bytes(archive())
            manifest = {'format': session_log.FORMAT, 'run_id': 'invalid'}
            traffic = log_pack.pack({}, [{'t': float('nan'), 'own': [], 'c': []}])
            (root / 'invalid.zip').write_bytes(session_log._zip(manifest, [], [],
                [session_log.Extra('nearby_cars', {'traffic.packed.xz': traffic})]))
            result = cli.run([root], root / 'out')
            self.assertEqual(len(result['runs']), 1)
            self.assertEqual(len(result['errors']), 1)

    def test_script_text_stays_inert_in_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'one.zip').write_bytes(archive(title='</script><script>window.pwned=true</script>'))
            result = cli.run([root / 'one.zip'], root / 'out')
            page = (root / 'out' / result['runs'][0]['folder'] / 'replay.html').read_text(encoding='utf-8')
            payload = page.split('id="payload">', 1)[1].split('</script>', 1)[0]
            self.assertNotIn('<script>', payload)
            self.assertIn('</script>', json.loads(payload)['review']['track'])
            self.assertNotIn('src="http', page)

    def test_legacy_without_traffic_or_version_stays_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'old.zip').write_bytes(session_log._zip({'format': session_log.FORMAT, 'run_id': 'old'}, [], [], []))
            result = cli.run([root], root / 'out')
            self.assertEqual(result['runs'][0]['profile'], 'UNKNOWN')
            self.assertEqual(result['runs'][0]['verdict'], 'INSUFFICIENT_FOR_PASS')


if __name__ == '__main__':
    unittest.main()
