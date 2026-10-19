"""Pedal graph protection around corners and sparse, measured session summaries."""
from copy import deepcopy
import unittest
from unittest.mock import Mock

import numpy as np

from overlay_cards import SessionCards
from overlay_control import OverlayController
from overlay_reference import AlignedReference, GridReference, OverlayReference
from overlay_timing import reference_clearance, reviewed_exit
from test_overlay_cards import view, completed, fuel_view, approach_view


def reference(channel='Brake', start=.5, end=.56, steering=True):
    x = np.linspace(0., 1., 5001)
    grid = {'distance_pct': x, 'time_s': x*100., 'Brake': np.zeros(len(x)),
            'Throttle': np.ones(len(x)), 'Gear': np.full(len(x), 3.)}
    if steering:
        grid['SteeringWheelAngle'] = np.zeros(len(x))
    if channel:
        grid[channel][(x >= start) & (x <= end)] = {'Brake': .5, 'Throttle': .3, 'SteeringWheelAngle': .3}[channel]
    return AlignedReference(GridReference(grid, 100.), 5000.)


def aligned(data, curve):
    return {**data, 'card_window': reference_clearance(curve, data)}


def passed(at, lap, delta=.4, braking=8., identifier='c1'):
    data = view(at, reference_status={'state': 'READY', 'lap_id': 'g61-a', 'lap_time_s': 100.},
                feedback={'corner_id': identifier, 'delta_s': delta, 'brake_delta_m': braking},
                corner_pass={'id': identifier, 'lap': lap, 'exit_at': at-5., 'clean': True})
    return data


def advance(cards, until, kind='PRACTICE'):
    start = cards.last_at if cards.last_at is not None else until
    for at in np.arange(start+.1, until, .1):
        data = view(float(at), kind)
        if cards.reference:
            data['reference_status'] = {'state': 'READY', 'lap_id': cards.reference[0], 'lap_time_s': cards.reference[1]}
        cards.select(data)


