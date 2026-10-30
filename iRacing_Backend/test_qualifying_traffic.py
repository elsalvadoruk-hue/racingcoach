"""Lone qualifying stays unchanged; open qualifying never invents a pace."""
import unittest
from unittest.mock import patch

from live_formation import FormationBrief, grid_topics, usable
from live_telemetry import LiveTelemetry
from qualifying_traffic import QualifyingTraffic, is_open_qualifying, start_window
from race_radio import RaceRadio
import radio_phrases


def car(idx, pct, **more):
    return dict(idx=idx, distance_pct=pct, on_pit_road=False, track_surface=3, **more)


def sample(t=0., **more):
    return dict(session_time=t, session_num=1, session_state=4, session_flags=0,
                on_track=True, is_replay=False, on_pit_road=False, distance_pct=.8,
                speed_mps=40., brake=0., steering_rad=0., car_left_right=2,
                lap_number=4, lap_completed=3, workload_in_s=20.,
                session_time_remaining_s=600., incidents=0, **more)


class WindowTests(unittest.TestCase):
    def profiles(self, *times):
        return {i: [[t / 8] * 8] * 2 for i, t in enumerate(times)}

    def test_equal_pace_needs_no_extra_delay(self):
        result = start_window(self.profiles(100, 100, 100), {0: .8, 1: .9, 2: .65}, 0)
        self.assertEqual(result['status'], 'window')
        self.assertAlmostEqual(result['delay_s'], 0)
        self.assertAlmostEqual(result['front_s'], 10)
        self.assertAlmostEqual(result['rear_s'], 15)

    def test_slower_front_needs_more_gap_and_fast_rear_can_block_it(self):
        result = start_window(self.profiles(100, 110), {0: .8, 1: .85}, 0)
        self.assertEqual(result['status'], 'window')
        self.assertGreater(result['delay_s'], 9.)
        self.assertGreater(result['front_s'], 13.)
        blocked = start_window(self.profiles(100, 110, 80), {0: .8, 1: .85, 2: .5}, 0)
        self.assertEqual(blocked['status'], 'blocked')

    def test_equal_lap_times_can_still_catch_in_middle_of_lap(self):
        profiles = self.profiles(100, 100)
        profiles[1] = [[17.5] * 4 + [7.5] * 4] * 2
        result = start_window(profiles, {0: .8, 1: .9}, 0, max_delay=30.)
        self.assertEqual(result['status'], 'window')
        self.assertGreater(result['delay_s'], 9.)

    def test_all_cars_and_recent_variability_constrain_window(self):
        positions = {0: .8, 1: .9, 2: .5}
        profiles = self.profiles(100, 100, 80)
        self.assertEqual(start_window(profiles, positions, 0)['status'], 'blocked')
        profiles = self.profiles(100, 100)
        first = start_window(profiles, {0: .8, 1: .9}, 0)
        profiles[1].append([14.] * 8)
        varied = start_window(profiles, {0: .8, 1: .9}, 0)
        self.assertTrue(varied['status'] == 'blocked' or varied['delay_s'] > first['delay_s'])


class ObservationTests(unittest.TestCase):
    def feed(self, until=310):
        advisor = QualifyingTraffic()
        for t in range(until):
            meta = dict(session_type='Open Qualify', player_idx=0,
                        cars=[car(0, (t / 100) % 1), car(1, (t / 100 + .1) % 1)])
            advisor.ingest(meta, sample(t), float(t))
        return advisor, meta

    def test_full_laps_required_no_best_lap_or_class_estimate_substitution(self):
        advisor, _ = self.feed(210)
        self.assertEqual(advisor.result, {'status': 'unknown'})
        advisor, _ = self.feed()
        self.assertEqual(advisor.result['status'], 'window')
        self.assertEqual(len(advisor.laps[0]), 2)

    def test_pit_offtrack_unknown_new_car_and_stale_profiles_do_not_claim_clear(self):
        for field, value in [('on_pit_road', None), ('track_surface', 1), ('distance_pct', None)]:
            advisor, meta = self.feed()
            meta['cars'][1][field] = value
            advisor.ingest(meta, sample(310), 310.)
            self.assertEqual(advisor.result['status'], 'unknown')
        advisor, meta = self.feed()
        meta['cars'].append(car(3, .5))
        advisor.ingest(meta, sample(310), 310.)
        self.assertEqual(advisor.result['status'], 'unknown')
        advisor, meta = self.feed()
        advisor.ingest(meta, sample(1000), 1000.)
        self.assertEqual(advisor.result['status'], 'unknown')

    def test_flags_gap_pit_and_insufficient_remaining_time(self):
        advisor, meta = self.feed()
        s = sample(310)
        s['session_flags'] = 0x8
        advisor.ingest(meta, s, 310.)
        self.assertIsNone(advisor.result)
        self.assertEqual(advisor.marks, {})
        s['session_flags'] = 0
        s['session_time_remaining_s'] = 10.
        advisor.ingest(meta, s, 311.)
        self.assertIsNone(advisor.result)
        advisor, meta = self.feed()
        meta['cars'][1]['on_pit_road'] = True
        advisor.ingest(meta, sample(310), 310.)
        self.assertNotIn(1, advisor.laps)

    def test_missing_type_and_non_open_sessions_do_not_activate(self):
        for kind in ('Lone Qualify', 'Qualify', 'Practice', 'Race', None):
            advisor = QualifyingTraffic()
            meta = dict(session_type=kind, player_idx=0, cars=[car(0, .8), car(1, .9)])
            advisor.ingest(meta, sample(), 0.)
            self.assertIsNone(advisor.result)
            self.assertFalse(is_open_qualifying(meta))
        self.assertFalse(is_open_qualifying(dict(session_type='Open Qualify', game='lmu')))


