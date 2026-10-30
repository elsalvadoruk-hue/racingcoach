"""Game-specific guidance regressions; synthetic inputs, no user files or services."""
import unittest
from unittest.mock import patch
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import threading
from live_corners import LiveCornerCoach
from live_damage import DamageWatch
from live_pit import stall_estimate
from spotter_context import TrafficApproach
from lmu_irsdk import LmuIrsdk
from formation_text import build_formation, FormationContext, FORMATION_ITEMS, pages, official_context
from formation_archive import FormationArchive
from live_formation import FormationBrief, RaceHistory
from live_wheels import wheel_view
from overlay_studio import defaults, normalize
from overlay_presentation import CoachingPresentation


def race(at=0):
    return {'session_type': 'Race', 'player_idx': 0, 'cars': [
        {'idx': i, 'class_id': 1, 'lap_completed': 0, 'distance_pct': .1 + i * .01 + at * .01,
         'on_pit_road': False, 'track_surface': 3, 'last_lap_s': -1, 'class_est_lap_s': 100}
        for i in range(2)]}


class GuidanceStages(unittest.TestCase):
    def test_first_lap_history_does_not_require_lap_time(self):
        watch = TrafficApproach()
        for at in range(6):
            watch.ingest(race(at), at)
        self.assertEqual(len(watch.tracks[0]), 6)
        self.assertAlmostEqual(watch.view(5)['ahead']['time_gap_s'], 1)

    def test_unknown_service_rule_has_no_single_stall_prediction(self):
        self.assertIsNone(stall_estimate(25, 2.5, True, None))
        self.assertEqual(stall_estimate(25, 2.5, True, 'CONCURRENT'), 21)
        self.assertAlmostEqual(stall_estimate(25, 2.5, True, 'SEQUENTIAL'), 31.4)

    def test_scoring_does_not_cross_explicit_session(self):
        sdk = LmuIrsdk(normalised=True)
        sdk._hold_scoring({'session_phase': 3, 'session_key': 'old', 'vehicle_id': 1,
                           'telemetry_et': 10, 'scoring_et': 10})
        new = sdk._hold_scoring({'session_phase': None, 'session_key': 'new', 'vehicle_id': 1,
                                'telemetry_et': 11})
        self.assertEqual(new['session_key'], 'new')
        self.assertIsNone(new.get('session_phase'))

    def test_native_impact_observed_once_not_repeated_frames(self):
        watch = DamageWatch()
        def sample(at, impact):
            return {'session_time': at, 'lap_number': 1, 'speed_mps': 20, 'on_track': True,
                    'on_pit_road': False, 'is_replay': False, 'lmu_last_impact_et': impact}
        watch.ingest(sample(10, 2))
        self.assertIsNone(watch.contact_lap)
        watch.ingest(sample(10.1, 10.05))
        self.assertEqual(watch.contact_lap, 1)
        watch.contact_lap = None
        watch.ingest(sample(10.2, 10.05))
        self.assertIsNone(watch.contact_lap)

    def test_impact_inside_a_long_observation_gap_is_not_a_fresh_contact(self):
        watch = DamageWatch()
        sample = {'session_time': 10, 'lap_number': 1, 'speed_mps': 20, 'on_track': True,
                  'on_pit_road': False, 'is_replay': False, 'lmu_last_impact_et': 2}
        watch.ingest(sample)
        watch.ingest({**sample, 'session_time': 20, 'lmu_last_impact_et': 19})
        self.assertIsNone(watch.contact_lap)

    def test_traffic_pass_measured_but_never_solo_reference(self):
        coach = LiveCornerCoach()
        coach.key = ('lmu', 'car', 'track', '')
        item = {'id': 'T1', 'lap': 1, 'duration_s': 2, 'traffic_context': 'TRAFFIC_PRESENT',
                'clean': True, 'apex_pct': .2, 'min_speed_mps': 20}
        coach._finished(item, 1000)
        self.assertEqual(coach.last_observation['duration_s'], 2)
        self.assertIsNone(coach.last_measurement)
        coach._lap_finished(1, 1000)
        self.assertFalse(coach.best[coach.key])

    def test_unknown_traffic_is_not_a_solo_reference(self):
        self.assertFalse(LiveCornerCoach._eligible({'clean': True, 'traffic_context': 'UNKNOWN'}))
        self.assertTrue(LiveCornerCoach._eligible({'clean': True, 'traffic_context': 'SOLO'}))

    def test_integrated_braking_does_not_reverse_and_resets_at_session(self):
        sdk = LmuIrsdk(normalised=True)
        values = [sdk._project(0, 1000, 60 - i, 10, 10 + i / 10, 5000, 'a') for i in range(41)]
        self.assertTrue(all(b >= a for a, b in zip(values, values[1:])))
        self.assertAlmostEqual(values[-1], 1160)
        self.assertEqual(sdk._project(0, 100, 0, 1, 1, 5000, 'b'), 100)

    def test_projection_cannot_cross_line_or_continue_after_gap(self):
        sdk = LmuIrsdk(normalised=True)
        self.assertLess(sdk._project(0, 4999, 100, 0, .2, 5000, 'a'), 5000)
        self.assertIsNone(sdk._project(0, 1000, 60, 0, 5, 5000, 'a'))

    def test_unknown_motion_waits_for_a_new_scoring_anchor(self):
        sdk = LmuIrsdk(normalised=True)
        self.assertEqual(sdk._project(0, 1000, 60, 10, 10, 5000, 'a'), 1000)
        self.assertGreater(sdk._project(0, 1000, 50, 10, 10.1, 5000, 'a'), 1000)
        self.assertIsNone(sdk._project(0, 1000, None, 10, 10.2, 5000, 'a'))
        self.assertIsNone(sdk._project(0, 1000, 30, 10, 10.3, 5000, 'a'))
        self.assertEqual(sdk._project(0, 1100, 30, 10.4, 10.4, 5000, 'a'), 1100)
        self.assertIsNone(sdk._project(0, 1100, 30, 10.4, 11.5, 5000, 'a'))
        self.assertIsNone(sdk._project(0, 1100, 30, 10.4, 11.6, 5000, 'a'))

    def test_repeated_source_time_neither_erases_history_nor_refreshes_age(self):
        watch = TrafficApproach()
        for at in range(6):
            watch.ingest({**race(at), 'sample_time': at}, 100 + at)
        track = list(watch.tracks[0])
        watch.ingest({**race(5), 'sample_time': 5}, 106)
        self.assertEqual(list(watch.tracks[0]), track)
        self.assertFalse(watch.view(108)['available'])
        watch.ingest({**race(6), 'sample_time': 6}, 106)
        self.assertEqual(len(watch.tracks[0]), 7)

    def test_traffic_context_requires_complete_fresh_coverage(self):
        from spotter_context import traffic_context
        r = race()
        r['sample_time'] = 1
        self.assertEqual(traffic_context(r, 1), 'TRAFFIC_PRESENT')
        r['cars'][1]['distance_pct'] = .4
        self.assertEqual(traffic_context(r, 1), 'SOLO')
        r['cars'][1]['distance_pct'] = None
        self.assertEqual(traffic_context(r, 1), 'UNKNOWN')
        self.assertEqual(traffic_context(r, 4), 'UNKNOWN')

    def test_hold_has_explicit_age_and_expires(self):
        sdk = LmuIrsdk(normalised=True)
        sdk._hold_scoring({'session_phase': 3, 'session_key': 'a', 'vehicle_id': 0, 'scoring_et': 10, 'telemetry_et': 10})
        self.assertTrue(sdk._hold_scoring({'session_key': 'a', 'vehicle_id': 0, 'telemetry_et': 14})['scoring_held'])
        self.assertIsNone(sdk._hold_scoring({'session_key': 'a', 'vehicle_id': 0, 'telemetry_et': 14.01}).get('session_phase'))

    def test_pit_contamination_is_rejected_without_erasing_normal_measurement(self):
        from live_pit import PitLossWatch
        def drive(contamination):
            watch, found, tick = PitLossWatch(), [], 0
            for lap, seconds in ((1, 100), (2, 100), (3, 137), (4, 100), (5, 100)):
                for step in range(seconds * 10):
                    t = step / 10
                    pit = lap == 3 and 95 <= t < 127
                    sample = {'session_time': tick / 10, 'lap_number': lap, 'session_state': 4, 'on_track': True,
                              'on_pit_road': pit, 'speed_mps': 0 if pit and 105 <= t < 117 else 60,
                              'incidents': 0, 'session_flags': 0}
                    if lap == 3 and 20 <= t < 40:
                        sample.update(contamination)
                    value = watch.ingest(sample, 100)
                    if value is not None:
                        found.append(value)
                    tick += 1
            return found
        self.assertAlmostEqual(drive({})[0], 25, delta=.2)
        for bad in ({'session_flags': 8}, {'incidents': 2}, {'tow_time_s': 1}, {'pit_repair_left_s': 10}, {'speed_mps': 0}):
            with self.subTest(bad=bad):
                self.assertEqual(drive(bad), [])

    def test_wheel_missing_recovery_zero_and_game_boundary(self):
        rows = [{'position': p, 'flat': False, 'detached': False, 'pressure_kpa': 0,
                 'temperature_c': [70, 71, 72], 'brake_temp_c': 400, 'wear_fraction': .7} for p in ('FL','FR','RL','RR')]
        s = {'session_time': 10, 'lmu_wheel_sample_time': 10, 'lmu_wheels': rows, 'on_track': True, 'is_replay': False}
        v = wheel_view(s, 'lmu')
        self.assertEqual(v['wheels'][0]['pressure_kpa'], 0)
        self.assertIs(v['wheels'][0]['flat'], False)
        rows[0]['flat'] = True
        self.assertTrue(wheel_view(s, 'lmu')['wheels'][0]['flat'])
        self.assertFalse(wheel_view({**s, 'lmu_wheels': None}, 'lmu')['available'])
        rows[0]['flat'] = False
        self.assertIs(wheel_view(s, 'lmu')['wheels'][0]['flat'], False)
        self.assertFalse(wheel_view({**s, 'session_time': 13}, 'lmu')['available'])
        self.assertFalse(wheel_view(s, 'iracing')['supported'])

    def test_short_observed_formation_segment_gets_eight_when_time_allows(self):
        from test_live_formation import FACTS, sample
        for span in (.97, .5, .3, .1):
            brief, found = FormationBrief(8), []
            for tick in range(600):
                pct = 1 - span + span * tick / 600
                result = brief.step(sample(pct, speed=span * 30000 / 600, session_time=tick),
                                    {**FACTS, 'track_length_m': 30000}, 'r', tick)
                if result:
                    found.append(result)
            self.assertEqual(len(found), 8, span)
            self.assertEqual(found[-1][0], 'closing')
        self.assertEqual(brief.view()['origin_pct'], .9)

    def test_formation_stop_preserves_origin_green_cancels_and_jump_blocks(self):
        from test_live_formation import FACTS, sample
        brief = FormationBrief(8)
        brief.step(sample(.7), FACTS, 'a', 0)
        brief.step(sample(.7, speed=0), FACTS, 'a', 1)
        brief.step(sample(.701), FACTS, 'a', 2)
        self.assertEqual(brief.origin, .7)
        brief.step(sample(.1), FACTS, 'a', 3)
        self.assertIsNotNone(brief.blocked)
        self.assertIsNone(brief.step(sample(.2, state=4), FACTS, 'a', 4))
        brief.step(sample(.4), FACTS, 'b', 5)
        self.assertEqual(brief.origin, .4)


