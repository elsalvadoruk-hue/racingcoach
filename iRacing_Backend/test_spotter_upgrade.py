"""Traffic, pit geometry, metadata and setting regressions without game/cloud."""
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_telemetry import LiveTelemetry
from race_engineer import read_race, parse_measure
from race_radio import RaceRadio
from spotter import Spotter
from spotter_context import TrafficApproach, PitBoxGuide
from voice_coach import VoiceCoach, register_voice_routes
from voice_notes import VoiceNoteStore


def traffic_race(t, side='behind', different=True, lap_offset=0, closing=True):
    own_progress = 5.5 + t * .01
    delta = -.04 if side == 'behind' else .04
    rate = .004 if side == 'behind' else -.004
    other_progress = own_progress + delta + (rate * t if closing else -rate * t) + lap_offset
    def car(idx, value, pace, class_id):
        return {'idx': idx, 'name': f'driver {idx}', 'class_id': class_id, 'class_name': str(class_id),
                'lap_completed': math.floor(value), 'distance_pct': value % 1,
                'last_lap_s': pace, 'track_surface': 3, 'on_pit_road': False}
    return {'session_type': 'Race', 'state': 4, 'player_idx': 0,
            'cars': [car(0, own_progress, 100, 1),
                     car(1, other_progress, 70 if side == 'behind' else 130, 2 if different else 1)]}


