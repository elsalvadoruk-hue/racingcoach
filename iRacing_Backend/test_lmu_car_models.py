"""One LMU car per model, whatever the livery: live, recordings and saved data agree.

Temporary data folder only; no game, recordings or personal data.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('IRACING_TUNER_DB_PATH', os.path.join(tempfile.mkdtemp(), 'lmu-car-models.sqlite3'))

import lmu_car_models as models
from lmu_ibt import LmuIbtReader


class CarModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.env = patch.dict(os.environ, {'IRACING_LMU_CAR_MODELS_PATH': str(self.folder / 'models.json')})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_a_livery_seen_live_names_its_model_and_its_garage_names_the_other_liveries(self):
        self.assertEqual(models.car_key('Team WRT 2026 #69:LM'), ('lmu-car-team-wrt-2026-69-lm', 'Team WRT 2026 #69:LM'))
        models.learn('Team WRT 2026 #32:LM', model='BMW M4 LMGT3')               # driven live
        models.learn('Team WRT 2026 #32:LM', fingerprint='lmu-model-aaa')        # its recording
        models.learn('Team WRT 2026 #69:LM', fingerprint='lmu-model-aaa')        # another livery, same garage
        for car in ('Team WRT 2026 #32:LM', 'Team WRT 2026 #69:LM'):
            self.assertEqual(models.car_key(car), ('lmu-car-bmw-m4-lmgt3', 'BMW M4 LMGT3'))
        self.assertEqual(models.car_key('Team WRT 2026 #46:LM', fingerprint='lmu-model-aaa')[0], 'lmu-car-bmw-m4-lmgt3')
        # A different garage stays its own car until its model is known.
        self.assertEqual(models.car_key('Iron Lynx 2026 #61:WEC', fingerprint='lmu-model-bbb')[0], 'lmu-car-iron-lynx-2026-61-wec')

    def test_live_and_recording_use_the_same_key(self):
        from lmu_irsdk import LmuIrsdk
        from test_lmu_irsdk import adapter, frame, read_all
        sdk = read_all(*adapter([frame(car_name='Team WRT 2026 #69:LM', vehicle_model='BMW M4 LMGT3')]), 1)
        driver = next(d for d in sdk['DriverInfo']['Drivers'] if d.get('UserName') == '나')
        info = LmuIbtReader._session_info({'car_name': 'Team WRT 2026 #69:LM', 'car_model_key': 'lmu-model-aaa'}, 5793.)
        recorded = info['DriverInfo']['Drivers'][0]
        self.assertEqual((driver['CarPath'], recorded['CarPath']), ('lmu-car-bmw-m4-lmgt3', 'lmu-car-bmw-m4-lmgt3'))
        self.assertEqual(recorded['CarScreenName'], 'BMW M4 LMGT3')
        self.assertIsInstance(LmuIrsdk, type)

    def test_saved_livery_data_moves_to_the_model_once(self):
        data = self.folder / 'data'
        data.mkdir()
        old = ['lmu-car-team-wrt-2026-32-lm', 'lmu-car-team-wrt-2026-32-lm', 'Team WRT 2026 #32:LM', 'lmu-track-monza', 'lmu-track-monza', 'Monza', '', '', 5775]
        other = ['lmu-car-team-wrt-2026-69-lm', 'lmu-car-team-wrt-2026-69-lm', 'Team WRT 2026 #69:LM', 'lmu-track-monza', 'lmu-track-monza', 'Monza', '', '', 5775]
        keep = ['132', '132', 'BMW M4 GT3', '179', '179', 'Long Beach', '', '', 3200]
        corners = {'version': 1, 'tracks': {}, 'items': {json.dumps(old, ensure_ascii=False): [{'label': 'A'}],
                                                         json.dumps(other, ensure_ascii=False): [{'label': 'B'}],
                                                         json.dumps(keep, ensure_ascii=False): [{'label': 'iRacing'}]}}
        (data / 'corner-definitions.json').write_text(json.dumps(corners), encoding='utf-8')
        history = {'version': 1, 'races': {'lmu-car-team-wrt-2026-69-lm|lmu-track-monza|': [{'class_position': 10, 'at': 2}],
                                           'lmu-car-team-wrt-2026-32-lm|lmu-track-monza|': [{'class_position': 4, 'at': 1}],
                                           '132|179|': [{'class_position': 3, 'at': 0}]}}
        (data / 'race-history.json').write_text(json.dumps(history), encoding='utf-8')
        models.learn('Team WRT 2026 #32:LM', model='BMW M4 LMGT3')
        models.learn('Team WRT 2026 #69:LM', model='BMW M4 LMGT3')
        self.assertGreater(models.migrate_saved_keys(data), 0)
        corners = json.loads((data / 'corner-definitions.json').read_text(encoding='utf-8'))['items']
        model_keys = [key for key in corners if json.loads(key)[0] == 'lmu-car-bmw-m4-lmgt3']
        self.assertEqual(len(model_keys), 1)                               # the first livery's splits are kept
        self.assertEqual(corners[model_keys[0]], [{'label': 'A'}])
        self.assertIn(json.dumps(keep, ensure_ascii=False), corners)        # iRacing untouched
        races = json.loads((data / 'race-history.json').read_text(encoding='utf-8'))['races']
        self.assertEqual([r['class_position'] for r in races['lmu-car-bmw-m4-lmgt3|lmu-track-monza|']], [4, 10])
        self.assertIn('132|179|', races)
        self.assertEqual(models.migrate_saved_keys(data), 0)                # nothing left to move


if __name__ == '__main__':
    unittest.main()
