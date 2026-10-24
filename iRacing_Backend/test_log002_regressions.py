"""Delivery baselines, session notices and the final buffered race timing rows."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import inspect
import tempfile
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_telemetry import LiveTelemetry
from log_pack import unpack
from session_log_extras import nearby_cars
from test_straight_brief import sample
from test_timing_log import race
from timing_log import TimingLog, read
import voice_coach


class DeliveryRegressionTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.
        self.live = LiveTelemetry(clock=lambda: self.now)
        self.live.run_id = 'log002-run'
        self.live.identity = {'game': 'lmu'}
        self.radio = self.live.radio
        self.metadata = {'session_type': 'Race'}
        self.current = sample(0, class_position=12)
        self.stored = []
        coach = SimpleNamespace(live=self.live, store=SimpleNamespace(record_radio=self.stored.extend), speech=None)
        app = FastAPI()
        with patch.object(voice_coach, 'SpotterAudio'):
            voice_coach.register_voice_routes(app, coach, None, None, None, None)
        self.client = TestClient(app)
        self.tick(0)

    def tick(self, seconds=.1, **updates):
        self.now += seconds
        self.current.update(updates, session_time=self.now - 1000.)
        self.radio.ingest(self.current, self.live.run_id, self.now, self.metadata)

    def hold(self, seconds=5.2, **updates):
        self.tick(**updates)
        for _ in range(round(seconds * 10)):
            self.tick()

    def event(self, key='position'):
        found = [e for e in self.radio.view(self.now)['events'] if e['key'] == key]
        self.assertTrue(found, f'Expected a current {key} call')
        return found[0]

    def feedback(self, event, status='completed', **extra):
        response = self.client.post('/api/live-coach/radio-delivery', json={'events': [dict(
            event_id=event['id'], run_id=event['run_id'], key=event['key'], status=status, **extra)]})
        self.assertEqual(response.status_code, 200)

    def test_cancelled_rank_does_not_reverse_direction_after_last_heard_rank(self):
        self.hold(class_position=11)
        heard = self.event()
        self.feedback(heard)
        self.radio.ack(heard['id'], self.now)
        self.hold(20, class_position=15)
        cancelled = self.event()
        self.feedback(cancelled, 'cancelled')
        self.hold(20, class_position=14)
        self.assertIn('내려', self.event()['speech_text'])

    def test_first_rank_and_legacy_ack_without_delivery_have_no_direction(self):
        self.hold(class_position=11)
        event = self.event()
        self.assertNotIn('올', event['speech_text'])
        self.radio.ack(event['id'], self.now)
        self.hold(20, class_position=14)
        self.assertNotIn('내려', self.event()['speech_text'])

    def test_interrupted_rank_requires_a_neutral_next_call(self):
        self.hold(class_position=11)
        self.feedback(self.event())
        self.hold(20, class_position=15)
        self.feedback(self.event(), 'interrupted')
        self.hold(20, class_position=14)
        self.assertNotIn('올', self.event()['speech_text'])
        self.assertNotIn('내려', self.event()['speech_text'])

    def test_delivery_after_ack_is_applied_but_old_completions_cannot_rewind_it(self):
        self.hold(class_position=11)
        first = self.event()
        self.radio.ack(first['id'], self.now)
        self.feedback(first)
        self.assertEqual(self.radio.position_said, 11)
        self.hold(20, class_position=13)
        second = self.event()
        self.feedback(second)
        self.feedback(first)
        self.assertEqual(self.radio.position_said, 13)

    def test_cooldown_does_not_drop_the_new_stable_rank(self):
        self.hold(class_position=11)
        first = self.event()
        self.feedback(first)
        self.radio.ack(first['id'], self.now)
        self.hold(class_position=10)
        self.assertFalse(any(e['key']=='position' for e in self.radio.view(self.now)['events']))
        self.hold(12)
        self.assertIn('10위', self.event()['speech_text'])

    def test_same_run_gap_does_not_repeat_briefing_but_clears_live_hazards(self):
        self.tick(session_state=1)
        first = self.event('briefing')
        self.radio.ack(first['id'], self.now)
        self.tick(1., session_state=3)
        self.radio.welcome(self.now)
        self.assertFalse(any(e['key']=='briefing' for e in self.radio.view(self.now)['events']))
        self.assertEqual(self.radio.hazard.view(self.now), [])

    def test_clock_rewind_and_new_run_can_brief_again(self):
        self.tick(session_state=1)
        self.radio.ack(self.event('briefing')['id'], self.now)
        self.tick(1., session_state=3)
        self.now += .1
        self.radio.ingest({**self.current, 'session_time': 0}, self.live.run_id, self.now, self.metadata)
        self.assertEqual(self.event('briefing')['run_id'], self.live.run_id)
        self.live.run_id = 'another-run'
        self.tick(session_state=1)
        self.assertEqual(self.event('briefing')['run_id'], 'another-run')

    def test_off_track_reset_does_not_replay_acknowledged_briefing(self):
        self.tick(session_state=1)
        self.radio.ack(self.event('briefing')['id'], self.now)
        self.live._reset_live('DISCONNECTED')
        self.tick(1., session_state=3)
        self.radio.welcome(self.now)
        self.assertFalse(any(e['key']=='briefing' for e in self.radio.view(self.now)['events']))

    def brief(self):
        self.current.update(workload_in_s=12.)
        self.radio.latest = dict(self.current)
        self.radio.last_sample = self.now
        self.live._brief_options = lambda *_: ([('delta', 'coach', [[('delta_behind', {'loss': .4})]])], {})
        self.live._brief_straight(self.current, self.now)
        return self.event('straight_brief')

    def test_cancelled_straight_does_not_consume_lap_topic(self):
        event = self.brief()
        self.feedback(event, 'cancelled')
        self.assertNotIn('delta_lap', self.live._briefed)
        self.assertNotIn('delta', self.live._briefed)
        self.assertIn('at', self.live._briefed)  # attempt pacing still prevents repeated retries

    def test_only_complete_straight_delivery_commits_topic_after_ack(self):
        event = self.brief()
        self.assertNotIn('delta_lap', self.live._briefed)
        self.radio.ack(event['id'], self.now)
        self.feedback(event)
        self.assertEqual(self.live._briefed['delta_lap'], self.current['lap_number'])

    def test_interrupted_or_muted_straight_does_not_commit_topic(self):
        event = self.brief()
        self.feedback(event, 'interrupted')
        self.feedback(event, 'muted')
        self.assertNotIn('delta_lap', self.live._briefed)

    def test_partial_straight_audio_does_not_consume_whole_topic(self):
        event = self.brief()
        self.feedback(event, dropped_parts=1)
        self.assertNotIn('delta_lap', self.live._briefed)

    def test_cancelled_topic_rotates_fairly_before_retrying(self):
        self.live._brief_options = lambda *_: ([
            ('position', 'spotter', [[('position_now', {'pos': 12})]]),
            ('time', 'spotter', [[('time_left', {'min': 20})]])], {})
        def attempt():
            self.current['workload_in_s'] = 12.
            self.radio.latest = dict(self.current)
            self.radio.pending.clear()
            self.live._brief_straight(self.current, self.now)
            return self.event('straight_brief')
        first = attempt()
        self.feedback(first, 'cancelled')
        self.tick(41)
        second = attempt()
        self.assertNotEqual(first['alert_categories'], second['alert_categories'])
        self.feedback(second, 'cancelled')
        self.tick(41)
        self.assertEqual(first['alert_categories'], attempt()['alert_categories'])

    def test_old_run_feedback_and_partial_rank_do_not_set_heard_position(self):
        self.hold(class_position=11)
        event = self.event()
        self.feedback(event, dropped_parts=1)
        self.assertIsNone(self.radio.position_said)
        self.radio.ingest(self.current, 'new-run', self.now, self.metadata)
        self.feedback(event)
        self.assertIsNone(self.radio.position_said)

    def test_compound_lap_rank_only_advances_after_whole_playback(self):
        self.hold(class_position=11)
        self.feedback(self.event())
        self.hold(20, class_position=15)
        def compound():
            return self.radio.emit('lap_time', self.now, 'lap and rank', cooldown=0,
                speech_parts=['이번 랩 기록입니다.', '현재 클래스 15위입니다.'],
                part_categories=['spotter.lap_time', 'spotter.position'])
        partial = compound()
        self.feedback(partial, dropped_parts=1)
        self.assertEqual(self.radio.position_said, 11)
        whole = compound()
        self.radio.ack(whole['id'], self.now)
        self.feedback(whole)
        self.assertEqual(self.radio.position_said, 15)
        self.hold(20, class_position=14)
        self.assertIn('올', self.event()['speech_text'])

    def test_cancelled_straight_keeps_bounded_local_window_diagnostics(self):
        event = self.brief()
        with self.assertLogs('race_radio', level='INFO') as captured:
            self.feedback(event, 'cancelled')
            self.feedback(event, 'cancelled')
        self.assertEqual(len(captured.records), 1)
        self.assertIn('latest_at_feedback', captured.output[0])
        self.assertIn('workload_s=12.0', captured.output[0])
        self.assertNotIn(event['speech_text'], captured.output[0])


class FinalTimingRegressionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)/'timing'
        self.log = TimingLog(self.folder)

    def record(self, at, state=4, lap=3):
        data = race(at, state=state)
        data['cars'][0]['lap_completed'] = lap
        self.log.record(data, 'timing-run', at)

    def last(self):
        return read(self.log.path)[1][-1]

    def test_checkered_transition_and_own_last_crossing_are_saved_without_batch_wait(self):
        for t in range(30):
            self.record(float(t))
        self.record(30., state=5)
        self.assertEqual(self.last()['t'], 30.)
        self.record(31., state=5, lap=4)
        self.assertEqual(self.last()['c'][0]['lap_completed'], 4)
        self.record(32., state=6, lap=4)
        self.assertEqual(self.last()['t'], 32.)
        with patch.object(self.log, 'flush', wraps=self.log.flush) as flush:
            self.record(33., state=6, lap=4)
            self.record(34., state=6, lap=4)
        self.assertEqual(flush.call_count, 0)

    def test_export_saves_the_partial_batch_before_finding_the_log(self):
        for t in range(45):
            self.record(float(t))
        calls=[]
        def prepare(run_id):
            calls.append(run_id)
            self.log.flush()
        options = {'prepare': prepare} if 'prepare' in inspect.signature(nearby_cars).parameters else {}
        provider = nearby_cars(None, self.folder, **options)
        extra = provider({'run_id':'timing-run','started_at':0.,'session_type':'Race'})
        _, rows = unpack(extra.files['timing.packed.xz'])
        self.assertEqual(rows[-1]['t'],44.)
        self.assertEqual(calls,['timing-run'])


if __name__ == '__main__':
    unittest.main()
