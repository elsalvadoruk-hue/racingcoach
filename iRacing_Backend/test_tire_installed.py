"""Installed cold pressure evidence, iRacing pit measurements and the richer stint records."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from tire_reader.history import TireHistoryStore
from tire_reader.installed import InstalledPressure
from tire_reader.log_export import _observation, _summary
from test_tire_advisor import snapshot

IDENTITY = dict(generation=1, car_path='bmwm4gt3', track_id=1, track_config='', session_id=5, subsession_id=6)


def row(**values):
    base = dict(IDENTITY, IsInGarage=False, IsOnTrack=True, PlayerCarInPitStall=False, PitSvFlags=0,
                LFcoldPressure=165., RFcoldPressure=166., LRcoldPressure=160., RRcoldPressure=161.,
                PitSvLFP=170., PitSvRFP=171., PitSvLRP=172., PitSvRRP=173., SessionTime=100.)
    for sdk in ('LF', 'RF', 'LR', 'RR'):
        for side in 'LMR':
            base[f'{sdk}wear{side}'] = .85
            base[f'{sdk}tempC{side}'] = 80.
    base.update(values)
    return base


class InstalledTests(unittest.TestCase):
    def test_leaving_the_garage_installs_the_setup_pressure_and_joining_on_track_does_not(self):
        joined = InstalledPressure()
        self.assertEqual(set(joined.update(row())['installed_cold_kpa'].values()), {None})
        tracker = InstalledPressure()
        tracker.update(row(IsInGarage=True, IsOnTrack=False, LFcoldPressure=150.))
        tracker.update(row(IsInGarage=True, IsOnTrack=False))  # the last garage value counts
        fields = tracker.update(row())
        self.assertEqual(fields['installed_cold_kpa'], {'FL': 165., 'FR': 166., 'RL': 160., 'RR': 161.})
        self.assertEqual(set(fields['installed_cold_source'].values()), {'garage_start'})

    def test_stop_installs_only_confirmed_tire_changes(self):
        tracker = InstalledPressure()
        tracker.update(row(IsInGarage=True, IsOnTrack=False)); tracker.update(row())
        # Arrival asks for LF, RF and LR; LF and RF are done, LR never confirmed, RR untouched.
        tracker.update(row(PlayerCarInPitStall=True, PitSvFlags=0x07, SessionTime=200.))
        tracker.update(row(PlayerCarInPitStall=True, PitSvFlags=0x04, PitSvLFP=168., SessionTime=210.))
        fields = tracker.update(row(PlayerCarInPitStall=False, PitSvFlags=0x04, LFwearM=1., SessionTime=230.))
        self.assertEqual(fields['installed_cold_kpa'], {'FL': 170., 'FR': 171., 'RL': None, 'RR': 161.})
        self.assertEqual(fields['installed_cold_source'],
                         {'FL': 'pit_tire_change', 'FR': 'pit_tire_change', 'RL': 'pit_unconfirmed', 'RR': 'garage_start'})
        snapshot_ = fields['pit_snapshot']
        self.assertEqual((snapshot_['stop'], snapshot_['tire_changed']), (1, {'FL': True, 'FR': True, 'RL': None, 'RR': False}))
        self.assertEqual(snapshot_['wheels']['FL']['wear_lmr'], [.85, 1., .85])

    def test_wear_reset_confirms_a_change_without_flags(self):
        tracker = InstalledPressure()
        tracker.update(row(IsInGarage=True, IsOnTrack=False)); tracker.update(row())
        tracker.update(row(PlayerCarInPitStall=True, PitSvFlags=None))
        fresh = {f'RR{kind}{side}': 1. for kind in ('wear',) for side in 'LMR'}
        fields = tracker.update(row(PitSvFlags=None, **fresh))
        self.assertEqual(fields['installed_cold_kpa']['RR'], 173.)
        self.assertEqual(fields['installed_cold_kpa']['FL'], 165.)

    def test_new_session_forgets_everything(self):
        tracker = InstalledPressure()
        tracker.update(row(IsInGarage=True, IsOnTrack=False)); tracker.update(row())
        self.assertEqual(set(tracker.update(row(subsession_id=7))['installed_cold_kpa'].values()), {None})


class StintRecordTests(unittest.TestCase):
    def test_stint_keeps_conditions_eligible_laps_and_the_stop_measurement(self):
        installed = {'FL': 165., 'FR': 166., 'RL': 160., 'RR': 161.}
        sources = dict.fromkeys(installed, 'garage_start')
        with tempfile.TemporaryDirectory() as temp:
            store = TireHistoryStore(Path(temp) / 'history.sqlite3')
            try:
                t = 0.
                for lap in range(3, 8):
                    for _ in range(31):
                        store.append(replace(snapshot(t), lap=lap, session_type='Race', track_temp_c=30. + lap,
                                             air_temp_c=20., fuel_l=80. - t / 100, installed_cold_kpa=installed,
                                             installed_cold_source=sources, fixed_setup=False))
                        t += 1
                store.append(replace(snapshot(t), lap=8, session_type='Race', on_pit_road=True,
                                     installed_cold_kpa=installed, installed_cold_source=sources))
                stop = {'stop': 1, 't': t + 30, 'tire_changed': dict.fromkeys(installed, True),
                        'wheels': {w: {'temp_lmr_c': [81., 85., 83.], 'wear_lmr': [.9, .88, .91]} for w in installed}}
                store.append(replace(snapshot(t + 31), lap=8, session_type='Race', on_pit_road=True, pit_snapshot=stop))
                data = json.loads(store.db.execute('SELECT data FROM tire_stints').fetchone()[0])
                conditions = data['conditions']
                self.assertEqual(conditions['installed_cold_kpa'], installed)
                self.assertEqual((conditions['air_temp_c'], conditions['track_temp_range_c']), (20., [33., 37.]))
                # Laps 3-4 are warm-up; lap 7 ends on track before the pit-road sample of lap 8.
                self.assertEqual([r['lap'] for r in data['eligible_laps']], [5, 6, 7])
                self.assertEqual(data['eligible_laps'][0]['FL_kpa'], 170.)
                self.assertEqual(data['pit_snapshot']['wheels']['FL']['temp_lmr_c'], [81., 85., 83.])
                observation = store.db.execute('SELECT data FROM tire_observations ORDER BY id DESC LIMIT 1').fetchone()[0]
                self.assertNotIn('pit_snapshot', json.loads(observation))
                exported = _summary(data)
                self.assertEqual(exported['conditions']['installed_cold_source']['FL'], 'garage_start')
                self.assertEqual(exported['pit_snapshot']['wheels']['RR']['wear_lmr'], [.9, .88, .91])
                self.assertEqual(_observation(json.loads(observation))['installed_cold_kpa']['FL'], None)
            finally:
                store.close()

    def test_installed_value_changing_inside_a_stint_is_not_reported(self):
        rows = [replace(snapshot(0.), installed_cold_kpa={'FL': 165.}, installed_cold_source={'FL': 'garage_start'}),
                replace(snapshot(1.), installed_cold_kpa={'FL': 170.}, installed_cold_source={'FL': 'pit_tire_change'})]
        self.assertIsNone(TireHistoryStore.conditions(rows)['installed_cold_kpa']['FL'])


if __name__ == '__main__':
    unittest.main()
