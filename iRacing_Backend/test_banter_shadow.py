"""Synthetic shadow opportunities, persistence, API and export; no audio or SDK."""
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from banter_catalog import CATALOG, ACTIVE_TRIGGERS
from banter_shadow import BanterShadow, gate, choose
from voice_notes import VoiceNoteStore
import session_log

CLIENT = dict(audio_busy=False, engineer_busy=False, pending_audio=False, muted=False)
SAMPLE = dict(on_track=True, is_replay=False, on_pit_road=False, car_left_right=1,
              session_state=4, session_flags=0, pace_mode=4, speed_mps=60.,
              brake=0., throttle=1., steering_rad=0., workload_in_s=30., speech_window_s=25.)


def proposal(shadow=None, multi=False):
    shadow = shadow or BanterShadow()
    for i in range(141):
        t = i / 10
        side = (4 if multi else 2) if 20 <= i < 100 else 1
        shadow.ingest({**SAMPLE, 'session_time': t, 'car_left_right': side}, 'run', 1000+t, True)
    return shadow


def feedback(shadow):
    candidate = shadow.view(1014.)[0]
    return shadow.feedback({**candidate, **CLIENT, 'session_time': 14.}, 1014.)


def record(at=1000., trigger='overlap_resolved', key='one', **changes):
    return dict(run_id='run', candidate_id=key, at=at, session_time=14., sample=dict(SAMPLE),
                trigger=trigger, reasons=[], **changes)


class GateTests(unittest.TestCase):
    def test_pacing_values_match_installed_sdk_and_start_modes_always_defer(self):
        import irsdk
        self.assertEqual(irsdk.PaceMode.not_pacing, 4)
        self.assertEqual(gate({**SAMPLE, 'pace_mode': irsdk.PaceMode.not_pacing}, CLIENT), [])
        for mode in (irsdk.PaceMode.single_file_start, irsdk.PaceMode.double_file_start,
                     irsdk.PaceMode.single_file_restart, irsdk.PaceMode.double_file_restart):
            self.assertIn('PACING_UNCONFIRMED_OR_ACTIVE', gate({**SAMPLE, 'pace_mode': mode}, CLIENT))
    def test_explicit_green_and_all_gates_are_required(self):
        self.assertEqual(gate(SAMPLE, CLIENT), [])
        for key, values in {'on_track': [None, False, 1], 'is_replay': [None, True, 0],
                            'on_pit_road': [None, True, 0], 'car_left_right': [None, False, 0, 4, float('nan')],
                            'session_flags': [None, False, 8, -1, .2], 'pace_mode': [None, False, 1],
                            'session_state': [None, False, 3], 'speed_mps': [None, False, 19],
                            'brake': [None, .1], 'steering_rad': [None, .3]}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.assertTrue(gate({**SAMPLE, key: value}, CLIENT))
        for key in CLIENT:
            for value in [None, True, 0]:
                self.assertTrue(gate(SAMPLE, {**CLIENT, key: value}))

    def test_catalog_and_inactive_triggers(self):
        self.assertEqual(len(CATALOG), 72)
        self.assertEqual(len({p['id'] for p in CATALOG}), 72)
        self.assertEqual(len([p for p in CATALOG if p['trigger'] in ACTIVE_TRIGGERS]), 72)
        self.assertTrue(all(p['text'] and p['motif'] for p in CATALOG))
        self.assertEqual(choose(record(trigger='unknown'), [])['reasons'], ['UNSUPPORTED_TRIGGER'])

    def test_budget_and_unknowns_fail_closed(self):
        for value in [None, False, float('nan'), 0., 2.]:
            value_record = record()
            value_record['sample']['speech_window_s'] = value
            self.assertEqual(choose(value_record, [])['status'], 'DEFERRED')
        self.assertEqual(choose(record(), [])['status'], 'ELIGIBLE_SHADOW')

    def test_cooldown_clock_reversal_and_repetition_eventually_cycle(self):
        history = []
        for i in range(50):
            selected = choose(record(at=1000.+600*i, key=str(i), trigger=ACTIVE_TRIGGERS[i % 2]), history)
            self.assertEqual(selected['status'], 'ELIGIBLE_SHADOW')
            self.assertNotIn(selected['selected']['id'], [r['selected']['id'] for r in history[:8]])
            self.assertNotIn(selected['selected']['motif'], [r['selected']['motif'] for r in history[:4]])
            history.insert(0, selected)
        for at in [1000., history[0]['at']+599.9]:
            self.assertEqual(choose(record(at=at), history)['reasons'], ['SHADOW_COOLDOWN'])


