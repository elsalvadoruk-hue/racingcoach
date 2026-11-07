import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import garage61_imports as imports

NAME = 'Garage 61 - Jane Doe - BMW M4 GT3 EVO - Long Beach Street Circuit (Unknown) - 01.18.898 - 01M3HJNV67E0C95DYS6DXM80Q1.csv'
OTHER = 'Garage 61 - Jo Roe - BMW M4 GT3 EVO - Long Beach Street Circuit (Unknown) - 01.19.100 - 01M3HJNV67E0C95DYS6DXM80Q2.csv'
CSV = b'Speed,LapDistPct,Brake,Throttle\n50,0.0,0,1\n'


def accept(path):
    if b'Speed' not in Path(path).read_bytes():
        raise ValueError('missing Speed')


def meta(path):
    return {'driver_name': 'Jane Doe', 'car_name': 'BMW M4 GT3 EVO', 'track_name': 'Long Beach', 'lap_time_s': 78.898}


class Garage61ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.downloads = root / 'Downloads'
        self.library = root / 'Garage61_References'
        self.downloads.mkdir()
        self.env = patch.dict(os.environ, {'IRACING_DOWNLOADS_DIR': str(self.downloads)})
        self.env.start()
        app = FastAPI()
        imports.register_routes(app, lambda: self.library, accept, meta)
        self.client = TestClient(app)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_names_that_could_act_as_paths_are_refused(self):
        self.assertTrue(imports.valid_name(NAME))
        for bad in ('..\\' + NAME, 'x/' + NAME, NAME.replace('.csv', '.txt'), 'Garage 61 - x.csv', ''):
            self.assertFalse(imports.valid_name(bad), bad)

    def test_downloads_listing_reads_names_only_and_marks_kept_laps(self):
        (self.downloads / NAME).write_bytes(CSV)
        (self.downloads / OTHER).write_bytes(CSV)
        (self.downloads / 'notes.csv').write_bytes(CSV)
        self.library.mkdir()
        (self.library / OTHER).write_bytes(CSV)
        payload = self.client.get('/api/garage61/downloads').json()
        names = {row['name']: row for row in payload['files']}
        self.assertEqual(set(names), {NAME, OTHER})
        self.assertFalse(names[NAME]['imported'])
        self.assertTrue(names[OTHER]['imported'])

    def test_import_from_downloads_copies_and_leaves_the_source(self):
        source = self.downloads / NAME
        source.write_bytes(CSV)
        response = self.client.post('/api/garage61/downloads/import', json={'name': NAME})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'name': NAME, 'already_imported': False})
        self.assertEqual((self.library / NAME).read_bytes(), CSV)
        self.assertEqual(source.read_bytes(), CSV)
        again = self.client.post('/api/garage61/downloads/import', json={'name': NAME}).json()
        self.assertTrue(again['already_imported'])
        self.assertEqual([p.name for p in self.library.iterdir()], [NAME])

    def test_same_lap_already_kept_under_another_name_is_not_duplicated(self):
        self.library.mkdir()
        kept = NAME.replace('Jane Doe', 'Jane D')
        (self.library / kept).write_bytes(CSV)
        response = self.client.post(f'/api/garage61/import?filename={NAME}', content=CSV)
        self.assertEqual(response.json(), {'name': kept, 'already_imported': True})
        self.assertFalse((self.library / NAME).exists())

    def test_picked_file_is_validated_before_it_reaches_the_library(self):
        response = self.client.post(f'/api/garage61/import?filename={NAME}', content=b'not,a,lap\n')
        self.assertEqual(response.status_code, 422)
        self.assertIn('읽을 수 없습니다', response.json()['detail'])
        self.assertFalse(any(p.suffix == '.csv' for p in self.library.rglob('*')))
        self.assertEqual(self.client.post(f'/api/garage61/import?filename={NAME}', content=b'').status_code, 422)

    def test_requests_cannot_reach_outside_the_downloads_folder(self):
        outside = Path(self.tmp.name) / NAME
        outside.write_bytes(CSV)
        for name in ('../' + NAME, '..\\' + NAME):
            response = self.client.post('/api/garage61/downloads/import', json={'name': name})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post('/api/garage61/downloads/import', json={'name': NAME}).status_code, 404)
        self.assertEqual(self.client.post('/api/garage61/import?filename=../x.csv', content=CSV).status_code, 422)

    def test_missing_downloads_folder_is_reported_not_raised(self):
        with patch.dict(os.environ, {'IRACING_DOWNLOADS_DIR': str(self.downloads / 'missing')}):
            payload = self.client.get('/api/garage61/downloads').json()
        self.assertFalse(payload['available'])
        self.assertEqual(payload['files'], [])


class WebExportLayoutTests(unittest.TestCase):
    """Garage61 file names drop the layout when the track name already holds it."""

    def check(self, ref_track, ref_config, track, config):
        from reference_conditions import external_reference_compatibility
        return external_reference_compatibility(
            {'car_name': 'McLaren 720S GT3 EVO', 'track_name': ref_track, 'track_config': ref_config},
            {'car_name': 'McLaren 720S GT3 EVO', 'track_name': track, 'track_config': config})['compatible']

    def test_layout_named_in_the_track_name_matches(self):
        self.assertTrue(self.check('Sebring International Raceway', '', 'Sebring International Raceway', 'International'))
        self.assertTrue(self.check('Circuit des 24 Heures du Mans', '', 'Circuit des 24 Heures du Mans', '24 Heures du Mans'))
        self.assertTrue(self.check('Detroit Grand Prix at Belle Isle', '', 'Detroit Grand Prix at Belle Isle', 'Belle Isle'))

    def test_a_layout_missing_from_the_name_is_not_assumed(self):
        self.assertFalse(self.check('Sebring International Raceway', '', 'Sebring International Raceway', 'Club'))
        self.assertFalse(self.check('Autodromo Nazionale Monza', '', 'Autodromo Nazionale Monza', 'Grand Prix'))
        self.assertFalse(self.check('Hockenheimring Baden-Württemberg', 'Grand Prix', 'Hockenheimring Baden-Württemberg', 'Outer'))

    def test_unknown_layout_matches_only_single_layout_venues(self):
        self.assertTrue(self.check('Long Beach Street Circuit', 'Unknown', 'Long Beach Street Circuit', ''))
        self.assertFalse(self.check('Long Beach Street Circuit', 'Unknown', 'Long Beach Street Circuit', 'Grand Prix'))


if __name__ == '__main__':
    unittest.main()
