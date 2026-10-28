"""Battle rollouts must react to traffic, keep a common clock, and disclose model limits."""
import copy
import json
import unittest

import numpy as np

import battle_simulation as sim
from test_battle_analysis import race, LENGTH


def fixture():
    a = np.linspace(0, 2 * np.pi, 500, endpoint=False)
    geometry = {'grid': 500, 'ref': np.column_stack([300 * np.cos(a), 300 * np.sin(a)]),
                'normal': np.column_stack([np.cos(a), np.sin(a)])}
    model = {'beta': np.array([5., 12., .2, .3]), 'accel_bounds': [-12., 5.],
             'speed_range': [8., 100.], 'target_speed': np.full(160, 35.),
             'training_laps': [1, 2], 'validation': {'distance_p90_m': .5}}
    frames = []
    for t in np.arange(0, 4.01, .1):
        pct = (.1 + 30 * t / LENGTH) % 1
        frames.append({'t': round(float(t), 2), 'pct': pct, 'me': sim._at(geometry, pct, 0).tolist(),
                       'speed_kph': 108., 'gap_m': 18., 'beside': None, 'brake': 0., 'throttle': .5,
                       'rival_lat': [-100, 100], 'other_nearby': 0})
    return model, geometry, {'frames': frames}, {'t_entry': 2., 'lap': 6}


class BattleSimulationTests(unittest.TestCase):
    def test_shared_state_clock_bounded_actuators_and_finite_json(self):
        model, geometry, scene, corner = fixture()
        result = sim.alternatives(scene, corner, model, geometry, LENGTH)
        self.assertEqual(result['status'], 'READY')
        self.assertTrue(result['alternatives'])
        first = result['baselines'][0]['trajectory'][0]
        for alt in result['alternatives']:
            for response in alt['responses']:
                frames = response['trajectory']
                self.assertEqual(frames[0], first)
                self.assertEqual([f['t'] for f in frames], [f['t'] for f in result['baselines'][0]['trajectory']])
                self.assertTrue(all(f['lat'] == frames[0]['lat'] for f in frames))
                for a, b in zip(frames, frames[1:]):
                    dt = b['t'] - a['t']
                    self.assertGreaterEqual(b['distance_m'], a['distance_m'])
                    self.assertLessEqual(abs(b['brake'] - a['brake']), 2.5 * dt + .002)
                    self.assertLessEqual(abs(b['throttle'] - a['throttle']), 3 * dt + .002)
                    self.assertLessEqual((b['speed_kph'] - a['speed_kph']) / 3.6 / dt, 5.1)
        json.dumps(result, allow_nan=False)

    def test_predictions_ignore_future_rival_and_retrospective_lateral_estimates(self):
        model, geometry, scene, corner = fixture()
        a = sim.alternatives(scene, corner, model, geometry, LENGTH)
        changed = copy.deepcopy(scene)
        for f in changed['frames']:
            f['rival_lat'] = [200, 300]
            if f['t'] > a['start_t']:
                f.update(gap_m=-100, beside='R', rival_speed_kph=0)
        b = sim.alternatives(changed, corner, model, geometry, LENGTH)
        self.assertEqual(a, b)

    def test_opponent_braking_changes_ego_response(self):
        model, geometry, scene, corner = fixture()
        result = sim.alternatives(scene, corner, model, geometry, LENGTH)
        responses = {r['id']: r['trajectory'] for r in result['alternatives'][0]['responses']}
        self.assertLess(responses['brakes'][-1]['speed_kph'], responses['holds_speed'][-1]['speed_kph'])
        self.assertNotEqual(responses['brakes'][-1]['me'], responses['holds_speed'][-1]['me'])

    def test_rival_reacts_when_ego_is_ahead(self):
        model, geometry, _, _ = fixture()
        initial = {'s': 100., 'v': 20., 'lat': 0., 'brake': 0., 'throttle': 0.}
        rival = {'gap': -10., 'speed': 30., 'lat': 0., 'lat_width': 1., 'side_known': True}
        times = np.arange(0, 2.01, .1)
        same = sim.rollout(model, initial, rival, geometry, LENGTH, 'baseline', 'holds_speed', times)
        away = sim.rollout(model, initial, dict(rival, lat=10.), geometry, LENGTH, 'baseline', 'holds_speed', times)
        self.assertLess(same['frames'][-1]['rival_speed_kph'], away['frames'][-1]['rival_speed_kph'])

    def test_missing_data_reasons(self):
        model, geometry, scene, corner = fixture()
        self.assertEqual(sim.alternatives(scene, corner, None, geometry, LENGTH)['reason'], 'INSUFFICIENT_DYNAMICS')
        broken = {'frames': [f for f in scene['frames'] if not 1.5 < f['t'] < 2.]}
        self.assertEqual(sim.alternatives(broken, corner, model, geometry, LENGTH)['reason'], 'DISCONTINUOUS_SCENE')
        short = {'frames': [f for f in scene['frames'] if f['t'] >= .9]}
        short['frames'] = [short['frames'][0]] + [f for f in short['frames'] if f['t'] >= 1.4]
        self.assertEqual(sim.alternatives(short, corner, model, geometry, LENGTH)['reason'], 'SHORT_RIVAL_HISTORY')

    def test_uncertainty_or_third_car_prevents_ranking(self):
        model, geometry, scene, corner = fixture()
        model['validation']['distance_p90_m'] = 100.
        result = sim.alternatives(scene, corner, model, geometry, LENGTH)
        self.assertTrue(all(not alt['rankable'] for alt in result['alternatives']))
        for f in scene['frames']:
            f['other_nearby'] = 1
        result = sim.alternatives(scene, corner, model, geometry, LENGTH)
        self.assertTrue(result['multi_car_unmodelled'])
        self.assertTrue(all(not alt['rankable'] for alt in result['alternatives']))
        self.assertEqual(result['lateral_planning'], 'UNAVAILABLE_TRACK_EDGES')

    def test_calibration_excludes_target_and_validates_on_separate_lap(self):
        channels = race()
        model = sim.fit_vehicle(channels, 6, [1, 2, 3, 4, 5, 6, 7])
        self.assertNotIn(6, model['training_laps'])
        self.assertNotIn(8, model['training_laps'])
        validation = model['validation']
        self.assertEqual(validation['status'], 'HELD_OUT_LAP')
        self.assertNotIn(validation['lap'], validation['training_laps'])
        self.assertGreater(validation['anchors'], 0)
        self.assertGreaterEqual(validation['distance_p90_m'], 0)
        changed = copy.deepcopy(channels)
        changed['Speed'][changed['Lap'] == 6] *= .5
        recalibrated = sim.fit_vehicle(changed, 6, [1, 2, 3, 4, 5, 6, 7])
        np.testing.assert_array_equal(model['beta'], recalibrated['beta'])
        np.testing.assert_array_equal(model['target_speed'], recalibrated['target_speed'])


if __name__ == '__main__':
    unittest.main()