class ShadowTests(unittest.TestCase):
    def test_live_telemetry_exposes_candidates_and_resets_on_disconnect(self):
        from live_telemetry import LiveTelemetry
        from test_oval_spotter import IDENTITY
        now = [1000.]
        live = LiveTelemetry(clock=lambda: now[0])
        self.addCleanup(live.drill.history.close)
        for i in range(141):
            now[0] = 1000+i/10
            live.ingest(IDENTITY, {**SAMPLE, 'session_time': i/10, 'session_num': 0,
                'car_left_right': 4 if 20 <= i < 100 else 1}, {'session_type':'Practice'})
        view = live.spotter_status()
        self.assertEqual(view['profile'], 'ASPHALT_OVAL')
        self.assertEqual(len(view['banter_shadow']), 1)
        item = live.banter.feedback({**view['banter_shadow'][0], **CLIENT, 'session_time':14.}, now[0])
        # No learned straight budget in this synthetic short drive: must stay deferred.
        self.assertEqual(choose(item, [])['status'], 'DEFERRED')
        live._reset_live('synthetic disconnect')
        self.assertEqual(live.banter.view(now[0]), [])

    def test_long_overlap_and_both_sides_clear_are_shadow_only(self):
        for multi in [False, True]:
            shadow = proposal(multi=multi)
            item = feedback(shadow)
            self.assertEqual(item['trigger'], 'sandwich_resolved' if multi else 'overlap_resolved')
            self.assertEqual(item['candidate_session_time'], 13.)
            self.assertEqual(item['session_time'], 14.)
            self.assertEqual(choose(item, [])['status'], 'ELIGIBLE_SHADOW')
            shadow.saved([item])
            self.assertEqual(shadow.view(1014), [])
            self.assertEqual(shadow.expired(1020), [])

    def test_short_overlap_never_creates_candidate(self):
        shadow = BanterShadow()
        for i in range(140):
            shadow.ingest({**SAMPLE, 'session_time': i/10, 'car_left_right': 2 if 20 < i < 40 else 1}, 'run', 1000+i/10, True)
        self.assertFalse(shadow.view(1014))

    def test_gap_reversal_reconnect_and_nonoval_reset(self):
        for run, t, at, enabled in [('run', 15., 1015., True), ('run', 1., 1014.1, True),
                                    ('new', 14.1, 1014.1, True), ('run', 14.1, 1014.1, False)]:
            shadow = proposal()
            shadow.ingest({**SAMPLE, 'session_time': t}, run, at, enabled)
            self.assertEqual(shadow.view(at), [])

    def test_invalid_state_then_recovery_does_not_revive_old_candidate(self):
        for changes in [{'on_pit_road': True}, {'on_track': False}, {'is_replay': True},
                        {'car_left_right': None}, {'car_left_right': 2}]:
            shadow = proposal()
            candidate = shadow.view(1014.)[0]
            shadow.ingest({**SAMPLE, 'session_time': 14.1, **changes}, 'run', 1014.1, True)
            shadow.ingest({**SAMPLE, 'session_time': 14.2}, 'run', 1014.2, True)
            result = shadow.feedback({**candidate, **CLIENT, 'session_time': 14.2}, 1014.2)
            self.assertIn('CONDITION_CHANGED', result['reasons'])

    def test_busy_stale_phase_change_and_expiry(self):
        for changes in [{'audio_busy': True}, {'engineer_busy': True}, {'pending_audio': True}, {'muted': True}, {'session_time': 13.}]:
            shadow = proposal()
            item = shadow.feedback({**shadow.view(1014.)[0], **CLIENT, 'session_time': 14., **changes}, 1014.)
            self.assertEqual(choose(item, [])['status'], 'DEFERRED')
        shadow = proposal()
        shadow.ingest({**SAMPLE, 'session_time': 14.1, 'session_flags': 8}, 'run', 1014.1, True)
        self.assertIn('PHASE_NOT_STABLE', feedback(shadow)['reasons'])
        shadow = proposal()
        first = shadow.expired(1020.)
        self.assertEqual(len(first), 1)
        self.assertIn('NO_FRONTEND_OBSERVATION', first[0]['reasons'])
        self.assertEqual(first, shadow.expired(1022.))  # Not acknowledged until persistence succeeds.
        shadow.saved(first)
        self.assertEqual(shadow.expired(1023.), [])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'notes.sqlite3'
        self.store = VoiceNoteStore(self.path)

    def records(self):
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT record_json FROM banter_reviews ORDER BY id')]

    def test_atomic_dedup_restart_history_and_actual_radio_is_untouched(self):
        item = record()
        failures = []
        def write():
            try:
                self.store.record_banter([item])
            except Exception as exc:
                failures.append(str(exc))
        threads = [threading.Thread(target=write) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(self.records()), 1)
        self.store = VoiceNoteStore(self.path)
        self.store.record_banter([record(at=1600, key='two')])
        self.assertNotEqual(self.records()[0]['selected']['motif'], self.records()[1]['selected']['motif'])
        self.assertEqual(self.store.radio_log(), [])

    def test_bounded_reviews_keep_virtual_history_even_with_many_deferrals(self):
        self.store.record_banter([record()])
        blocked = [{**record(at=1000+i, key=str(i+1)), 'reasons': ['MUTED']} for i in range(2050)]
        self.store.record_banter(blocked)
        self.assertEqual(len(self.records()), 2048)
        self.store.record_banter([record(at=1500, key='later')])
        self.assertEqual(self.records()[-1]['reasons'], ['SHADOW_COOLDOWN'])

    def test_export_caps_and_size_omission_metadata_match_real_files(self):
        self.store.record_session_health('run', {'game': 'iracing'}, {'at': 1000., 'state': 'CONNECTED'})
        self.store.record_banter([{**record(at=1000+i, key=str(i)), 'reasons': ['MUTED']} for i in range(150)])
        _, data = session_log.export(self.store, 'run', '2.4.2')
        manifest = session_log.read(data)[0]
        content = session_log.read_extras(data)['banter_shadow']
        self.assertEqual(len(content), 128)
        self.assertEqual(content[8]['candidate_id'], '30')
        self.assertEqual(manifest['banter_shadow']['omitted_observations'], 22)
        _, reduced = session_log.export(self.store, 'run', '2.4.2', limit=len(data)-200)
        small = session_log.read(reduced)[0]
        self.assertEqual(small['banter_shadow']['included_observations'], 0)
        self.assertNotIn('banter-shadow.json', zipfile.ZipFile(io.BytesIO(reduced)).namelist())
        self.assertFalse(small['banter_shadow']['actual_playback'])

    def test_api_ignores_old_runs_strict_bools_and_save_failure_retries(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import voice_coach
        shadow = proposal()
        live = SimpleNamespace(lock=threading.RLock(), run_id='run', identity={}, clock=lambda:1014.,
                               banter=shadow, samples=[], state='CONNECTED', pit_wall=SimpleNamespace(race={}))
        app = FastAPI()
        with patch.object(voice_coach, 'SpotterAudio'):
            voice_coach.register_voice_routes(app, SimpleNamespace(live=live, store=self.store, speech=None), None, None, None, None)
        client = TestClient(app)
        item = {**shadow.view(1014)[0], **CLIENT, 'session_time': 14.}
        self.assertEqual(client.post('/api/live-coach/radio-delivery', json={'events': [], 'banter': [{**item, 'muted': 0}]}).status_code, 422)
        self.assertEqual(client.post('/api/live-coach/radio-delivery', json={'events': [], 'banter': [{**item, 'run_id': 'old'}]}).status_code, 200)
        self.assertEqual(self.records(), [])
        with patch.object(self.store, 'record_banter', side_effect=OSError('synthetic')):
            self.assertEqual(client.post('/api/live-coach/radio-delivery', json={'events': [], 'banter': [item]}).status_code, 200)
        self.assertEqual(len(shadow.expired(1014.)), 1)
        self.assertEqual(client.post('/api/live-coach/radio-delivery', json={'events': [], 'banter': [item]}).status_code, 200)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.store.radio_log(), [])
        self.assertEqual(shadow.expired(1020), [])

    def test_health_retries_failed_shadow_storage_outside_live_lock(self):
        from session_health import SessionHealthRecorder
        shadow = proposal()
        live = SimpleNamespace(lock=threading.Lock(), run_id='run', identity={},
            banter=shadow, samples=[{**SAMPLE, 'session_time': 14., 'wall_time': 1014.}],
            state='CONNECTED', radio=SimpleNamespace(metadata={}))
        recorder = SessionHealthRecorder(live, self.store, '2.4.2', clock=lambda:1020.)
        with patch.object(self.store, 'record_banter', side_effect=OSError('synthetic')):
            recorder.run_once()
        self.assertTrue(shadow.expired(1021.))
        original = self.store.record_banter
        def write(items):
            self.assertTrue(live.lock.acquire(blocking=False))
            live.lock.release()
            return original(items)
        with patch.object(self.store, 'record_banter', side_effect=write):
            recorder.run_once()
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(shadow.expired(1021.), [])


if __name__ == '__main__':
    unittest.main()
