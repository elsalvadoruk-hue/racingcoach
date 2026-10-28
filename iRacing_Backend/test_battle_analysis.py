"""After-race battle analysis on a synthetic race: a round track, eight laps, one battle."""
import gzip
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import battle_analysis as ba

RADIUS = 300.
LENGTH = 2 * math.pi * RADIUS
HZ = 60
CORNERS = [{'id': 'C1', 'entry_pct': .40, 'apex_pct': .50, 'exit_pct': .60},
           {'id': 'C2', 'entry_pct': .85, 'apex_pct': .90, 'exit_pct': .95}]
BATTLE_LAP = 6


def speed_at(pct, lap):
    """60 m/s, slowing to 30 m/s at each corner apex. The battle lap is slower at C1."""
    v = np.full_like(pct, 60.)
    for c, depth in ((CORNERS[0], 30.), (CORNERS[1], 20.)):
        inside = np.abs(pct - c['apex_pct']) < .1
        v[inside] -= depth * np.cos((pct[inside] - c['apex_pct']) / .1 * math.pi / 2)
    if lap == BATTLE_LAP:
        hit = np.abs(pct - .5) < .1
        v[hit] -= 8 * np.cos((pct[hit] - .5) / .1 * math.pi / 2)
    return v


def race():
    t, laps, pcts, speeds = [], [], [], []
    now = 10.
    for lap in range(0, 9):
        pct = 0.
        while pct < 1:
            v = float(speed_at(np.array([pct]), lap)[0])
            t.append(now); laps.append(lap); pcts.append(pct); speeds.append(v)
            now += 1 / HZ
            pct += v / HZ / LENGTH
    t, laps, pcts, speeds = map(np.array, (t, laps, pcts, speeds))
    angle = pcts * 2 * math.pi
    x, y = RADIUS * np.cos(angle), RADIUS * np.sin(angle)
    lat = 49.3 + y / 6378137.0 * 180 / math.pi
    lon = 8.5 + x / (6378137.0 * math.cos(math.radians(49.3))) * 180 / math.pi
    brake = np.zeros_like(pcts)
    for c in CORNERS:
        brake[(pcts > c['entry_pct'] - .03) & (pcts < c['apex_pct'])] = .6
    throttle = np.where(brake > 0, 0., 1.)
    steer = np.full_like(pcts, .2)                      # always turning left on this circle
    return {'SessionTime': t, 'Lap': laps.astype(float), 'LapDistPct': pcts, 'Lat': lat, 'Lon': lon,
            'Speed': speeds, 'Brake': brake, 'Throttle': throttle, 'SteeringWheelAngle': steer,
            'Gear': np.full_like(pcts, 4.), 'OnPitRoad': np.zeros_like(pcts)}


def write_log(folder, ch, *, car='mclaren720sgt3', track='390', side=True):
    """10 Hz rows; a same-class car 8 m ahead through C1 on the battle lap, beside us for a moment."""
    header = {'format': ba.FORMAT, 'version': 3, 'game': 'iracing', 'run_id': 'run', 'track': 'Synthetic',
              'track_id': track, 'track_config': 'GP', 'track_length_m': LENGTH, 'car': car, 'player_idx': 0,
              'classes': {'0': {'est_lap_s': 40.}}, 'rate_s': .1, 'near_m': 200.,
              'fields': ['idx', 'class_id', 'gap_m', 'est_time_s', 'lap_completed', 'position', 'class_position',
                         'on_pit_road', 'track_surface', 'local_x_m', 'local_z_m', 'path_lateral_m', 'track_edge_m'],
              'own_fields': ['lap_completed', 'distance_pct', 'speed_mps', 'brake', 'throttle', 'steering_rad',
                             'car_left_right', 'on_pit_road', 'path_lateral_m', 'track_edge_m']}
    t = ch['SessionTime']
    rows = []
    for i in range(0, len(t), 6):
        lap, pct = int(ch['Lap'][i]), float(ch['LapDistPct'][i])
        cars, clr = [], 1
        if lap == BATTLE_LAP and .35 < pct < .9:
            gap = 8. - 30 * (pct - .35)            # we close in, are level near .62, then lead
            if .6 < pct < .64 and side:
                clr = 2
            cars.append([7, 0, round(gap, 2), None, lap, 3, 3, 0, 3, None, None, None, None])
        rows.append({'t': round(float(t[i]), 3),
                     'own': [lap, round(pct, 6), round(float(ch['Speed'][i]), 3), round(float(ch['Brake'][i]), 3),
                             round(float(ch['Throttle'][i]), 3), .2, clr, 0, None, None], 'c': cars})
    path = Path(folder) / 'traffic-20261028-200000-Synthetic.jsonl.gz'
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        handle.write(json.dumps(header) + '\n')
        for row in rows:
            handle.write(json.dumps(row) + '\n')
    return path