class GraphProtectionTests(unittest.TestCase):
    def test_reference_pedals_lift_and_turn_all_reserve_braking_preview(self):
        for channel in ('Brake', 'Throttle', 'SteeringWheelAngle'):
            for beginning, can_show in ((.45, False), (.5, True)):
                data = aligned(view(session_events=completed()), reference(channel, start=beginning))
                shown = SessionCards().select(data)
                self.assertEqual(shown is not None, can_show, (channel, beginning))

    def test_faster_current_speed_shortens_available_straight(self):
        data = view(session_events=completed())
        curve = reference()
        self.assertIsNotNone(SessionCards().select(aligned(data, curve)))
        data['snapshot']['speed_mps'] = 80.
        self.assertIsNone(SessionCards().select(aligned(data, curve)))

    def test_known_corner_zone_protects_full_throttle_corner_without_steering_reference(self):
        curve = reference(channel=None, steering=False)
        data = view(session_events=completed())
        self.assertIsNone(SessionCards().select(aligned(data, curve)))
        data['corner_zones'] = [{'entry_pct': .45, 'exit_pct': .49}]
        self.assertIsNone(SessionCards().select(aligned(data, curve)))
        data['corner_zones'][0].update(entry_pct=.6, exit_pct=.65)
        self.assertIsNotNone(SessionCards().select(aligned(data, curve)))

    def test_nan_steering_can_use_known_zones_but_unknown_pedals_cannot(self):
        curve = reference(channel=None)
        curve.grid['SteeringWheelAngle'][:] = np.nan
        data = view(session_events=completed(), corner_zones=[{'entry_pct': .6, 'exit_pct': .65}])
        self.assertIsNotNone(SessionCards().select(aligned(data, curve)))
        curve.grid['Brake'][(curve.distance > .42) & (curve.distance < .43)] = np.nan
        self.assertIsNone(SessionCards().select(aligned(data, curve)))

    def test_missing_future_time_coverage_and_absent_or_stale_clearance_keep_graph(self):
        data = view(session_events=completed())
        curve = reference()
        curve.times[(curve.distance > .42) & (curve.distance < .43)] = np.nan
        self.assertIsNone(SessionCards().select(aligned(data, curve)))
        for window in (None, {}, {'at': 9., 'lap': 3, 'clear_s': 12.}, {'at': 10., 'lap': 2, 'clear_s': 12.}):
            self.assertIsNone(SessionCards().select({**data, 'card_window': window}))

    def test_exit_requires_five_seconds_of_real_input_review_and_no_gaps_or_resets(self):
        base = view(session_events=completed(), corner_pass={'exit_at': 8.})
        self.assertIsNone(SessionCards().select(base))
        for name, value in (('brake', .1), ('throttle', .5), ('steering_rad', .2), ('distance_pct', .9)):
            data = view(session_events=completed())
            data['points'][-20][name] = value
            self.assertIsNone(SessionCards().select(data), name)
        data = view(session_events=completed())
        data['points'] = data['points'][-30:]
        self.assertFalse(reviewed_exit(data))
        data = view(session_events=completed(), corner_pass={'exit_at': 5.})
        self.assertIsNotNone(SessionCards().select(data))

    def test_visible_card_closes_five_seconds_before_next_input_even_before_driver_brakes(self):
        cards = SessionCards()
        self.assertIsNotNone(cards.select(view(session_events=completed())))
        data = view(10.1, session_events=completed())
        data['card_window']['clear_s'] = 4.99
        self.assertIsNone(cards.select(data))
        self.assertEqual(data['snapshot']['brake'], 0.)

    def test_fuel_and_escalation_cannot_bypass_graph_protection(self):
        for clear in (None, 4., 7.49):
            data = fuel_view()
            data['card_window'] = None if clear is None else {**data['card_window'], 'clear_s': clear}
            self.assertIsNone(SessionCards().select(data))
        data = fuel_view()
        data['corner_pass'] = {'exit_at': 9.}
        self.assertIsNone(SessionCards().select(data))

    def test_finish_line_lookahead_includes_next_lap_and_missing_seam_is_not_filled(self):
        curve = reference(start=.02, end=.03)
        data = view()
        data['snapshot']['distance_pct'] = .99
        self.assertLess(reference_clearance(curve, data)['clear_s'], 3.)
        curve.times[curve.distance < .01] = np.nan
        self.assertLess(reference_clearance(curve, data)['clear_s'], 1.1)

    def test_ordinary_cards_have_one_per_lap_and_forty_five_seconds_of_graph(self):
        cards = SessionCards()
        self.assertIsNotNone(cards.select(view(session_events=completed())))
        advance(cards, 60.)
        self.assertIsNone(cards.select(view(60., session_events=completed(60.))))
        data = view(60.1, session_events=completed(60.1))
        data['snapshot']['lap_number'] = data['card_window']['lap'] = 4
        self.assertIsNotNone(cards.select(data))
        other = SessionCards()
        self.assertIsNotNone(other.select(view(session_events=completed())))
        advance(other, 20.)
        data = view(20., session_events=completed(20.))
        data['snapshot']['lap_number'] = data['card_window']['lap'] = 4
        self.assertIsNone(other.select(data))

    def test_controller_keeps_graph_reference_while_footer_needs_no_future_window(self):
        service = OverlayReference('.', Mock())
        service.reference, service.state = reference(), {'state': 'READY', 'lap_id': 'test'}
        service.observe = Mock()  # This test installs the curve directly, without a cache worker.
        controller = OverlayController(reference=service)
        controller.auto_show = True
        controller._start_child, controller.command = Mock(), Mock()
        controller.status = Mock(return_value={})
        controller.sync(view(session_events=completed()))
        packet = controller.command.call_args.args[1]
        self.assertTrue(packet['comparison'])
        self.assertIsNotNone(packet['card'])
        self.assertLessEqual(max(row['offset'] for row in packet['comparison']['reference']), 2.)
        service.reference = reference(start=.44)
        controller.sync(view(10.1, session_events=completed()))
        self.assertEqual(controller.command.call_args.args[1]['card']['presentation'], 'footer')