class IntegrationTests(unittest.TestCase):
    def radio(self, kind='Open Qualify'):
        radio = RaceRadio()
        s = sample()
        meta = dict(session_type=kind, player_idx=0, cars=[car(0, .8), car(1, .9)])
        radio.ingest(s, 'run', 10., meta)
        radio.pending.clear()
        radio.sent.clear()
        return radio, s

    def test_lone_and_generic_qualifying_keep_old_filter_and_lap_calls(self):
        for kind in ('Lone Qualify', 'Qualify'):
            radio, _ = self.radio(kind)
            for key in ('traffic_slower_ahead', 'close_behind', 'hazard_slow_ahead', 'yellow', 'blue'):
                radio.emit(key, 10., priority=2)
            self.assertEqual(len(radio.pending), 0)
            for key in ('lap_time', 'personal_best', 'corner_focus', 'straight_brief', 'quali_result'):
                radio.emit(key, 10.)
            self.assertEqual(len(radio.pending), 5)

    def test_open_has_real_warnings_and_both_keep_lap_calls(self):
        radio, _ = self.radio()
        for key in ('traffic_slower_ahead', 'close_behind', 'hazard_slow_ahead', 'yellow', 'blue', 'lap_time'):
            radio.emit(key, 10., priority=2)
        self.assertEqual(len(radio.pending), 6)

    def test_spotter_active_on_first_open_sample_only(self):
        for kind, expected in [('Open Qualify', 1), ('Lone Qualify', 0), ('Qualify', 0)]:
            live = LiveTelemetry(clock=lambda: 10.)
            with patch.object(live.spotter, 'ingest') as ingest:
                live.ingest({}, sample(), dict(session_type=kind))
                self.assertEqual(ingest.call_count, expected, kind)

    def test_advice_is_revalidated_and_ack_limits_one_per_lap(self):
        radio, s = self.radio()
        radio.quali_traffic.result = dict(status='window', front_s=10., rear_s=15., delay_s=0.)
        radio._qualifying_gap(s, 10.)
        event = radio.pending[-1]
        self.assertEqual(event['key'], 'quali_gap_window')
        self.assertIn('앞차 약 10.0초.', event['text'])
        self.assertTrue(radio.valid(event, 10.))
        radio.quali_traffic.result['rear_s'] = 12.
        self.assertFalse(radio.valid(event, 10.))
        radio.quali_traffic.result['rear_s'] = 15.
        self.assertTrue(radio.ack(event['id'], 10.))
        radio._qualifying_gap(s, 10.1)
        self.assertEqual(len(radio.pending), 0)

    def test_expiry_on_line_pit_flags_stale_metadata_or_type_change(self):
        for field, value in [('distance_pct', .01), ('on_pit_road', True), ('session_flags', 0x8), ('workload_in_s', 2.)]:
            radio, s = self.radio()
            radio.quali_traffic.result = dict(status='unknown')
            radio._qualifying_gap(s, 10.)
            event = radio.pending[-1]
            radio.latest[field] = value
            self.assertFalse(radio.valid(event, 10.))
        radio, s = self.radio()
        radio.quali_traffic.result = dict(status='unknown')
        radio._qualifying_gap(s, 10.)
        event = radio.pending[-1]
        radio.last_sample = 11.4
        self.assertFalse(radio.valid(event, 11.5))
        radio.open_qualifying = False
        self.assertFalse(radio.valid(event, 10.))


