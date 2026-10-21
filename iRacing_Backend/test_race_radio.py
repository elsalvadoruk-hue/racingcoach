"""Basic call accuracy, expiry, variants, role handoffs and no fabricated data."""
import asyncio
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_telemetry import LiveTelemetry
from race_radio import RaceRadio, STATIC_CALLS, duration
from race_engineer import parse_measure, choose_role
from voice_coach import VoiceCoach, ReportRequest, register_voice_routes
from voice_notes import VoiceNoteStore


class RaceRadioTests(unittest.TestCase):
    def setUp(self):
        self.radio = RaceRadio()
        self.now = 1000.
        self.sample = {'session_time': 0., 'session_num': 0, 'session_state': 4, 'session_flags': 0,
                       'on_track': True, 'is_replay': False, 'on_pit_road': False, 'car_left_right': 1,
                       'speed_mps': 40., 'brake': 0, 'steering_rad': 0, 'lap_completed': 2,
                       'lap_last_s': 101., 'lap_best_s': 100., 'class_position': 5, 'incidents': 0,
                       'engine_warnings': 0, 'pit_service_status': 0, 'fuel_l': 20.}
        self.meta = {'session_type': 'Race', 'total_laps': 20., 'track_name': 'Test Circuit', 'pit_speed_limit_kph': 80.}
        self.tick()

    def tick(self, advance=.1, run='run', **patch):
        self.now += advance
        self.sample.update(patch, session_time=self.now - 1000)
        self.radio.ingest(self.sample, run, self.now, self.meta)
        return self.radio.view(self.now)

    def wait(self, seconds):
        for _ in range(round(seconds * 10)):
            self.tick()

    def events(self):
        return self.radio.view(self.now)['events']

    def keys(self):
        return [e['key'] for e in self.events()]

    def test_basic_flags_and_personal_penalty_without_inventing_reason(self):
        self.tick(session_flags=0x10000 | 0x20 | 0x8)
        self.assertEqual(set(self.keys()), {'black', 'yellow'})       # blue only once it has stayed up
        self.wait(1.6)
        self.assertEqual(set(self.keys()), {'black', 'blue', 'yellow'})
        black = next(e for e in self.events() if e['key'] == 'black')
        self.assertNotIn('과속', black['text'])
        self.assertEqual(black['engineer'], 'spotter')
        self.tick(session_flags=0)
        self.assertFalse(self.events())

    def test_green_is_said_once_when_the_race_starts(self):
        # The driver's decision: green starts the race and nothing else.
        self.tick(session_state=3, session_flags=0)
        self.radio.pending.clear()
        self.tick(session_state=4, session_flags=0x4)
        self.assertIn('green', self.keys())
        self.radio.pending.clear()
        self.tick(session_flags=0xC000)                 # caution, then the restart
        self.wait(11)
        self.tick(session_flags=0x4)
        self.assertNotIn('green', self.keys())
        self.radio.reset()                              # link drops mid-race (LMU keeps the bit up)
        self.tick(session_flags=0x4)
        self.wait(11)
        self.assertNotIn('green', self.keys())

    def test_no_green_outside_a_race_start(self):
        self.meta['session_type'] = 'Practice'
        self.radio.reset()
        self.tick(session_state=3, session_flags=0)
        self.tick(session_state=4, session_flags=0x4)
        self.assertNotIn('green', self.keys())
        self.meta['session_type'] = 'Race'
        self.radio.reset()
        self.tick(session_state=4, session_flags=0x4)  # joined after the start
        self.assertNotIn('green', self.keys())

    def test_the_leaders_chequered_flag_is_not_ours(self):
        # The driver's decision: the checker is said when we cross the line, as the result.
        self.tick(session_flags=0x1)
        self.assertNotIn('finish', self.keys())
        self.tick(session_flags=0x1, lap_completed=3, class_position=4)
        self.wait(4)
        result = next(e for e in self.events() if e['key'] == 'race_result')
        self.assertEqual(result['priority'], 2)                # said through braking, not after it

    def test_caution_is_not_duplicated_as_local_yellow(self):
        self.tick(session_flags=0xC108)
        self.assertEqual(self.keys(), ['caution'])
        self.tick(session_flags=0xC108)
        self.assertEqual(self.keys(), ['caution'])

    def test_variant_rotates_after_clear_and_cooldown(self):
        self.tick(session_flags=0x20); self.wait(1.6)
        first = self.events()[0]
        self.tick(session_flags=0)
        self.wait(31)
        self.tick(session_flags=0x20); self.wait(1.6)
        second = self.events()[0]
        self.assertNotEqual(first['variant'], second['variant'])
        self.assertNotEqual(first['text'], second['text'])

    def test_pre_race_briefing_uses_actual_limits_fuel_and_no_grid_claim(self):
        self.tick(session_state=2, track_temp_c=32)
        event = next(e for e in self.events() if e['key'] == 'briefing')
        for text in ('Test Circuit', '20랩', '20.0리터', '80킬로미터', '32도'):
            self.assertIn(text, event['text'])
        self.assertNotIn('출발 순위', event['text'])
        self.tick(session_state=4)
        self.assertNotIn('briefing', self.keys())

    def test_late_enable_on_grid_refreshes_expired_briefing(self):
        self.tick(session_state=2)
        self.wait(121)
        self.assertNotIn('briefing', self.keys())
        self.radio.welcome(self.now)
        self.assertIn('briefing', self.keys())

    def test_previous_session_history_is_not_used_after_run_reset(self):
        self.tick(session_flags=0x20); self.wait(1.6)
        self.radio.ack(self.events()[0]['id'], self.now)
        self.tick(run='next', session_flags=0)
        self.assertFalse(self.radio.history)

    def test_lap_time_and_best_are_one_basic_message(self):
        self.tick(lap_completed=3, lap_last_s=99., lap_best_s=99.)
        self.assertEqual(self.keys(), ['personal_best'])
        self.assertIn('1분 39.00초', self.events()[0]['text'])
        self.tick(lap_completed=4, lap_last_s=102.)
        self.assertEqual(self.keys(), ['lap_time'])
        self.assertIn('1분 42.00초', self.events()[0]['text'])

    def test_best_is_not_claimed_after_pit_or_incident(self):
        self.tick(incidents=1)
        self.tick(lap_completed=3, lap_last_s=99., lap_best_s=99.)
        self.assertNotIn('personal_best', self.keys())
        self.assertIn('lap_time', self.keys())

    def test_lap_counter_can_arrive_before_new_lap_time(self):
        self.tick(lap_completed=3)
        self.assertNotIn('lap_time', self.keys())
        self.tick(advance=.04, lap_last_s=103.)
        self.assertIn('1분 43.00초', next(e for e in self.events() if e['key'] == 'lap_time')['text'])

    def test_position_waits_for_five_seconds_of_stability(self):
        self.tick(class_position=4)
        self.wait(1)
        self.assertNotIn('position', self.keys())
        self.tick(class_position=3)
        self.wait(4)
        self.assertNotIn('position', self.keys())
        self.wait(1.1)
        event = next(e for e in self.events() if e['key'] == 'position')
        self.assertIn('3위', event['text'])
        self.tick(class_position=4)
        self.assertNotIn('position', self.keys())

    def test_pit_service_and_speed_are_available_even_when_overlap_is_paused(self):
        self.tick(on_pit_road=True, speed_mps=24., pit_service_status=1)
        self.assertEqual(set(self.keys()), {'pit_enter', 'pit_speed', 'service_start'})
        self.tick(speed_mps=0., pit_service_status=100)
        self.assertNotIn('pit_speed', self.keys())
        self.assertIn('stall_left', self.keys())
        self.tick(pit_service_status=2)
        self.assertIn('service_done', self.keys())
        self.tick(on_pit_road=False)
        self.assertEqual(self.keys(), ['pit_exit'])

    def test_no_pit_speed_warning_without_explicit_limit(self):
        self.meta['pit_speed_limit_kph'] = None
        self.tick(on_pit_road=True, speed_mps=100.)
        self.assertNotIn('pit_speed', self.keys())
        self.assertFalse(self.radio.capabilities()['pit_speed'])

    def test_limiter_is_not_guessed_for_a_car_without_the_channel(self):
        self.tick(on_pit_road=True, engine_warnings=None)
        self.assertNotIn('limiter_off', self.keys())
        self.tick(engine_warnings=0x10)
        self.tick(engine_warnings=0)
        self.assertIn('limiter_off', self.keys())

    def test_repair_flag_is_cached_spotter_only_and_cancels_if_resolved(self):
        self.tick(session_flags=0x100000)
        self.assertEqual(self.keys(), ['repair'])
        self.wait(5.1)
        self.assertEqual(self.keys(), ['repair'])
        self.assertTrue(self.events()[0]['cached'])
        self.assertEqual(self.events()[0]['engineer'], 'spotter')
        self.tick(session_flags=0)
        self.assertFalse(self.events())

    def test_engine_alarm_and_detail_do_not_claim_a_specific_failure(self):
        self.tick(engine_warnings=4)
        self.assertIn('oil_pressure', self.keys())
        self.wait(5.1)
        self.assertTrue(any(e['engineer'] == 'race' for e in self.events()))
        self.tick(engine_warnings=0)
        self.assertFalse(self.events())

    def test_timer_laps_and_official_flag_are_not_duplicated(self):
        self.tick(session_time_remaining_s=301, session_laps_remaining=6)
        self.tick(session_time_remaining_s=299, session_laps_remaining=5, session_flags=0x1000)
        self.assertIn('time_300', self.keys())
        self.assertIn('five_laps', self.keys())
        self.assertNotIn('laps_5', self.keys())

    def test_gap_replay_and_new_run_clear_pending_not_replay_old_lap(self):
        self.tick(lap_completed=3, lap_last_s=105.)
        self.assertIn('lap_time', self.keys())
        self.tick(advance=.6)
        self.assertNotIn('lap_time', self.keys())
        self.tick(session_flags=0x8)
        self.tick(is_replay=True)
        self.assertFalse(self.events())
        self.tick(is_replay=False, run='new', session_flags=0)
        self.assertFalse(self.events())

    def test_ack_stops_retry_and_history_is_bounded(self):
        self.tick(session_flags=0x20); self.wait(1.6)
        event = self.events()[0]
        self.assertTrue(self.radio.ack(event['id'], self.now))
        self.assertFalse(self.events())
        self.assertFalse(self.radio.ack(event['id'], self.now))
        self.assertEqual(self.radio.history[0]['engineer'], 'spotter')

    def test_clean_pace_deviation_is_said_with_the_lap_time_in_one_call(self):
        for lap, last in zip(range(3, 6), (100.9, 101., 101.1)):
            self.tick(lap_completed=lap, lap_last_s=last)
            self.wait(.3)
        self.tick(lap_completed=6, lap_last_s=103.)
        self.wait(5.1)
        event = next(e for e in self.events() if e['key'] == 'lap_time')
        self.assertEqual(len(event['speech_parts']), 2)
        self.assertIn('느렸습니다', event['speech_parts'][1])
        self.assertIn('2.00초', event['text'])

    def test_incident_pattern_is_coach_not_setup_diagnosis(self):
        for lap, last in zip((3, 4), (100., 101.)):
            self.tick(lap_completed=lap, lap_last_s=last)
            self.wait(.3)
        self.tick(incidents=4)
        self.wait(6.1)
        event = next(e for e in self.events() if e['key'] == 'incident_coaching')
        self.assertEqual(event['engineer'], 'coach')
        self.assertIn('원인은 아직 확인되지', event['text'])

    def test_unit_strings_are_explicit_and_time_rounding_has_no_sixty_seconds(self):
        self.assertAlmostEqual(parse_measure('50 mph', 'speed'), 80.4672)
        self.assertEqual(parse_measure('30 min', 'seconds'), 1800)
        for value in ('80', 80, 'unlimited', None):
            self.assertIsNone(parse_measure(value, 'speed'))
        self.assertEqual(duration(119.999), '2분 0.00초')

    def test_four_role_routing(self):
        for text, role in [('랩타임 알려줘', 'spotter'), ('경기전 브리핑', 'spotter'),
                           ('피트 제한속도 얼마야', 'spotter'), ('지금 몇 등?', 'spotter'),
                           ('랩타임 왜 느려?', 'race'), ('뒤차 간격', 'race'),
                           ('완주 연료 돼?', 'strategy'), ('코너가 어려워', 'coach')]:
            self.assertEqual(choose_role(text), role, text)


class RadioRouteTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.now = [1000.]
        self.live = LiveTelemetry(clock=lambda: self.now[0])
        self.sample = {'session_time': 1., 'session_num': 0, 'on_track': True, 'is_replay': False,
                       'session_state': 4,
                       'on_pit_road': False, 'car_left_right': 1, 'class_position': 3, 'lap_last_s': 101.2}
        self.live.ingest({}, self.sample)
        self.speech = MagicMock()
        self.speech.synthesize.return_value = base64.b64encode(b'ID3' + b'x' * 500).decode()
        audio_env = patch.dict('os.environ', {'IRACING_RADIO_AUDIO_DIR': ''})
        audio_env.start(); self.addCleanup(audio_env.stop)
        self.store = VoiceNoteStore(Path(self.work.name) / 'notes.sqlite3')
        self.coach = VoiceCoach(self.live, self.store, speech=self.speech)
        self.app = FastAPI()
        register_voice_routes(self.app, self.coach, None, None, None, None)

    def emit(self):
        self.live.radio.emit('position', self.now[0], '현재 클래스 3위입니다.', condition=('position', 3))
        return self.live.radio.view(self.now[0])['events'][0]['id']

    def test_measured_basic_answer_and_render_cache_do_not_write_driver_notes(self):
        reply, source = self.coach.answer('랩타임 알려줘', {**self.live.context(), 'engineer': 'spotter'})
        self.assertIn('1분 41.20초', reply)
        identifier = self.emit()
        with TestClient(self.app) as client:
            self.assertTrue(client.post(f'/api/live-coach/spotter/radio/{identifier}/render').json()['audio_base64'])
            client.post(f'/api/live-coach/spotter/radio/{identifier}/render')
            self.assertEqual(self.speech.synthesize.call_count, 1)
            self.assertTrue(client.post(f'/api/live-coach/spotter/radio/{identifier}/ack').json()['accepted'])
        self.assertEqual(self.store.list(), [])

    def test_cloud_completion_is_discarded_if_condition_changes(self):
        identifier = self.emit()
        def change(*_, **__):
            self.now[0] += .1
            self.live.ingest({}, {**self.sample, 'session_time': 1.1, 'class_position': 4})
            return base64.b64encode(b'ID3' + b'x' * 500).decode()
        self.speech.synthesize.side_effect = change
        with TestClient(self.app) as client:
            result = client.post(f'/api/live-coach/spotter/radio/{identifier}/render').json()
        self.assertEqual(result, {'event': None})