class Store:
    def get(self, identity):
        return CORNERS

    def find(self, *args):
        return CORNERS

    def names_for(self, definitions, track, config):
        return {'C1': {'number': '1', 'name': 'Synthetic', 'spoken': None}}


class BattleAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.ibt = self.folder / 'race.ibt'
        self.ibt.write_bytes(b'ibt')
        self.ch = race()
        ba._CACHE.clear()

    def telemetry(self, **meta):
        metadata = {'game': None, 'session_type': 'Race', 'car_platform_id': '188', 'car_path': 'mclaren720sgt3',
                    'car_name': 'McLaren', 'track_platform_id': '390', 'track_path': 'synthetic', 'track_name': 'Synthetic',
                    'track_config': 'GP', 'track_version': '1', 'track_length_m': LENGTH, **meta}
        return SimpleNamespace(metadata=metadata, channels=self.ch, path=self.ibt)

    def report(self, telemetry=None, folder=None):
        return ba.battle_report(telemetry or self.telemetry(), traffic_folder=folder or self.folder,
                                corner_store=Store(), reader=None)

    def test_finds_the_race_log_and_describes_the_battle(self):
        write_log(self.folder, self.ch)
        result = self.report()
        self.assertEqual(result['status'], 'READY', result)
        self.assertEqual(result['laps_used'], 8)
        scene = next(s for s in result['scenes'] if s['rival'] == 7)
        self.assertEqual(scene['lap'], BATTLE_LAP)
        self.assertEqual(scene['outcome'], 'passed')
        self.assertGreater(scene['beside_s'], 0)
        beside = [f for f in scene['frames'] if f['beside']]
        self.assertTrue(beside and all(f['beside'] == 'L' for f in beside))
        # beside us on the left: at least a car width left of our own line
        self.assertTrue(all(f['rival_lat'][0] >= ba.CAR_WIDTH_M - 1 for f in beside))

    def test_corner_has_conditional_trajectories_instead_of_a_transplanted_pass(self):
        write_log(self.folder, self.ch)
        scene = next(s for s in self.report()['scenes'] if s['rival'] == 7)
        corner = next(c for c in scene['corners'] if c['corner'] == 'C1')
        self.assertEqual(corner['label'], 'T1 Synthetic')
        self.assertNotIn('usual', corner)
        self.assertNotIn('best', corner)
        self.assertEqual(corner['simulation']['status'], 'READY')
        self.assertNotIn(BATTLE_LAP, corner['simulation']['training_laps'])
        for alt in corner['alternatives']:
            self.assertGreater(len(alt['responses'][0]['trajectory']), 10)
            self.assertNotIn('gain_s', alt)

    def test_no_effect_actions_are_not_presented_as_different_choices(self):
        write_log(self.folder, self.ch)
        scene = next(s for s in self.report()['scenes'] if s['rival'] == 7)
        corner = next(c for c in scene['corners'] if c['corner'] == 'C1')
        self.assertTrue(corner['simulation']['omitted'])
        self.assertTrue(all(item['reason'] == 'NO_EFFECT_UNDER_TRAFFIC' for item in corner['simulation']['omitted']))

    def test_no_side_signal_means_nose_to_tail(self):
        write_log(self.folder, self.ch, side=False)
        scene = next(s for s in self.report()['scenes'] if s['rival'] == 7)
        short = [f for f in scene['frames'] if f['gap_clamped']]
        self.assertTrue(short)
        self.assertTrue(all(abs(f['gap_m']) > ba.CAR_LENGTH_M for f in short))

    def test_unavailable_reasons(self):
        self.assertEqual(self.report(self.telemetry(session_type='Practice'))['reason'], 'NOT_A_RACE')
        self.assertEqual(self.report(self.telemetry(game='lmu'))['reason'], 'GAME_NOT_SUPPORTED')
        self.assertEqual(self.report()['reason'], 'NO_MATCHING_TRAFFIC_LOG')
        write_log(self.folder, self.ch, car='bmwm4gt3')
        self.assertEqual(self.report()['reason'], 'NO_MATCHING_TRAFFIC_LOG')
        self.assertEqual(self.report(folder=self.folder / 'missing')['reason'], 'NO_TRAFFIC_LOGS')

    def test_two_logs_of_one_race_are_never_combined(self):
        first = write_log(self.folder, self.ch)
        first.rename(self.folder / 'traffic-20261028-200001-Synthetic.jsonl.gz')
        write_log(self.folder, self.ch)
        self.assertEqual(self.report()['reason'], 'AMBIGUOUS_TRAFFIC_LOG')

if __name__ == '__main__':
    unittest.main()