class FrontRowTests(unittest.TestCase):
    def test_single_class_front_row_and_counts_stay_the_same(self):
        for position, hello, station in [(1, 'form_pole', 'form_pole_station'),
                                         (2, 'form_front_row', 'form_front_station'),
                                         (7, 'form_hello', 'form_hold_station'),
                                         (None, 'form_hello', 'form_hold_station')]:
            facts = dict(grid_position=position, class_position=1)
            topics = grid_topics(position)
            self.assertIn(hello, dict(usable(topics[0], facts)))
            start = next(t for t in topics if t[0] == 'start')
            self.assertIn(station, dict(usable(start, facts)))
            self.assertEqual(len(FormationBrief(2).planned(facts, 'run')), 2)
            self.assertEqual(FormationBrief(0).planned(facts, 'run'), [])

    def test_multiclass_uses_class_grid_even_behind_other_classes(self):
        for overall, position, hello, station in [
                (7, 1, 'form_class_pole', 'form_class_pole_station'),
                (8, 2, 'form_class_front_row', 'form_class_front_station'),
                (1, 1, 'form_class_pole', 'form_class_pole_station'),
                (9, 3, 'form_hello', 'form_hold_station'),
                (1, None, 'form_hello', 'form_hold_station')]:
            with self.subTest(overall=overall, position=position):
                facts = dict(grid_position=overall, class_position=position, multiclass=True,
                             class_cars=20, class_sof=2000)
                plan = FormationBrief(11).planned(facts, 'run')
                self.assertIn(hello, dict(usable(plan[0], facts)))
                start = next(t for t in plan if t[0] == 'start')
                self.assertIn(station, dict(usable(start, facts)))
                self.assertEqual(plan[-1][0], 'closing')
                if position in (1, 2):
                    self.assertIn('form_front_rolling', dict(usable(start, facts)))
                    self.assertNotIn('form_hold_station', dict(usable(start, facts)))
                    self.assertEqual([t[0] for t in FormationBrief(4).planned(facts, 'run')],
                                     ['race', 'field', 'start', 'closing'])
                for count in (0, 1, 2):
                    self.assertEqual(len(FormationBrief(count).planned(facts, 'run')), count)

    def test_class_front_row_plan_stays_fixed_until_the_next_run(self):
        brief = FormationBrief(4)
        facts = dict(grid_position=7, class_position=1, multiclass=True, class_cars=20, class_sof=2000)
        base = dict(session_state=3, speed_mps=20.)
        brief.step({**sample(), 'distance_pct': .1, **base}, facts, 'run', 0.)
        plan = brief.plan
        found = brief.step({**sample(), 'distance_pct': .13, **base}, {**facts, 'class_position': 3}, 'run', 1.)
        self.assertIs(brief.plan, plan)
        self.assertIn('form_class_pole', dict(found[2]))
        brief.step({**sample(), 'distance_pct': .1, **base}, {**facts, 'class_position': 2}, 'next', 2.)
        self.assertIn('form_class_front_row', dict(usable(brief.plan[0], facts)))

    def test_pace_car_does_not_make_the_formation_multiclass(self):
        for other_class in (False, True):
            with self.subTest(other_class=other_class):
                live = LiveTelemetry()
                live.radio.metadata['session_type'] = 'Race'
                cars = [car(0, .1, class_id=10), car(1, .11, class_id=10),
                        car(9, .12, class_id=99, is_pace_car=True)]
                if other_class:
                    cars.append(car(2, .13, class_id=20))
                live.pit_wall.race = dict(player_idx=0, cars=cars)
                with patch.object(live.formation, 'step', return_value=None) as step:
                    live.formation_context.data['official_grid'] = {'overall': 7, 'class_position': 1, 'source': 'SDK_PRESTART'}
                    live._brief_formation({**sample(), 'session_state': 3, 'position': 7, 'class_position': 1}, 0.)
                facts = step.call_args.args[1]
                self.assertEqual(facts['multiclass'], other_class)
                self.assertEqual(facts['class_cars'], 2)
                plan = FormationBrief(2).planned(facts, 'run')
                self.assertIn('form_class_pole' if other_class else 'form_hello', dict(usable(plan[0], facts)))

    def test_all_new_formation_wordings_exist_in_catalog(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        for multiclass in (False, True):
            for position in (1, 2):
                for _, role, sentences in grid_topics(position, multiclass=multiclass):
                    for key, _ in sentences:
                        if key not in ('form_pole', 'form_front_row', 'form_pole_station', 'form_front_station',
                                       'form_front_rolling', 'form_class_pole', 'form_class_front_row',
                                       'form_class_pole_station', 'form_class_front_station'):
                            continue
                        self.assertEqual(radio_phrases.PHRASES[key][0], role)
                        self.assertEqual(len(radio_phrases.PHRASES[key][1]), 3)
                        for text in radio_phrases.PHRASES[key][1]:
                            self.assertIn(text, prepared)
                            self.assertNotIn(',', text)
                            self.assertNotIn('전체', text)
