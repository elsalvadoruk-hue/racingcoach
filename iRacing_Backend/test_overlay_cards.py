"""Session cards from sampled laps/reference passes/engineer facts, with no services."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

from live_telemetry import LiveTelemetry, CHANNELS
from overlay_cards import OverlayLapEvents, SessionCards
from overlay_control import OverlayController
from race_engineer import RaceEngineer


def sample(at=10., **changes):
    return {'session_time': at, 'lap_number': 3, 'distance_pct': .4, 'session_state': 4,
            'on_track': True, 'is_replay': False, 'on_pit_road': False,
            'brake': 0., 'throttle': 1., 'steering_rad': 0., 'speed_mps': 40., 'gear': 3,
            'session_flags': 0, 'car_left_right': 0, 'incidents': 0, 'track_surface': 3,
            'session_time_remaining_s': 260., **changes}


def view(at=10., kind='PRACTICE', **changes):
    return {'connected': True, 'run_id': 'run', 'snapshot': sample(at),
            'session': {'kind': kind}, 'practice': None, 'track_length_m': 5000.,
            'card_window': {'at': at, 'lap': 3, 'clear_s': 12.},
            'points': [sample(at-(60-i)/10, distance_pct=.4-(60-i)*.0008) for i in range(61)], **changes}


def completed(at=10., best=100., time=99.):
    return {'lap': {'id': f'lap:{at}', 'at': at, 'lap': 2, 'time_s': time,
                    'previous_best_s': best, 'new_best': time < best}}


def fuel_view(at=10., **changes):
    return view(at, 'RACE', session_race={'active': True, 'fuel_laps': .9, 'remaining_laps': 5.6,
                'fuel_finish_shortfall_l': 9., 'fuel_next_stop_l': 18., **changes})


def approach_view(at=10., **changes):
    return view(at, 'RACE', session_race={'active': True, 'ahead': {
        'idx': 4, 'gap_trend': 'closing', 'gap_start_s': 1.8, 'gap_at_line_s': .7,
        'gap_estimate_s': .7, 'gap_change_s_per_lap': -.37, 'gap_observations': 4,
        'trend_lap': 3, **changes}})


class LapEventTests(unittest.TestCase):
    def drive(self, changes=None, kind='Practice', until=40.1):
        tracker = OverlayLapEvents()
        for tick in range(round(until*10)+1):
            at = tick/10
            row = sample(at, distance_pct=(tick%200)/200, lap_number=1+tick//200,
                         lap_current_s=(tick%200)/10, lap_last_s=20. if tick >= 400 else 21.,
                         lap_best_s=20. if tick >= 400 else 21.)
            if changes:
                changes(tick, row)
            tracker.ingest(row, 'run', kind)
        return tracker, row

    def test_complete_clean_lap_requires_observed_start_and_published_matching_time(self):
        tracker, row = self.drive()
        event = tracker.view(row)['lap']
        self.assertEqual((event['time_s'], event['previous_best_s'], event['new_best']), (20., 21., True))
        first, row = self.drive(until=20.1)
        self.assertIsNone(first.view(row)['lap'])

    def test_stale_or_negative_lap_time_missing_channels_and_dirty_laps_are_excluded(self):
        modifications = [
            lambda tick, row: row.update(lap_last_s=21.),
            lambda tick, row: row.update(lap_last_s=-1.),
            lambda tick, row: row.update(track_surface=None),
            lambda tick, row: row.update(incidents=None),
            lambda tick, row: row.update(incidents=int(tick >= 250)),
            lambda tick, row: row.update(on_pit_road=tick == 250),
            lambda tick, row: row.update(is_replay=tick == 250),
            lambda tick, row: row.update(distance_pct=.1) if tick == 250 else None,
        ]
        for change in modifications:
            tracker, row = self.drive(change)
            self.assertIsNone(tracker.view(row)['lap'])

    def test_delayed_best_time_is_waited_for_and_equal_lap_times_can_be_measured(self):
        tracker, row = self.drive(lambda tick, row: row.update(lap_best_s=21.) if tick < 401 else None)
        self.assertEqual(tracker.view(row)['lap']['time_s'], 20.)
        tracker, row = self.drive(lambda tick, row: row.update(lap_last_s=20., lap_best_s=20.))
        self.assertFalse(tracker.view(row)['lap']['new_best'])

    def test_best_updated_just_before_distance_wrap_still_compares_with_previous_best(self):
        tracker, row = self.drive(lambda tick, row: row.update(lap_best_s=20.) if tick >= 399 else None)
        event = tracker.view(row)['lap']
        self.assertTrue(event['new_best'])
        self.assertEqual(event['previous_best_s'], 21.)

    def test_lap_counter_can_change_one_tick_before_or_after_distance_wrap(self):
        for offset in (-1, 1):
            tracker, row = self.drive(lambda tick, row: row.update(lap_number=1+max(0, tick+offset)//200))
            self.assertEqual(tracker.view(row)['lap']['time_s'], 20.)

    def test_quali_invalid_reason_uses_incident_observation_and_is_not_assumed_on_surface_alone(self):
        tracker = OverlayLapEvents()
        tracker.ingest(sample(10., distance_pct=.2), 'run', 'Lone Qualify')
        tracker.ingest(sample(10.1, distance_pct=.21, incidents=1, track_surface=0), 'run', 'Lone Qualify')
        self.assertEqual(tracker.view(sample(10.1))['invalid']['reason'], '트랙 이탈 · +1점')
        tracker.ingest(sample(10.2, distance_pct=.22, incidents=2, track_surface=3), 'run', 'Lone Qualify')
        self.assertEqual(tracker.view(sample(10.2))['invalid']['at'], 10.1)
        other = OverlayLapEvents()
        other.ingest(sample(10.), 'run', 'Qualify')
        other.ingest(sample(10.1, track_surface=0), 'run', 'Qualify')
        self.assertIsNone(other.view(sample(10.1))['invalid'])

    def test_session_change_gap_replay_and_expiry_clear_events(self):
        tracker, row = self.drive()
        self.assertIsNone(tracker.view(sample(53.))['lap'])
        tracker.ingest(dict(row, session_time=41.), 'run', 'Practice')
        self.assertIsNone(tracker.view(sample(41.))['lap'])
        tracker, row = self.drive()
        tracker.ingest(dict(row, session_time=40.2), 'new-run', 'Qualify')
        self.assertIsNone(tracker.view(sample(40.2))['lap'])


class CardPolicyTests(unittest.TestCase):
    def test_lap_and_quali_cards_use_actual_delta_and_do_not_repeat_after_expiry(self):
        for kind, expected in (('PRACTICE', '-1.000초'), ('QUALIFY', '1:39.000')):
            cards = SessionCards()
            data = view(kind=kind, session_events=completed())
            card = cards.select(data)
            self.assertEqual(card['value'], expected)
            for tick in range(1, 101):
                data['snapshot']['session_time'] = 10+tick/10
                data['card_window']['at'] = 10+tick/10
                data['points'] = view(10+tick/10)['points']
                shown = cards.select(data)
                if tick < 25:
                    self.assertEqual(shown['id'], card['id'])
                else:
                    self.assertIsNone(shown)

    def test_invalid_card_only_in_quali_and_no_unmeasured_lap_best(self):
        data = view(kind='QUALIFY', session_events={'invalid': {'id': 'invalid', 'at': 10., 'reason': '인시던트 +2점'}})
        self.assertEqual(SessionCards().select(data)['left'], '인시던트 +2점')
        data['session']['kind'] = 'PRACTICE'
        self.assertIsNone(SessionCards().select(data))
        slower = SessionCards().select(view(kind='QUALIFY', session_events=completed(best=99., time=100.)))
        self.assertEqual((slower['value'], slower['left']), ('1:40.000', '이전보다 +1.000초'))

    def test_missing_data_pit_replay_task_or_inactive_session_never_cover_graph(self):
        changes = ({'brake': None}, {'throttle': None}, {'steering_rad': None},
                   {'on_pit_road': True}, {'on_track': False}, {'is_replay': True},
                   {'session_state': 3}, {'session_flags': 8}, {'car_left_right': 2}, {'car_left_right': None})
        for change in changes:
            data = fuel_view()
            data['snapshot'].update(change)
            self.assertIsNone(SessionCards().select(data), change)
        self.assertIsNone(SessionCards().select(dict(fuel_view(), practice={'scope': 'today'})))
        self.assertIsNone(SessionCards().select(view()))

    def test_busy_card_waits_then_expires_and_displayed_card_yields_to_braking(self):
        cards = SessionCards()
        data = view(session_events=completed())
        data['snapshot']['brake'] = .8
        self.assertIsNone(cards.select(data))
        for tick in range(1, 52):
            data = view(10+tick/10, session_events=completed())
            shown = cards.select(data)
            if tick < 50:
                self.assertIsNone(shown)
        self.assertIsNotNone(shown)
        data['snapshot'].update(session_time=15.2, brake=.1)
        self.assertIsNone(cards.select(data))
        cards = SessionCards()
        for tick in range(91):
            data['snapshot'].update(session_time=10+tick/10, brake=.8 if tick < 90 else 0.)
            self.assertIsNone(cards.select(data))

    def test_fuel_matches_engineer_box_margin_and_never_requests_stop_when_finish_is_covered(self):
        card = SessionCards().select(fuel_view())
        self.assertEqual((card['value'], card['left'], card['right']), ('이번 랩 피트', '잔여 연료 0.9랩', '필요 급유 18.0L'))
        for change in ({'remaining_laps': .5, 'fuel_finish_shortfall_l': 0.}, {'fuel_laps': None},
                       {'fuel_laps': 5.}, {'active': False}):
            self.assertIsNone(SessionCards().select(fuel_view(**change)))
        self.assertEqual(SessionCards().select(fuel_view(fuel_next_stop_l=None))['right'], '급유량 —')

    def test_fuel_preempts_lower_priority_card_and_cancels_when_fuel_is_sufficient(self):
        cards = SessionCards()
        data = approach_view()
        self.assertEqual(cards.select(data)['kind'], 'closing')
        data = fuel_view(10.1, ahead=data['session_race']['ahead'])
        self.assertEqual(cards.select(data)['kind'], 'fuel')
        data['session_race'].update(remaining_laps=.5, fuel_finish_shortfall_l=0.)
        data['snapshot']['session_time'] = 10.2
        self.assertIsNone(cards.select(data))

    def test_current_critical_fuel_waits_through_long_corner_with_fresh_values(self):
        cards = SessionCards()
        for tick in range(161):
            data = fuel_view(10+tick/10, fuel_laps=.8 if tick >= 100 else .9)
            data['snapshot']['brake'] = .8 if tick < 110 else 0.
            card = cards.select(data)
            if tick < 159:
                self.assertIsNone(card)
        self.assertEqual(card['left'], '잔여 연료 0.8랩')

    def test_approach_uses_observed_endpoints_and_intervals_and_cancels_on_neighbor_change(self):
        cards = SessionCards()
        data = approach_view()
        card = cards.select(data)
        self.assertEqual((card['value'], card['left']), ('1.8 → 0.7초', '최근 3랩 연속 접근'))
        data['session_race']['ahead']['idx'] = 5
        data['snapshot']['session_time'] = 10.1
        self.assertIsNone(cards.select(data))
        for change in ({'gap_trend': 'opening'}, {'gap_start_s': None}, {'gap_observations': 2},
                       {'gap_at_line_s': 9.}, {'gap_change_s_per_lap': -.1}):
            self.assertIsNone(SessionCards().select(approach_view(**change)))

    def test_repeat_corner_needs_three_consecutive_passes_against_same_graph_reference(self):
        cards = SessionCards()
        def passed(lap, at, braking=8., reference='g61-a'):
            return view(at, reference_status={'state': 'READY', 'lap_id': reference, 'lap_time_s': 100.},
                        comparison={'reference': [1]}, feedback={'corner_id': 'c1', 'delta_s': .24, 'brake_delta_m': braking},
                        corner_pass={'id': 'c1', 'lap': lap, 'exit_at': at-5., 'clean': True})
        # Monitor observes each lap continuously, not just three unrelated packets.
        for lap in range(1, 4):
            at = 10.+lap
            card = cards.select(passed(lap, at))
            if lap < 3:
                self.assertIsNone(card)
                uncovered = passed(lap, at+.1)
                uncovered.update(comparison=None, feedback=None, corner_pass=None)
                self.assertIsNone(cards.select(uncovered))
        self.assertEqual(card['value'], '8m 늦음')
        self.assertEqual(card['right'], '코너 +0.24초')
        self.assertIsNone(cards.select(passed(4, 14., reference='g61-b')))
        self.assertIsNone(SessionCards().select(passed(5, 15.)))
        for laps, directions in (([1, 2, 4], [8., 8., 8.]), ([1, 2, 3], [8., -8., 8.])):
            cards = SessionCards()
            for index, lap in enumerate(laps):
                self.assertIsNone(cards.select(passed(lap, 10.+index, directions[index])))

    def test_scope_and_connection_changes_clear_active_cards(self):
        for change in ({'connected': False}, {'run_id': 'other'}, {'session': {'kind': 'RACE'}}):
            cards = SessionCards()
            data = view(session_events=completed())
            self.assertIsNotNone(cards.select(data))
            # New sessions must never supply previous-session events in production.
            data.update(change, session_events={})
            data['snapshot']['session_time'] = 10.1
            self.assertIsNone(cards.select(data))


class IntegrationTests(unittest.TestCase):
    def test_measured_fuel_consumption_flows_from_live_samples_through_existing_engineer(self):
        from test_race_engineer import race_fixture
        live = LiveTelemetry(clock=lambda: 100.)
        identity = {'car_id': '1', 'track_id': '2'}
        for tick in range(821):
            at = tick/10
            race = race_fixture() if tick % 10 == 0 else None
            if race:
                for car in race['cars']:
                    progress = at/20+(.1 if car['idx'] == 0 else -.1 if car['idx'] == 2 else 0)
                    car.update(lap_completed=max(0, int(progress)), distance_pct=progress % 1, last_lap_s=20.)
            live.ingest(identity, sample(at, session_num=0, session_type='Race', lap_number=1+tick//200,
                        lap_completed=tick//200, distance_pct=(tick % 200)/200,
                        fuel_l=10.-at*.1, lap_last_s=20.), race)
        state = live.overlay_state()
        self.assertAlmostEqual(state['session_race']['fuel_laps'], .9)
        self.assertAlmostEqual(state['session_race']['fuel_next_stop_l'], 11.)
        # Reference clearance is supplied separately; this test checks SDK fuel facts.
        state['card_window'] = {'at': state['snapshot']['session_time'], 'lap': state['snapshot']['lap_number'], 'clear_s': 12.}
        card = SessionCards().select(state)
        self.assertEqual((card['kind'], card['right']), ('fuel', '필요 급유 11.0L'))

    def test_observed_quali_incident_reaches_card_without_sample_events(self):
        live = LiveTelemetry(clock=lambda: 100.)
        for tick in range(61):
            at, incidents, surface = 10+tick/10, int(tick >= 1), 0 if tick == 1 else 3
            live.ingest({'car_id': '1', 'track_id': '2'}, sample(at, session_type='Lone Qualify',
                        incidents=incidents, track_surface=surface))
        state = live.overlay_state()
        self.assertEqual(state['session']['kind'], 'QUALIFY')
        state['card_window'] = {'at': state['snapshot']['session_time'], 'lap': state['snapshot']['lap_number'], 'clear_s': 12.}
        card = SessionCards().select(state)
        self.assertEqual((card['value'], card['left']), ('기록 무효', '트랙 이탈 · +1점'))

    def test_sampled_live_lap_reaches_controller_packet_and_has_no_sample_only_fields(self):
        live = LiveTelemetry(clock=lambda: 100.)
        identity = {'car_id': '1', 'track_id': '2'}
        for tick in range(402):
            at = tick/10
            row = sample(at, session_num=0, session_type='Practice',
                         distance_pct=(tick%200)/200, lap_number=1+tick//200,
                         lap_completed=tick//200, lap_current_s=(tick%200)/10,
                         lap_last_s=20. if tick >= 400 else 21., lap_best_s=20. if tick >= 400 else 21.)
            live.ingest(identity, row)
        data = live.overlay_state()
        self.assertEqual(data['session_events']['lap']['time_s'], 20.)
        data['card_window'] = {'at': data['snapshot']['session_time'], 'lap': data['snapshot']['lap_number'], 'clear_s': 12.}
        controller = OverlayController()
        controller.auto_show = True
        controller._start_child = Mock()
        controller.command = Mock()
        controller.status = Mock(return_value={})
        controller.sync(data)
        packet = controller.command.call_args.args[1]
        self.assertEqual(packet['card']['value'], '-1.000초')
        self.assertEqual(packet['snapshot']['gear'], 3)
        self.assertEqual(CHANNELS['track_surface'], 'PlayerTrackSurface')

    def test_real_engineer_start_line_history_supplies_observed_approach_endpoints(self):
        from test_race_engineer import race_fixture
        from collections import deque
        wall = RaceEngineer()
        data = race_fixture()
        wall.ingest(data, 'run', 0.)
        wall.paces = {0: deque([100., 100., 100.]), 1: deque([100., 100., 100.])}
        # Four measured start-line gaps, with intermediate 1Hz observations.
        for lap, gap in enumerate((1.8, 1.43, 1.07, .7), 4):
            data = deepcopy(data)
            for car in data['cars']:
                car.update(lap_completed=lap-1, distance_pct=.99)
            wall.ingest(data, 'run', lap*100.-1)
            data = deepcopy(data)
            data['cars'][1].update(lap_completed=lap, distance_pct=.001)
            data['cars'][0].update(lap_completed=lap, distance_pct=.001+gap/100)
            wall.ingest(data, 'run', lap*100.)
        measured = wall.view(sample(700., distance_pct=.001), 2., 3)
        ahead = measured['ahead']
        self.assertEqual((ahead['gap_start_s'], ahead['gap_at_line_s']), (1.8, .7))
        self.assertEqual(SessionCards().select(view(700., 'RACE', session_race=measured))['value'], '1.8 → 0.7초')


if __name__ == '__main__':
    unittest.main()