class FormationTextTests(unittest.TestCase):
    def setUp(self):
        self.sample = dict(session_state=3, session_time=10, on_track=True, on_pit_road=False, is_replay=False,
                           fuel_l=0., track_temp_c=25., air_temp_c=20., track_wetness=1)
        self.identity = dict(game='iracing', car_id='1', track_id='2', track_config='gp')
        self.race = {**race(), 'game': 'iracing'}

    def model(self, **kwargs):
        return build_formation(self.sample, self.race, self.identity, **kwargs)

    def test_exact_adopted_catalog_and_current_data_without_reference_or_voice(self):
        model = self.model()
        self.assertEqual(set(model['cards']), set(FORMATION_ITEMS))
        self.assertEqual(model['cards']['8']['text'], '0.0 L')
        self.assertTrue(model['cards']['9']['available'])
        self.assertEqual(model['grid']['text'], '출발 순위 확인 중')
        self.assertFalse(model['cards']['10']['supported'])
        self.assertFalse(model['cards']['22']['supported'])

    def test_lmu_never_gets_irating_or_invented_grid(self):
        self.identity['game'] = 'lmu'
        model = self.model(energy={'energy_pct': 0})
        self.assertEqual(model['cards']['22']['text'], '0.0%')
        self.assertFalse(model['cards']['15']['supported'])
        self.assertFalse(model['cards']['23']['supported'])
        self.assertEqual(model['grid']['text'], '출발 순위 확인 중')

    def test_grid_and_qualifying_use_distinct_tables_and_survive_green(self):
        sdk = {'QualifyResultsInfo': {'Results': [{'CarIdx': 0, 'Position': 0, 'ClassPosition': 0, 'FastestTime': 99}]}}
        session = {'SessionType': 'Race', 'ResultsPositions': [{'CarIdx': 0, 'Position': 5, 'ClassPosition': 3, 'LapsComplete': 0}]}
        ctx = official_context(sdk, set(), session, 'iracing', self.race['cars'], 0, 3)
        self.assertEqual(ctx['official_grid']['overall'], 5)
        self.assertEqual(ctx['qualifying']['overall'], 1)
        watch = FormationContext()
        watch.ingest(ctx, 'r')
        watch.ingest({'official_grid': None}, 'r')
        self.assertEqual(watch.data['official_grid']['overall'], 5)
        watch.ingest({}, 'new')
        self.assertFalse(watch.data)

    def test_rankings_wait_for_all_valid_own_class_ratings(self):
        for car in self.race['cars']:
            car['irating'] = 2000
        self.assertTrue(self.model()['cards']['15']['available'])
        self.race['cars'][1]['irating'] = None
        self.assertFalse(self.model()['cards']['15']['available'])
        self.race['cars'][1]['irating'] = 2000
        self.race['cars'][1]['is_ai'] = True
        self.assertFalse(self.model()['cards']['15']['available'])

    def test_long_note_pages_preserve_all_characters_and_negation(self):
        text = '피트 진입에서 급가속하지 마세요.\n' * 40
        self.assertEqual(''.join(pages(text)), text)
        self.assertEqual(''.join(self.model(archive={'notes': [{'id': 'n', 'text': text}]})['cards']['27']['pages']), text)

    def test_native_presentation_rotates_but_never_uses_missing_or_deleted_notes(self):
        p = CoachingPresentation()
        config = {**defaults(), 'formation_items': ['8', '27']}
        model = self.model(archive={'notes': [{'id': 'n', 'text': '가속하지 마세요'}]})
        view = {'connected': True, 'run_id': 'r', 'snapshot': self.sample}
        first = p.build(view, {'formation': model}, config, 'formation')
        self.assertEqual(first['layout']['ids'], ['8'])
        self.sample['session_time'] += 8
        second = p.build(view, {'formation': model}, config, 'formation')
        self.assertEqual(second['layout']['ids'], ['27'])
        model['cards']['27']['available'] = False
        self.assertEqual(p.build(view, {'formation': model}, config, 'formation')['items'], {})
        model['active'] = False
        self.assertEqual(p.build(view, {'formation': model}, config, 'formation')['items'], {})

    def test_custom_preferences_and_graph_survive_normalization(self):
        value = defaults()
        value['formation_items'] = []
        value['selected']['formation'] = ['goal']
        value['graph']['window'] = 16
        value['modes']['formation'] = 'inputs'
        self.assertEqual(normalize(value), value)

    def test_game_history_legacy_compatibility_status_and_correction(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'
            old = RaceHistory(path)
            legacy = {k: v for k, v in self.identity.items() if k != 'game'}
            old.add(legacy, {'class_position': 3})
            self.assertIsNone(old.last(self.identity))
            for provisional in (True, False):
                old.add(self.identity, {'id': 'r', 'status': 'DNF', 'start_source': 'OFFICIAL', 'start_position': 5,
                                        'class_position': 9, 'provisional': provisional})
            again = RaceHistory(path)
            self.assertEqual(again.count(self.identity), 1)
            self.assertFalse(again.last(self.identity)['provisional'])
            self.assertEqual(again.last(legacy)['class_position'], 3)
            self.assertIsNone(again.last({**self.identity, 'game': 'lmu'}))

    def test_archive_reads_on_worker_and_refreshes_edited_deleted_user_notes(self):
        main = threading.get_ident()
        calls = []
        notes = [{'id': 'n', 'text': '가속하지 마세요', 'source': 'TEXT', 'updated_at': 1,
                  'context': {'identity': self.identity}}]
        def lookup(identity):
            calls.append(threading.get_ident())
            return list(notes)
        with tempfile.TemporaryDirectory() as folder:
            owner = SimpleNamespace(race_history=RaceHistory(Path(folder) / 'history.json'),
                                    history_lookup=lambda identity: {'game': identity['game'], 'losses':['T1 · 이전 비교 관측']},
                                    notes_lookup=lookup)
            archive = FormationArchive(owner)
            self.addCleanup(archive.close)
            for at, text in ((0, '가속하지 마세요'), (3, '급가속하지 마세요')):
                notes[0]['text'] = text
                self.assertTrue(archive.submit(self.identity, at))
                archive.jobs.join()
                self.assertEqual(archive.view(self.identity)['notes'][0]['text'], text)
            notes.clear()
            archive.submit(self.identity, 6)
            archive.jobs.join()
            self.assertEqual(archive.view(self.identity)['notes'], [])
            self.assertTrue(all(ident != main for ident in calls))
            archive.close()

    def test_real_lmu_tick_keeps_text_with_voice_count_zero(self):
        from test_lmu_irsdk import frame
        from live_telemetry import LiveTelemetry
        raw = frame(100, phase=3, session_key='event', virtual_energy_raw=0., fuel_l=0.,
                    wheels=[{'position': p, 'flat': False, 'detached': False, 'temperature_c': [70,71,72],
                             'brake_temp_c': 400} for p in ('FL','FR','RL','RR')])
        class Source:
            def open(self):
                return {}
            def read(self, **kwargs):
                return raw
        sdk = LmuIrsdk(source_factory=Source, normalised=True)
        sdk._car = lambda *args: ('synthetic', 'Synthetic')
        live = LiveTelemetry(clock=lambda: 1000)
        live.formation.calls = 0
        live._tick(sdk)
        view = live.overlay_state()
        self.assertTrue(view['formation']['active'])
        self.assertEqual(view['formation']['cards']['8']['text'], '0.0 L')
        self.assertEqual(view['formation']['cards']['22']['text'], '0.0%')
        self.assertTrue(view['wheel_observation']['available'])
        self.assertEqual(view['snapshot']['session_state'], 3)

    def test_unknown_rule_range_reaches_pit_wall_without_single_auto_prediction(self):
        from live_telemetry import LiveTelemetry
        live = LiveTelemetry()
        live.identity = self.identity
        live.pit_knowledge.learn(self.identity, transit_loss_s=25, fuel_rate_lps=2.5)
        facts = live._pit_facts({'fuel_next_stop_l':25}, {'pit_sv_flags':15})
        self.assertEqual(facts['pit_stall_range_s'], [21.,31.4])
        self.assertIsNone(facts['pit_loss_auto_s'])
        live.game = 'lmu'
        facts = live._pit_facts({'fuel_next_stop_l':25}, {'pit_sv_flags':15})
        self.assertIsNone(facts['pit_loss_auto_s'])
        self.assertFalse(facts['pit_service_supported'])


if __name__ == '__main__':
    unittest.main()
