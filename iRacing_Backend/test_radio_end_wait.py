"""Terminal race phases and bounded queue diagnostics, using synthetic data only."""
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from race_radio import RaceRadio, RACE_START_CALLS
import session_log
from session_health import feedback_context
import voice_coach
from voice_notes import VoiceNoteStore


class RaceEndTests(unittest.TestCase):
    def setUp(self):
        self.radio = RaceRadio()
        self.now = 1000.
        self.sample = dict(session_state=4, session_flags=0, session_time=1., on_track=True,
                           is_replay=False, on_pit_road=False, lap_completed=2, class_position=5,
                           speed_mps=40., brake=0., steering_rad=0.)
        self.meta = dict(session_type='Race')
        self.tick()

    def tick(self, **patches):
        self.now += .1
        self.sample.update(patches)
        self.radio.ingest(self.sample, 'test-run', self.now, self.meta)

    def test_actual_post_finish_flag_transitions_never_create_start_events(self):
        for before, after in [(268697601, 268698113), (0x1, 0x60000201)]:
            with self.subTest(flags=after):
                self.setUp()
                self.tick(session_state=5, session_flags=before)
                self.tick(session_state=6, session_flags=after)
                self.assertFalse(RACE_START_CALLS.intersection(e['key'] for e in self.radio.pending))

    def test_already_queued_calls_are_invalid_after_terminal_phase(self):
        for key in RACE_START_CALLS:
            with self.subTest(key=key):
                self.setUp()
                self.tick(session_state=3)
                event = self.radio.emit(key, self.now, 'start', cooldown=0)
                self.assertTrue(self.radio.valid(event, self.now))
                self.tick(session_state=6, session_flags=0x200)
                self.assertFalse(self.radio.valid(event, self.now))
                self.assertIsNone(self.radio.emit(key, self.now, 'start', cooldown=0))

    def test_own_finish_status_or_result_suppresses_all_start_calls(self):
        for status in (1, 2, 3):
            with self.subTest(status=status):
                self.setUp()
                self.tick(finish_status=status)
                self.assertTrue(all(self.radio.emit(k,self.now,'start',cooldown=0) is None for k in RACE_START_CALLS))
        self.setUp()
        self.radio.result = {'class_position': 5}
        self.assertIsNone(self.radio.emit('one_to_green',self.now,cooldown=0))

    def test_genuine_formation_and_caution_restart_and_safety_flags_survive(self):
        for state, flags in ((3,0x200), (4,0xC200), (4,0x201)):
            with self.subTest(state=state,flags=flags):
                self.setUp()
                self.tick(session_state=state,session_flags=flags)
                self.assertTrue(any(e['key']=='one_to_green' and self.radio.valid(e,self.now) for e in self.radio.pending))
        self.setUp()
        self.tick(session_state=6,session_flags=0x200 | 0x10000)
        self.assertTrue(any(e['key']=='black' and self.radio.valid(e,self.now) for e in self.radio.pending))


class QueueWaitTests(unittest.TestCase):
    def waiting(self):
        return dict(scope='QUEUE_OBSERVATIONS', observations=2, elapsed_ms=1900.,
                    reasons={'overlap':1,'audio_busy':1}, last_reason='audio_busy',
                    preparation=dict(requests=1,requested_ms=25.,response_ms=90.,status='ready'))

    def test_strict_bounded_nonidentifying_schema(self):
        cls = voice_coach.RadioQueueDiagnostics
        self.assertEqual(cls(**self.waiting()).observations,2)
        for patch in ({'scope':'ORIGINAL_SDK_DECISION'}, {'device_id':'private'},
                      {'observations':True}, {'observations':65536}, {'elapsed_ms':float('nan')},
                      {'elapsed_ms':120001}, {'last_reason':'private'},
                      {'reasons':{'overlap':True}}, {'reasons':{'private':1}},
                      {'preparation':dict(requests=256,requested_ms=0,status='ready')},
                      {'preparation':dict(requests=1,requested_ms=0,status='headset')},
                      {'preparation':dict(requests=1,requested_ms=0,status='ready',device_id='private')}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                cls(**{**self.waiting(),**patch})

    def test_latest_overlap_context_is_finite_and_never_attached_to_old_run(self):
        live=SimpleNamespace(run_id='synthetic',radio=SimpleNamespace(latest={'car_left_right':3},last_sample=1.))
        self.assertEqual(feedback_context(live,'synthetic',2.)['sample']['car_left_right'],3)
        live.radio.latest['car_left_right']=float('nan')
        self.assertIsNone(feedback_context(live,'synthetic',2.)['sample']['car_left_right'])
        self.assertNotIn('sample',feedback_context(live,'old',2.))

    def test_actual_api_store_and_export_preserve_waiting_and_legacy_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            store=VoiceNoteStore(str(Path(folder)/'notes.sqlite3'))
            live=SimpleNamespace(lock=threading.Lock(),run_id='synthetic',identity={'game':'iracing'},
                                 pit_wall=SimpleNamespace(race={'session_type':'Race'}))
            coach=SimpleNamespace(live=live,store=store,speech=None)
            app=FastAPI()
            with patch.object(voice_coach,'SpotterAudio'):
                voice_coach.register_voice_routes(app,coach,None,None,None,None)
            rows=[dict(event_id='synthetic:1',run_id='synthetic',key='lap_time',status='expired',
                       reason='no_playback_window',waiting=self.waiting()),
                  dict(event_id='synthetic:2',run_id='synthetic',key='personal_best',status='completed')]
            client=TestClient(app)
            self.assertEqual(client.post('/api/live-coach/radio-delivery',json={'events':rows}).status_code,200)
            rows[0]['waiting']['reasons']={'device_id':1}
            self.assertEqual(client.post('/api/live-coach/radio-delivery',json={'events':rows}).status_code,422)
            _,data=session_log.export(store,'synthetic','2.5.3')
            _,radio,_=session_log.read(data)
            self.assertEqual(radio[0]['waiting'],self.waiting())
            self.assertIsNone(radio[1].get('waiting'))


if __name__ == '__main__':
    unittest.main()