if __name__ == '__main__':
    unittest.main()


class BlueAndPitSpeedTests(unittest.TestCase):
    setUp = RaceRadioTests.setUp
    tick = RaceRadioTests.tick
    wait = RaceRadioTests.wait
    events = RaceRadioTests.events
    keys = RaceRadioTests.keys

    def test_a_blue_flag_up_for_a_moment_is_not_called(self):
        self.tick(session_flags=0x20); self.wait(1.2)                 # 1.3 s just over the line at a rolling start
        self.tick(session_flags=0)
        self.wait(5)
        self.assertNotIn('blue', [e['key'] for e in self.radio.pending])
        self.tick(session_flags=0x20); self.wait(1.6)                 # a real one stays up
        self.assertIn('blue', self.keys())
        self.wait(10)
        self.assertEqual([e['key'] for e in self.radio.pending].count('blue'), 1)   # once while it stays up

    def blue_traffic(self, other_lap=0, other_pct=.49, own_pct=.5):
        self.meta.update(player_idx=0, cars=[
            {'idx': 0, 'lap_completed': 0, 'distance_pct': own_pct,
             'on_pit_road': False, 'track_surface': 3},
            {'idx': 1, 'lap_completed': other_lap, 'distance_pct': other_pct,
             'on_pit_road': False, 'track_surface': 3},
        ])

    def test_first_lap_startup_flag_longer_than_hold_needs_lapping_traffic(self):
        # Recorded Laguna Seca startup flag lasted 1.9 s, beyond the old hold.
        for completed in (0, None):
            with self.subTest(completed=completed):
                self.setUp()
                self.blue_traffic()
                self.tick(lap_completed=completed, session_flags=0x20)
                self.wait(3)
                self.assertNotIn('blue', self.keys())
                self.assertIsNone(self.radio.blue_since)

    def test_start_line_lap_counter_difference_is_not_a_lapping_car(self):
        # The front car crossed the line first but is only .02 laps ahead.
        self.blue_traffic(other_lap=1, other_pct=.01, own_pct=.99)
        self.tick(lap_completed=0, session_flags=0x20)
        self.wait(2)
        self.assertNotIn('blue', self.keys())

    def test_late_starter_can_have_genuine_first_lap_blue_and_loses_it_after_pass(self):
        self.blue_traffic(other_lap=1)
        self.tick(lap_completed=0, session_flags=0x20)
        self.wait(1.6)
        event = next(e for e in self.radio.pending if e['key'] == 'blue')
        self.assertTrue(self.radio.valid(event, self.now))
        self.meta['cars'][1]['distance_pct'] = .51
        self.tick()
        self.assertFalse(self.radio.valid(event, self.now))
        self.assertNotIn('blue', self.keys())

    def test_first_lap_blue_requires_fresh_on_track_lapping_car_and_actual_flag(self):
        for changes in ({'on_pit_road': True}, {'track_surface': -1}, {'lap_completed': None},
                        {'distance_pct': .2}):
            with self.subTest(changes=changes):
                self.setUp()
                self.blue_traffic(other_lap=1)
                self.meta['cars'][1].update(changes)
                self.tick(lap_completed=0, session_flags=0x20)
                self.wait(2)
                self.assertNotIn('blue', self.keys())
        self.setUp()
        self.blue_traffic(other_lap=1)
        self.tick(lap_completed=0, session_flags=0)
        self.wait(2)
        self.assertNotIn('blue', self.keys())
        self.tick(session_flags=0x20)
        self.wait(1.6)
        event = next(e for e in self.radio.pending if e['key'] == 'blue')
        for _ in range(21):
            self.now += .1
            self.sample['session_time'] += .1
            self.radio.ingest(self.sample, 'run', self.now, metadata=None)
        self.assertFalse(self.radio.valid(event, self.now))

    def test_formation_and_pit_flags_do_not_count_toward_racing_hold(self):
        for changes in ({'session_state': 3}, {'on_pit_road': True}, {'session_state': None}):
            with self.subTest(changes=changes):
                self.setUp()
                self.tick(session_flags=0x20, **changes)
                self.wait(3)
                self.assertNotIn('blue', self.keys())
                self.tick(session_state=4, on_pit_road=False)
                self.wait(1.2)
                self.assertNotIn('blue', self.keys())
                self.wait(.4)
                self.assertIn('blue', self.keys())

    def test_unknown_flag_replay_and_car_exit_restart_observation(self):
        for changes in ({'session_flags': None}, {'is_replay': True}, {'on_track': False}):
            with self.subTest(changes=changes):
                self.setUp()
                self.tick(session_flags=0x20)
                self.wait(1)
                self.tick(**changes)
                self.wait(1)
                self.tick(session_flags=0x20, is_replay=False, on_track=True)
                self.wait(1.2)
                self.assertNotIn('blue', self.keys())
                self.wait(.4)
                self.assertIn('blue', self.keys())

    def test_waiting_blue_cannot_return_on_a_new_brief_flag(self):
        self.tick(session_flags=0x20)
        self.wait(1.6)
        event = next(e for e in self.radio.pending if e['key'] == 'blue')
        self.tick(session_flags=0)
        self.tick(session_flags=0x20)
        self.assertFalse(self.radio.valid(event, self.now))
        self.assertNotIn('blue', self.keys())
        self.assertNotIn(event, self.radio.pending)

    def test_practice_blue_does_not_require_race_lap_progress(self):
        self.meta['session_type'] = 'Practice'
        self.tick(session_flags=0x20, lap_completed=0)
        self.wait(1.6)
        self.assertIn('blue', self.keys())

    def test_the_limiter_hovering_just_over_the_limit_is_not_called_but_real_overspeed_is(self):
        self.tick(on_pit_road=True, speed_mps=81.5 / 3.6)              # 80 km/h limit, limiter on: +1.5 km/h
        self.assertNotIn('pit_speed', self.keys())
        self.tick(speed_mps=82.9 / 3.6)
        self.assertNotIn('pit_speed', self.keys())
        self.tick(speed_mps=84. / 3.6)                                  # entering the lane still braking
        self.assertIn('pit_speed', self.keys())
