"""Synthetic observation boundaries and exact phase accounting; no personal data."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from overlay_diagnostics import CornerDiagnostics, impact_card
from overlay_studio import CoachingDashboard, defaults, select_modules
from live_damage import DamageWatch


def fixture(lap=1, start=100., shift=.003, relift=False, rebrake=False):
    pct = np.linspace(.19, .31, 361)
    def pedals(shift):
        brake = np.interp(pct, [.19, .21+shift, .212+shift, .228+shift, .236+shift, .31], [0, 0, .8, .8, 0, 0])
        throttle = np.interp(pct, [.19, .26+shift, .265+shift, .29+shift, .31], [0, 0, .6, 1, 1])
        return brake, throttle
    reference_brake, reference_throttle = pedals(0)
    brake, throttle = pedals(shift)
    if relift:
        throttle[(pct >= .279) & (pct <= .286)] = .1
    if rebrake:
        brake[(pct >= .265) & (pct <= .272)] = .3
    times = start+(pct-.19)*102
    rows = [dict(session_time=float(at), distance_pct=float(p), brake=float(b), throttle=float(t), speed_mps=49.,
                 on_track=True, on_pit_road=False, is_replay=False, lap_number=lap, incidents=0,
                 session_flags=0, car_left_right=0, fuel_l=30., track_temp_c=25., track_wetness=1, tire_compound=0)
            for at, p, b, t in zip(times, pct, brake, throttle)]
    end = start+(.30-.19)*102
    reference = SimpleNamespace(grid={'Brake': reference_brake, 'Throttle': reference_throttle},
                                times=(pct-.19)*100, distance=pct)
    # Real arrays have no value equality; mirror AlignedReference identity.
    class Reference:
        pass
    ref = Reference(); ref.__dict__.update(reference.__dict__)
    sample = next(row for row in rows if row['session_time'] >= end)
    view = {'connected': True, 'run_id': 'synthetic', 'track_length_m': 5000.,
            '_history': tuple(rows), 'snapshot': sample, 'reference_status': {'state': 'READY', 'source': 'PERSONAL'},
            'corner_pass': dict(id='C4', call='C4', lap=lap, clean=True, entry_pct=.2, apex_pct=.25, exit_pct=.3,
                                exit_at=end, duration_s=10.2)}
    return ref, view


class DiagnosticsTests(unittest.TestCase):
    def continue_to(self, engine, ref, view):
        at = view['snapshot']['session_time']
        if engine.last_at is not None:
            for now in np.arange(engine.last_at+.5, at, .5):
                engine.build(ref, {**view, 'snapshot': {**view['snapshot'], 'session_time': float(now)}, 'corner_pass': None})
        return engine.build(ref, view)

    def test_first_observed_difference_and_three_phase_sum(self):
        ref, view = fixture()
        result = CornerDiagnostics().build(ref, view)
        self.assertTrue(result['divergence']['available'])
        self.assertIn('제동 시작부터', result['divergence']['title'])
        self.assertEqual(result['divergence']['metric'], '15.0')
        phases = result['phases']
        self.assertTrue(phases['available'])
        self.assertAlmostEqual(float(phases['metric']), .2, places=3)
        self.assertAlmostEqual(sum(float(value[:-1]) for _, value in phases['stats']), .2, places=3)

    def test_once_per_pass_cache_and_bounded_repetition_without_reference(self):
        engine = CornerDiagnostics()
        for lap in range(1, 7):
            _, view = fixture(lap=lap, start=lap*20., relift=True, rebrake=lap % 2 == 0)
            result = self.continue_to(engine, None, view)
        self.assertTrue(result['pattern']['available'])
        self.assertEqual(result['pattern']['metric'], '5 / 3')
        self.assertTrue(result['common']['available'])
        self.assertFalse(result['divergence']['available'])
        with patch.object(engine, '_measure', side_effect=AssertionError('must be cached')):
            self.assertIs(engine.build(None, view), result)
        self.assertEqual(len(next(iter(engine.rows.values()))), 6)

    def test_short_input_noise_does_not_count_as_relift_or_rebrake(self):
        engine = CornerDiagnostics()
        for lap in range(1, 4):
            _, view = fixture(lap=lap, start=lap*20.)
            view['_history'][280]['throttle'] = .01
            view['_history'][230]['brake'] = .5
            result = self.continue_to(engine, None, view)
        self.assertEqual(result['pattern']['metric'], '0 / 0')

    def test_missing_channel_gap_wrong_lap_or_traffic_does_not_invent_result(self):
        for kind in ('brake', 'gap', 'traffic', 'replay', 'pit', 'backwards', 'incidents'):
            ref, view = fixture()
            rows = list(view['_history'])
            if kind == 'brake': rows[150]['brake'] = None
            elif kind == 'gap': del rows[130:150]
            elif kind == 'traffic': rows[150]['car_left_right'] = 2
            elif kind == 'replay': rows[150]['is_replay'] = True
            elif kind == 'pit': rows[150]['on_pit_road'] = True
            elif kind == 'backwards': rows[150]['distance_pct'] = .1
            elif kind == 'incidents': rows[150]['incidents'] = 1
            view['_history'] = rows
            with self.subTest(kind=kind):
                result = CornerDiagnostics().build(ref, view)
                self.assertFalse(any(item['available'] for item in result.values()))
        ref, view = fixture(); view['corner_pass']['clean'] = False
        self.assertEqual(CornerDiagnostics().build(ref, view), {})

    def test_reference_hole_and_held_brake_preserve_unknown_phase(self):
        ref, view = fixture()
        ref.times[150:160] = np.nan
        result = CornerDiagnostics().build(ref, view)
        self.assertFalse(result['phases']['available'])
        ref, view = fixture(); ref.grid['Brake'][:] = .8
        result = CornerDiagnostics().build(ref, view)
        self.assertFalse(result['phases']['available'])

    def test_common_requires_known_similar_conditions_and_five_distinct_laps(self):
        engine = CornerDiagnostics()
        for lap in range(1, 6):
            _, view = fixture(lap=lap, start=lap*20.)
            for row in view['_history']:
                row['fuel_l'] = lap*20
            result = self.continue_to(engine, None, view)
        self.assertFalse(result['common']['available'])
        self.assertTrue(result['pattern']['available'])

    def test_run_reference_geometry_gap_and_pit_never_reuse_history(self):
        for change in ('run', 'reference', 'geometry', 'gap', 'pit'):
            engine = CornerDiagnostics(); ref, view = fixture()
            engine.build(ref, view)
            new = deepcopy(view); new['snapshot']['session_time'] += .1
            if change == 'run': new['run_id'] = 'new'
            elif change == 'reference': ref, _ = fixture()
            elif change == 'geometry': new['corner_pass']['apex_pct'] = .251
            elif change == 'gap': new['snapshot']['session_time'] += 3
            elif change == 'pit': new['snapshot']['on_pit_road'] = True
            engine.build(ref, new)
            with self.subTest(change=change):
                self.assertTrue(all(len(rows) <= 1 for rows in engine.rows.values()))

    def test_diagnostics_are_real_dashboard_cards_and_selectable(self):
        ref, view = fixture(); view['diagnostics'] = CornerDiagnostics().build(ref, view)
        dashboard = CoachingDashboard().build(view)
        config = defaults(); config['selected']['practice'] = ['divergence', 'phases']
        self.assertTrue(dashboard['result'])
        self.assertEqual(select_modules(config, 'practice', dashboard)['ids'], ['divergence', 'phases'])
        self.assertNotIn('pending', dashboard['items']['divergence'])


class ImpactTests(unittest.TestCase):
    def watch(self):
        watch = DamageWatch()
        watch.history.extend([dict(lap=1, time=100., vmax=200., flat_s=10., steer=0.),
                              dict(lap=2, time=102., vmax=202., flat_s=10., steer=.2)])
        watch.contact_lap = 3
        watch.finished = dict(lap=4, vmax=197., flat_s=10., steer=.6)
        return watch

    def sample(self, **overrides):
        return dict(on_track=True, is_replay=False, on_pit_road=False, incidents=2, lap_number=5, **overrides)

    def test_observed_speed_time_steering_do_not_use_estimated_loss(self):
        watch = self.watch(); result = watch.lap_done(101.2)
        self.assertGreater(result['loss_per_lap_s'], 0)  # old estimate still exists for old callers
        view = {'snapshot': self.sample(), 'damage_observation': watch.overlay_view(self.sample())}
        item = impact_card(view)
        self.assertEqual(item['metric'], '-4.0')
        self.assertEqual(item['stats'], [['랩타임 차이', '+0.20s'], ['직선 조향 차이', '+0.5°']])
        self.assertNotIn('loss_per_lap_s', view['damage_observation']['measured'])
        view['snapshot']['lap_number'] = 10
        self.assertFalse(impact_card(view)['available'])

    def test_missing_signal_and_pit_are_explicit_waiting(self):
        watch = self.watch(); watch.lap_done(104.)
        sample = self.sample(); sample['on_pit_road'] = True
        self.assertIsNone(watch.overlay_view(sample)['measured'])
        empty = DamageWatch(); sample = self.sample(); sample['incidents'] = None
        self.assertIn('확인할 수 없습니다', empty.overlay_view(sample)['reason'])
        self.assertFalse(impact_card({'snapshot': sample, 'damage_observation': empty.overlay_view(sample)})['available'])

    def test_gap_or_partial_lap_cannot_supply_hud_baseline_but_old_audio_result_is_preserved(self):
        watch = self.watch()
        sample = self.sample(); sample.update(session_time=100., distance_pct=.5, speed_mps=50., lap_number=2)
        watch.ingest(sample)  # starts part-way through baseline lap 2
        watch.finished = dict(lap=4, vmax=197., flat_s=10., steer=.6)
        result = watch.lap_done(104.)
        self.assertIsNotNone(result)
        self.assertIsNone(watch.observed)
        watch = self.watch(); watch.lap_done(104.)
        self.assertIsNotNone(watch.observed)
        sample.update(lap_number=5); watch.ingest(sample)
        self.assertIsNone(watch.observed)


    def test_complete_live_laps_produce_observed_change_for_incidents_and_lmu_detachment(self):
        for source in ('incidents', 'parts_detached'):
            watch = DamageWatch(); previous_lap = None
            for tick in range(5*1800+1):
                lap, index = tick//1800+1, tick % 1800
                after = lap >= 5
                sample = dict(session_time=tick/30, lap_number=lap, distance_pct=index/1800,
                              speed_mps=(248. if after else 252.)/3.6, throttle=1., yaw_rate=0.,
                              steering_rad=np.deg2rad(1. if after else .5).item(),
                              on_track=True, on_pit_road=False, is_replay=False, session_flags=0, car_left_right=0,
                              incidents=2 if source == 'incidents' and tick >= 3*1800+100 else 0 if source == 'incidents' else None,
                              engine_warnings=0x100 if source == 'parts_detached' and lap == 4 and index >= 100 else 0)
                watch.ingest(sample)
                if previous_lap is not None and lap != previous_lap and previous_lap != 4:
                    watch.lap_done(60.2 if previous_lap == 5 else 60.)
                previous_lap = lap
            with self.subTest(source=source):
                observation = watch.overlay_view(sample)['measured']
                self.assertEqual(observation['baseline_laps'], 2)
                self.assertEqual(observation['speed_delta_kph'], -4.)
                self.assertEqual(observation['steer_delta_deg'], .5)


if __name__ == '__main__':
    unittest.main()