class TrafficTests(unittest.TestCase):
    def test_faster_and_slower_class_need_persistent_closing(self):
        for side, key in [('behind', 'traffic_faster_behind'), ('ahead', 'traffic_slower_ahead')]:
            tracker = TrafficApproach()
            for t in range(-15, 3):
                tracker.ingest(traffic_race(t, side), t)
                call = tracker.view(t)['nearby'][0]['call_key']
                # The gap in seconds must be seen to shrink for a few seconds first.
                if t < -11: self.assertIsNone(call)
            self.assertEqual(call, key)

    def test_same_class_lap_offset_is_distinct_from_physical_side(self):
        for side, offset, key in [('behind', 1, 'traffic_lapping_behind'), ('ahead', -1, 'traffic_lapped_ahead')]:
            tracker = TrafficApproach()
            for t in range(-15, 3): tracker.ingest(traffic_race(t, side, False, offset), t)
            view = tracker.view(2)
            self.assertEqual(view[side]['call_key'], key)
            self.assertEqual(view[side]['side'], side)

    def test_same_lap_battle_is_visible_without_lapping_call(self):
        tracker = TrafficApproach()
        for t in range(3): tracker.ingest(traffic_race(t, different=False), t)
        self.assertEqual(tracker.view(2)['behind']['kind'], 'same_class')
        self.assertIsNone(tracker.view(2)['behind']['call_key'])

    def test_opening_unknown_class_and_missing_pace_do_not_guess(self):
        for mode in ('opening', 'class', 'pace'):
            tracker = TrafficApproach()
            for t in range(-9, 3):
                race = traffic_race(t, closing=mode != 'opening')
                if mode == 'class': race['cars'][1]['class_id'] = None
                if mode == 'pace': race['cars'][1]['last_lap_s'] = None
                tracker.ingest(race, t)
            self.assertFalse(any(c['call_key'] for c in tracker.view(2)['nearby']))

    def test_stale_pit_jump_and_disconnected_cars_drop_approach(self):
        for mode in ('pit', 'jump', 'missing', 'pause'):
            tracker = TrafficApproach()
            for t in range(-9, 3): tracker.ingest(traffic_race(t), t)
            self.assertTrue(tracker.view(2)['behind']['call_key'])
            race = traffic_race(3)
            if mode == 'pit': race['cars'][1]['on_pit_road'] = True
            if mode == 'jump': race['cars'][1]['lap_completed'] += 1
            if mode == 'missing': race['cars'] = race['cars'][:1]
            tracker.ingest(race, 6 if mode == 'pause' else 3)
            self.assertFalse(any(c['call_key'] for c in tracker.view(6 if mode == 'pause' else 3)['nearby']))
        self.assertFalse(tracker.view(8)['available'])

    def test_start_line_wrap_preserves_correct_side(self):
        tracker = TrafficApproach()
        for t in range(-15, 3):
            race = traffic_race(t, 'ahead')
            for car in race['cars']:
                value = car['lap_completed'] + car['distance_pct'] + .49
                car.update(lap_completed=math.floor(value), distance_pct=value % 1)
            tracker.ingest(race, t)
        self.assertEqual(tracker.view(2)['ahead']['call_key'], 'traffic_slower_ahead')

    def test_a_follower_bunching_up_in_braking_zones_is_not_an_approach(self):
        # Both cars drive the same lap: fast on the straights, a third of that speed in two slow corners.
        def position(seconds):
            """Lap fraction after this many seconds of an 80 s lap, integrated in small steps."""
            value, step = 0., .01
            for _ in range(int(round(seconds / step))):
                slow = .25 < value % 1 < .30 or .70 < value % 1 < .75
                value += step * (.006 if slow else .0155)
            return value

        def run(lead_s, catching_s_per_s=0.):
            tracker, called = TrafficApproach(), []
            for t in range(20, 180):
                def car(idx, value, class_id, estimate):
                    return {'idx': idx, 'name': str(idx), 'class_id': class_id, 'lap_completed': math.floor(value), 'distance_pct': value % 1,
                            'last_lap_s': 80., 'class_est_lap_s': estimate, 'track_surface': 3, 'on_pit_road': False}
                behind = position(t - lead_s + catching_s_per_s * (t - 20))
                tracker.ingest({'session_type': 'Race', 'player_idx': 0,
                                'cars': [car(0, position(t), 1, 80.), car(1, behind, 2, 70.)]}, float(t))
                view = tracker.view(float(t))['behind']
                if view and view['call_key']:
                    called.append((t, view['time_gap_s']))
            return called
        # A faster-class car that sits 1.5 s behind for two laps: the gap in metres halves into each slow corner,
        # the gap in seconds never changes.
        self.assertEqual(run(1.5), [])
        # The same car really catching up, 0.05 s every second from 6 s back.
        called = run(6., .05)
        self.assertTrue(called)
        self.assertLessEqual(called[0][1], 2.6)

    def test_a_car_that_keeps_following_is_called_once_until_it_gets_past(self):
        radio = RaceRadio()
        sample = {'session_state': 4, 'session_flags': 0, 'on_track': True, 'is_replay': False, 'on_pit_road': False}
        nearby = [{'idx': 7, 'side': 'behind', 'gap_estimate_s': 1., 'close': True, 'call_key': 'traffic_faster_behind', 'close_key': None}]
        clock = [0.]
        radio.ingest({**sample, 'session_time': 0.}, 'run', 0.)
        radio.traffic.view = lambda now: {'available': True, 'nearby': nearby, 'ahead': None, 'behind': None}

        def waiting(until):
            # Samples keep coming: a gap in them would reset the radio and what it remembers.
            while clock[0] < until:
                clock[0] = min(until, clock[0] + .4)
                radio.ingest({**sample, 'session_time': clock[0]}, 'run', clock[0])
            return [e for e in radio.view(until)['events'] if e['key'].startswith(('traffic_', 'close_'))]
        first = waiting(.4)
        self.assertEqual([e['key'] for e in first], ['traffic_faster_behind'])
        # Not heard (the radio was busy): it may be tried again once the shared pause has passed.
        again = waiting(30.8)
        self.assertEqual(len(again), 1)
        radio.ack(again[0]['id'], 30.8)
        # Heard: while the same car stays there it is not called again.
        self.assertEqual(waiting(80.), [])
        self.assertEqual(waiting(150.), [])
        # Another car is.
        nearby.append({**nearby[0], 'idx': 8})
        other = waiting(151.)
        self.assertEqual(len(other), 1)
        radio.ack(other[0]['id'], 151.)
        nearby.pop()
        # It got past and later comes up from behind again: called again.
        nearby[0] = {**nearby[0], 'side': 'ahead', 'call_key': None}
        self.assertEqual(waiting(190.), [])
        nearby[0] = {**nearby[0], 'side': 'behind', 'call_key': 'traffic_faster_behind'}
        self.assertEqual(len(waiting(191.)), 1)
        # A car sitting on the bumper is announced once as well.
        nearby[0] = {**nearby[0], 'idx': 9, 'call_key': None, 'close_key': 'close_behind'}
        close = waiting(192.)
        self.assertEqual([e['key'] for e in close], ['close_behind'])
        radio.ack(close[0]['id'], 192.)
        self.assertEqual(waiting(300.), [])
        # It dropped back and arrives again: that is a new arrival.
        nearby[0] = {**nearby[0], 'gap_estimate_s': 2., 'close_key': None}
        self.assertEqual(waiting(310.), [])
        nearby[0] = {**nearby[0], 'gap_estimate_s': .4, 'close_key': 'close_behind'}
        self.assertEqual(len(waiting(311.)), 1)

    def test_radio_uses_cached_spotter_and_drops_invalid_approach(self):
        radio = RaceRadio()
        sample = {'session_time': 0, 'session_state': 4, 'session_flags': 0,
                  'on_track': True, 'is_replay': False, 'on_pit_road': False}
        for tick in range(-90, 21):
            t = tick / 10
            radio.ingest({**sample, 'session_time': t + 9}, 'run', t,
                         traffic_race(t) if tick % 10 == 0 else None)
        event = next(e for e in radio.view(2)['events'] if e['key'] == 'traffic_faster_behind')
        self.assertTrue(event['cached'])
        self.assertEqual((event['engineer'], event['priority']), ('spotter', 2))
        radio.ingest({**sample, 'session_time': 2.1, 'session_flags': 8}, 'run', 2.1)
        self.assertFalse(any(e['key'].startswith('traffic_') for e in radio.view(2.1)['events']))