class AdditionalCardTests(unittest.TestCase):
    def test_improvement_uses_three_prior_clean_same_reference_passes_and_no_causal_claim(self):
        cards = SessionCards()
        for lap, delta in enumerate((.4, .5, .3, .15), 1):
            at = 10+lap*10.
            advance(cards, at)
            shown = cards.select(passed(at, lap, delta, braking=None))
        self.assertEqual((shown['kind'], shown['value']), ('improvement', '0.25초 단축'))
        self.assertEqual(shown['caption'], '이전 3회 중앙값 대비')

    def test_reference_change_dirty_pass_or_missing_lap_never_fabricates_improvement(self):
        for problem in ('reference', 'dirty', 'gap'):
            cards = SessionCards()
            for lap, delta in enumerate((.4, .5, .3, .1), 1):
                at = 10+lap*10.
                advance(cards, at)
                data = passed(at, lap+int(problem == 'gap' and lap == 4), delta, braking=None)
                if problem == 'reference' and lap == 4:
                    data['reference_status']['lap_id'] = 'different'
                if problem == 'dirty' and lap == 3:
                    data['corner_pass']['clean'] = False
                shown = cards.select(data)
            self.assertIsNone(shown, problem)

    def test_largest_loss_is_merged_into_lap_result_and_uses_confirmed_name_only(self):
        for kind in ('PRACTICE', 'QUALIFY'):
            cards = SessionCards()
            data = passed(10., 2, delta=.32, identifier='c5')
            data['session']['kind'] = kind
            data['corner_pass']['call'] = '5번 코너'
            self.assertIsNone(cards.select(data))
            data = passed(10.1, 2, delta=.12, identifier='c8')
            data['session']['kind'] = kind
            self.assertIsNone(cards.select(data))
            data = view(10.2, kind, session_events=completed(10.2))
            data['reference_status'] = {'state': 'READY', 'lap_id': 'g61-a', 'lap_time_s': 100.}
            shown = cards.select(data)
            self.assertEqual(shown['kind'], 'lap')
            self.assertEqual(shown['right'], '5번 코너 +0.32초')
            self.assertEqual(shown['basis'], 'G61 2구간 중 최대 손실')
            self.assertEqual(len(cards.pending), 0)

    def test_first_valid_qualifying_lap_has_no_invented_best_delta(self):
        data = view(kind='QUALIFY', session_events={'lap': {'id': 'first', 'at': 10., 'lap': 2,
                    'time_s': 99., 'previous_best_s': None, 'new_best': False}})
        card = SessionCards().select(data)
        self.assertEqual((card['value'], card['left']), ('1:39.000', '베스트 비교 없음'))

    def test_pit_setting_warning_reads_enable_bit_and_cancels_when_corrected(self):
        for flags, expected in ((0, '설정 0L'), (0x10, '설정 10L')):
            data = fuel_view(fuel_laps=2.)
            data['snapshot'].update(pit_sv_fuel=10., pit_sv_flags=flags)
            cards = SessionCards()
            card = cards.select(data)
            self.assertEqual((card['kind'], card['left'], card['right']), ('fuel_setting', expected, '필요 18.0L'))
            data = deepcopy(data)
            data['snapshot'].update(session_time=10.1, pit_sv_fuel=18., pit_sv_flags=0x10)
            data['card_window']['at'] = 10.1
            self.assertIsNone(cards.select(data))

    def test_critical_fuel_merges_setting_shortage_instead_of_second_popup(self):
        data = fuel_view()
        data['snapshot'].update(pit_sv_fuel=10., pit_sv_flags=0x10)
        cards = SessionCards()
        card = cards.select(data)
        self.assertEqual((card['kind'], card['right']), ('fuel', '설정 10 / 필요 18.0L'))
        self.assertFalse(any(row['kind'] == 'fuel_setting' for row in cards.pending.values()))

    def test_sampled_pit_configuration_reaches_overlay_snapshot(self):
        from live_telemetry import LiveTelemetry, CHANNELS
        from test_overlay_cards import sample
        live = LiveTelemetry(clock=lambda: 100.)
        live.ingest({'car_id': '1', 'track_id': '2'},
                    sample(session_type='Race', pit_sv_fuel=10., pit_sv_flags=0x10))
        snapshot = live.overlay_state()['snapshot']
        self.assertEqual((snapshot['pit_sv_fuel'], snapshot['pit_sv_flags']), (10., 0x10))
        self.assertEqual((CHANNELS['pit_sv_fuel'], CHANNELS['pit_sv_flags']), ('PitSvFuel', 'PitSvFlags'))

    def test_fuel_escalates_setting_warning_but_updates_do_not_restart_duration(self):
        cards = SessionCards()
        data = fuel_view(fuel_laps=2.)
        data['snapshot'].update(pit_sv_fuel=10., pit_sv_flags=0x10)
        self.assertEqual(cards.select(data)['kind'], 'fuel_setting')
        data['snapshot']['session_time'] = data['card_window']['at'] = 10.1
        data['snapshot']['pit_sv_fuel'] = 11.
        updated = cards.select(data)
        self.assertEqual((updated['left'], updated['expires_at']), ('설정 11L', 12.5))
        data['snapshot']['session_time'] = data['card_window']['at'] = 10.2
        data['session_race']['fuel_laps'] = .9
        self.assertEqual(cards.select(data)['kind'], 'fuel')

    def test_unknown_settings_sufficient_fuel_and_distant_stop_do_not_warn(self):
        for changes in ({'pit_sv_fuel': None}, {'pit_sv_flags': None}, {'pit_sv_fuel': -1}, {'pit_sv_flags': 1.2}):
            data = fuel_view(fuel_laps=2.)
            data['snapshot'].update({'pit_sv_fuel': 10., 'pit_sv_flags': 0x10, **changes})
            self.assertIsNone(SessionCards().select(data))
        for changes in ({'fuel_laps': 3.}, {'fuel_finish_shortfall_l': 0.}):
            data = fuel_view(**changes)
            data['snapshot'].update(pit_sv_fuel=10., pit_sv_flags=0x10)
            self.assertIsNone(SessionCards().select(data))


if __name__ == '__main__':
    unittest.main()
