"""Compact notices use real workload and event lifetimes without covering graphs."""
import unittest
import test_overlay_cards as lap_fixtures

from overlay_cards import SessionCards
from overlay_control import OverlayController
from test_overlay_cards import view, completed, fuel_view, approach_view


class FooterNoticeTests(unittest.TestCase):
    def test_short_straight_without_reference_can_show_measured_race_lap(self):
        self.assertTrue(OverlayController().cards.footer)
        data = view(kind='RACE', session_events=completed(), card_window=None)
        data['points'] = data['points'][-6:]
        notice = SessionCards(footer=True).select(data)
        self.assertEqual(notice['presentation'], 'footer')
        self.assertEqual(notice['text'], '랩 1:39.000 · 베스트 대비 -1.000초')
        self.assertEqual(notice['expires_at']-notice['shown_at'], 4.)
        self.assertIsNone(SessionCards().select(data))
        data['points'] = data['points'][-5:]
        self.assertIsNone(SessionCards(footer=True).select(data))

    def test_workload_ends_notice_immediately_and_unknown_overlap_stays_silent(self):
        for change in ({'brake': .2}, {'throttle': .4}, {'steering_rad': .2},
                       {'car_left_right': 2}, {'car_left_right': None},
                       {'session_flags': 8}, {'on_pit_road': True},
                       {'on_track': False}, {'is_replay': True}):
            notices = SessionCards(footer=True)
            self.assertIsNotNone(notices.select(fuel_view()))
            data = fuel_view(10.1)
            data['snapshot'].update(change)
            self.assertIsNone(notices.select(data), change)

    def test_notice_expires_once_and_new_session_cannot_reuse_it(self):
        notices = SessionCards(footer=True)
        data = view(kind='RACE', session_events=completed())
        self.assertIsNotNone(notices.select(data))
        for tick in range(1, 61):
            data = view(10+tick/10, 'RACE', session_events=completed())
            shown = notices.select(data)
            self.assertEqual(shown is not None, tick < 40)
        data['run_id'] = 'new-race'
        data['session_events'] = {}
        self.assertIsNone(notices.select(data))

    def test_fuel_preempts_lap_and_refreshes_actual_measurement(self):
        notices = SessionCards(footer=True)
        self.assertEqual(notices.select(view(kind='RACE', session_events=completed()))['kind'], 'lap')
        data = fuel_view(10.1)
        notice = notices.select(data)
        self.assertEqual(notice['text'], '이번 랩 피트 · 잔여 연료 0.9랩')
        data = fuel_view(10.2, fuel_laps=.8)
        self.assertEqual(notices.select(data)['text'], '이번 랩 피트 · 잔여 연료 0.8랩')
        data['session_race'].update(remaining_laps=.5, fuel_finish_shortfall_l=0.)
        self.assertIsNone(notices.select(data))

    def test_missing_best_does_not_invent_delta_and_old_result_is_not_queued(self):
        data = view(kind='RACE', session_events=completed())
        data['session_events']['lap']['previous_best_s'] = None
        self.assertEqual(SessionCards(footer=True).select(data)['text'], '랩 1:39.000')
        data['snapshot']['session_time'] = 19.
        self.assertIsNone(SessionCards(footer=True).select(data))

    def test_traffic_notice_uses_observed_gap_and_disappears_on_neighbor_change(self):
        notices = SessionCards(footer=True)
        data = approach_view()
        self.assertEqual(notices.select(data)['text'], '앞차 접근 · 1.8 → 0.7초')
        data = approach_view(10.1, idx=5, gap_trend=None)
        self.assertIsNone(notices.select(data))

    def test_history_gap_or_busy_input_cannot_be_called_a_straight(self):
        for change in ({'brake': .2}, {'on_pit_road': True}, {'distance_pct': .9},
                       {'session_time': 8.}):
            data = view(kind='RACE', session_events=completed())
            data['points'][-3].update(change)
            self.assertIsNone(SessionCards(footer=True).select(data), change)

    def test_similar_previous_lap_waits_for_sdk_clock_update(self):
        def delayed(tick, row):
            row.update(lap_last_s=19.95 if tick < 408 else 20., lap_best_s=19.95)
            if 400 <= tick < 408:
                row['lap_current_s'] = 20.+(tick-400)/10
        tracker, row = lap_fixtures.LapEventTests().drive(delayed, until=40.7)
        self.assertIsNone(tracker.view(row)['lap'])
        tracker, row = lap_fixtures.LapEventTests().drive(delayed, until=40.8)
        self.assertEqual(tracker.view(row)['lap']['time_s'], 20.)


if __name__ == '__main__':
    unittest.main()