class PitBoxTests(unittest.TestCase):
    def setUp(self):
        self.guide = PitBoxGuide()
        self.metadata = {'track_length_m': 1000, 'pit_box_pct': .1, 'player_idx': 0}
        self.previous = {'on_pit_road': False}
        self.calls = []

    def sample(self, pct, **extra):
        return {'distance_pct': pct, 'speed_mps': 20, 'on_pit_road': True,
                'on_track': True, 'is_replay': False, 'tow_time_s': 0, 'pit_service_status': 0, **extra}

    def tick(self, n, **extra):
        sample = self.sample((.85 + n * .01) % 1, **extra)
        threshold = self.guide.ingest(sample, self.previous, self.metadata, n * .5)
        self.previous = sample
        if threshold: self.calls.append(threshold)
        return threshold

    def test_checked_motion_distance_countdown_wraps_and_never_replays_passed_box(self):
        for n in range(25): self.tick(n)
        self.assertEqual(self.calls, [200, 100, 50, 20, 10])
        self.assertAlmostEqual(self.guide.view()['distance_estimate_m'], 10)
        for n in range(25, 30): self.tick(n)
        self.assertFalse(self.guide.view()['ready'])
        self.assertEqual(self.guide.view()['reason'], '박스 위치 통과')
        self.assertEqual(self.calls, [200, 100, 50, 20, 10])

    def test_missing_invalid_position_and_tow_never_produce_countdown(self):
        for field, value in [('pit_box_pct', None), ('pit_box_pct', -1), ('track_length_m', None)]:
            self.setUp(); self.metadata[field] = value
            for n in range(12): self.tick(n)
            self.assertFalse(self.calls)
            self.assertFalse(self.guide.view()['ready'])
        self.setUp()
        for n in range(12): self.tick(n, tow_time_s=5)
        self.assertFalse(self.calls)

    def test_enable_mid_pit_and_stationary_car_do_not_make_up_distance(self):
        self.previous = {'on_pit_road': True}
        for n in range(12): self.tick(n)
        self.assertFalse(self.calls)
        self.setUp()
        for n in range(10):
            sample = self.sample(.85, speed_mps=0)
            self.guide.ingest(sample, self.previous, self.metadata, n * .1)
            self.previous = sample
        self.assertFalse(self.guide.view()['ready'])

    def test_jump_reversal_or_projection_mismatch_blocks_until_next_entry(self):
        for bad in (.3, .84, .86):
            self.setUp(); self.tick(0); self.tick(1); self.tick(2)
            sample = self.sample(bad, speed_mps=80)
            self.guide.ingest(sample, self.previous, self.metadata, 1.1)
            for n in range(3, 12): self.tick(n)
            self.assertFalse(self.guide.view()['ready'])
            self.assertFalse(self.calls)

    def test_radio_countdown_default_off_and_short_lived_when_enabled(self):
        for enabled in (False, True):
            radio = RaceRadio(); radio.pit_box_enabled = enabled
            s = self.sample(.85, on_pit_road=False, session_time=0)
            radio.ingest(s, 'run', 0, self.metadata)
            seen = []
            for n in range(63):
                s = self.sample((.85 + n * .002) % 1, session_time=(n + 1) * .1)
                radio.ingest(s, 'run', (n + 1) * .1)
                seen.extend(e for e in radio.view((n + 1) * .1)['events'] if e['key'].startswith('pit_box_'))
            self.assertEqual(bool(seen), enabled)
            if enabled:
                self.assertTrue(all(e['cached'] and e['expires_in_ms'] <= 800 for e in seen))
                s.update(on_pit_road=False, session_time=6.4)
                radio.ingest(s, 'run', 6.4)
                self.assertFalse(any(e['key'].startswith('pit_box_') for e in radio.view(6.4)['events']))


class SettingsMetadataTests(unittest.TestCase):
    def test_sdk_metadata_is_player_specific_and_unit_checked(self):
        values = {'DriverInfo': {'DriverCarIdx': 2, 'DriverPitTrkPct': .9, 'Drivers': [
            {'CarIdx': 1, 'DriverPitTrkPct': .4}, {'CarIdx': 2, 'DriverPitTrkPct': .9}]},
                  'WeekendInfo': {'TrackLength': '4.250 km'}, 'SessionInfo': {}}
        race = read_race(values, set(), 0)
        self.assertEqual(race['pit_box_pct'], .9)
        self.assertEqual(race['track_length_m'], 4250)
        values['DriverInfo']['DriverPitTrkPct'] = -1
        self.assertIsNone(read_race(values, set(), 0)['pit_box_pct'])
        self.assertIsNone(parse_measure('4.250', 'distance'))
        self.assertIsNone(parse_measure('80 km/h', 'distance'))

    def test_api_validates_settings_and_session_resets_preserve_them(self):
        with tempfile.TemporaryDirectory() as root:
            live = LiveTelemetry(clock=lambda: 1)
            app = FastAPI()
            register_voice_routes(app, VoiceCoach(live, VoiceNoteStore(Path(root) / 'notes.db'), speech=MagicMock()), None, None, None, None)
            with TestClient(app) as client:
                for payload in ({'repeat_seconds': 0}, {'clear_delay': .01}, {'clear_delay': 1}):
                    self.assertEqual(client.put('/api/live-coach/spotter/settings', json=payload).status_code, 422)
                result = client.put('/api/live-coach/spotter/settings', json={
                    'repeat_seconds': 2, 'clear_delay': .3, 'traffic_enabled': False, 'pit_box_enabled': True})
                self.assertEqual(result.status_code, 200)
                live.spotter.reset(); live.radio.reset()
                self.assertEqual(live.spotter_settings(), result.json()['settings'])

    def test_repeat_is_directional_and_clear_confirmation_is_configurable(self):
        spotter = Spotter(); spotter.repeat_seconds = 2; spotter.clear_delay = .3
        s = {'on_track': True, 'is_replay': False, 'on_pit_road': False}
        for n in range(23):
            spotter.ingest({**s, 'car_left_right': 5, 'session_time': n * .1}, 'run', n * .1)
        self.assertEqual([e['key'] for e in spotter.events], ['two_left', 'hold_two_left'])
        for n in range(23, 26): spotter.ingest({**s, 'car_left_right': 1, 'session_time': n * .1}, 'run', n * .1)
        self.assertNotEqual(spotter.confirmed, 1)
        spotter.ingest({**s, 'car_left_right': 1, 'session_time': 2.61}, 'run', 2.61)
        self.assertEqual(spotter.events[-1]['key'], 'clear_left')
