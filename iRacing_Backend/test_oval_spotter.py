"""Synthetic oval profile, overlap, cache-only audio and exported diagnostic contracts."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import log_pack
from live_telemetry import LiveTelemetry, session_identity
from oval_spotter import (ASPHALT_TYPES, OPTIONAL_CLIPS, OVAL_FIELDS, audio_key, classify,
                          read_pacing, track_context)
from radio_audio import spoken_form
from session_health import SessionHealthRecorder
import session_log
from session_log_extras import nearby_cars
from spotter import Spotter, SpotterAudio, VARIANTS
from test_traffic_log import fixture
from traffic_log import TrafficLog, read, traffic_snapshot
from voice_notes import VoiceNoteStore

IDENTITY = {'game': 'iracing', 'track_id': '123', 'track_path': 'test oval', 'track_name': 'Test Oval',
            'track_config': 'Oval', 'track_type': 'short oval', 'track_direction': 'left'}


class ProfileTests(unittest.TestCase):
    def test_only_explicit_sdk_track_types_enable_asphalt_oval(self):
        for kind in ASPHALT_TYPES:
            self.assertEqual(classify({**IDENTITY, 'track_type': kind.upper()})[0], 'ASPHALT_OVAL')
        for kind in ('', 'oval', 'speedway-ish', 'not oval', 'roval', None):
            self.assertEqual(classify({**IDENTITY, 'track_type': kind})[0], 'UNKNOWN')

    def test_car_or_sporting_category_never_enables_profile(self):
        self.assertEqual(classify({**IDENTITY, 'track_type': '', 'car_name': 'NASCAR', 'track_category': 'Oval'})[0], 'UNKNOWN')
        self.assertEqual(classify({**IDENTITY, 'track_type': 'road course', 'car_name': 'NASCAR'})[0], 'ROAD')
        self.assertEqual(classify({**IDENTITY, 'car_name': 'GT3'})[0], 'ASPHALT_OVAL')

    def test_road_layout_dirt_skidpad_other_game_and_missing_identity_fall_back(self):
        for changed, expected in [({'track_config': 'Road Course'}, 'ROAD'), ({'track_config': 'Roval'}, 'ROAD'),
                ({'track_type': 'dirt oval'}, 'DIRT_OVAL'), ({'track_category': 'DirtOval'}, 'DIRT_OVAL'),
                ({'track_path': 'centripetal'}, 'UNKNOWN'), ({'game': 'lmu'}, 'UNKNOWN'),
                ({'track_id': ''}, 'UNKNOWN'), ({'track_id': '-1'}, 'UNKNOWN'),
                ({'track_id': True}, 'UNKNOWN'), ({'track_path': ''}, 'UNKNOWN')]:
            with self.subTest(changed=changed):
                self.assertEqual(classify({**IDENTITY, **changed})[0], expected)

    def test_live_metadata_does_not_change_recorded_file_identity(self):
        weekend = {'TrackType': 'short oval', 'TrackDirection': 'right', 'Category': 'Oval',
                   'WeekendOptions': {'CourseCautions': 'full', 'Restarts': 'double file', 'StandingStart': 0}}
        self.assertNotIn('track_type', session_identity(weekend, {}))
        result = track_context(weekend, 'iracing')
        self.assertEqual((result['track_type'], result['track_direction'], result['standing_start']), ('short oval', 'right', '0'))
        self.assertEqual(track_context(weekend, 'lmu'), {})

    def test_frozen_sdk_tick_collects_metadata_and_only_the_players_pacing(self):
        from test_live_drive_lifecycle import FakeSdk
        sdk = FakeSdk()
        sdk.values.update(WeekendInfo={'TrackID': 123, 'TrackName': 'test oval', 'TrackType': 'short oval',
                          'TrackDirection': 'right', 'WeekendOptions': {'CourseCautions': 'full'}},
                          IsReplayPlaying=False, OnPitRoad=False, PlayerCarIdx=1, PaceMode=3, PitsOpen=True,
                          CarIdxPaceLine=[9, 0], CarIdxPaceRow=[9, 2], CarIdxPaceFlags=[9, 4])
        live = LiveTelemetry(clock=lambda: 100.)
        self.addCleanup(live.drill.history.close)
        with patch('live_telemetry.read_race', return_value=None):
            self.assertTrue(live._tick(sdk))
        self.assertEqual(live.spotter.profile, 'ASPHALT_OVAL')
        self.assertEqual(live.identity['track_direction'], 'right')
        self.assertEqual(live.samples[-1]['player_pace_line'], 0)
        self.assertEqual(live.samples[-1]['player_pace_flags'], 4)
        self.assertEqual(sdk.freeze_count, sdk.unfreeze_count)

    def test_player_assignments_preserve_zero_false_missing_and_invalid(self):
        sdk = {'PaceMode': 0, 'PitsOpen': False, 'CarIdxPaceLine': [0, 1],
               'CarIdxPaceRow': [1, -1], 'CarIdxPaceFlags': [2, 0]}
        result = read_pacing(sdk, set(sdk), 0)
        self.assertEqual(result, {'pace_mode': 0, 'pits_open': False, 'player_pace_line': 0,
                                 'player_pace_row': 1, 'player_pace_flags': 2})
        self.assertIsNone(read_pacing(sdk, set(sdk), 1)['player_pace_row'])
        for idx in (-1, 64, True, None, 2):
            self.assertIsNone(read_pacing(sdk, set(sdk), idx)['player_pace_flags'])
        self.assertTrue(all(v is None for v in read_pacing({}, set(), 0).values()))
        for mode in (-1, 5, True, float('nan'), '0'):
            self.assertIsNone(read_pacing({'PaceMode': mode}, {'PaceMode'}, 0)['pace_mode'])
        self.assertIsNone(read_pacing({'PitsOpen': 2}, {'PitsOpen'}, 0)['pits_open'])


class OverlapTests(unittest.TestCase):
    def setUp(self):
        self.s = Spotter(); self.s.configure_track(IDENTITY); self.now = 1000.

    def tick(self, raw, duration=.1, **values):
        self.now += duration
        self.s.ingest({'session_time': self.now - 1000, 'car_left_right': raw,
            'on_track': True, 'is_replay': False, 'on_pit_road': False, **values}, 'run', self.now)
        return self.s.view(self.now)

    def stable(self, raw, **values):
        self.tick(raw, **values)
        return self.tick(raw, .4, **values)

    def test_own_pass_and_fast_car_still_warn_only_after_existing_confirmation(self):
        self.stable(1)
        self.tick(2, side_relative_mps=-8., side_closing_mps=8.)
        self.tick(2, .2, side_relative_mps=-8., side_closing_mps=8.)
        self.assertFalse(self.s.events)
        view = self.tick(2, .2, side_relative_mps=-8., side_closing_mps=8.)
        self.assertEqual(view['events'][0]['audio_key'], 'oval_inside')
        self.assertEqual(view['state_label'], '포장 오벌 · 안쪽 차량.')

    def test_short_overlap_does_not_create_a_clear(self):
        self.stable(1); self.tick(2); self.tick(1, .1); self.tick(1, .2)
        self.assertEqual(list(self.s.events), [])

    def test_sustained_overlap_repeats_at_selected_interval(self):
        self.s.repeat_seconds = 2.
        self.stable(2)
        for _ in range(60): self.tick(2)
        keys = [e['key'] for e in self.s.events]
        self.assertEqual(keys, ['left', 'hold_left', 'hold_left', 'hold_left'])
        self.assertEqual(self.stable(1)['events'][0]['audio_key'], 'oval_clear_inside')

    def test_three_wide_and_remaining_side_never_say_clear_all(self):
        self.stable(4); self.stable(2); self.stable(5); self.stable(2); self.stable(1)
        self.assertEqual([e['key'] for e in self.s.events], ['both', 'left_only', 'two_left', 'left_one', 'clear_left'])
        self.assertEqual([e.get('audio_key') for e in self.s.events],
                         [None, 'oval_inside_only', 'oval_two_inside', 'oval_inside_one', 'oval_clear_inside'])

    def test_reversed_direction_and_unknown_direction(self):
        self.s.configure_track({**IDENTITY, 'track_direction': 'right'})
        self.assertEqual(self.stable(2)['current_audio_key'], 'oval_outside')
        self.s.configure_track({**IDENTITY, 'track_direction': 'neutral'})
        view = self.stable(2)
        self.assertIsNone(view['current_audio_key']); self.assertEqual(view['state_label'], '포장 오벌 · 왼쪽 차량.')
        self.assertIsNone(audio_key(None, 'left'))

    def test_gap_replay_pit_off_and_profile_change_do_not_manufacture_clear(self):
        for values in ({'is_replay': True}, {'on_pit_road': True}, {'on_track': False}):
            self.s.reset(); self.stable(2); self.tick(2, **values); self.stable(1)
            self.assertEqual(list(self.s.events), [])
        self.stable(2); self.tick(1, .7); self.stable(1)
        self.assertFalse(self.s.events)
        self.stable(2); self.s.configure_track({**IDENTITY, 'track_type': 'road course'}); self.stable(1)
        self.assertFalse(self.s.events)

    def test_live_race_parade_uses_oval_policy_and_switch_resets(self):
        live = LiveTelemetry(clock=lambda: self.now)
        for n in range(8):
            self.now += .1
            live.ingest(IDENTITY, {'session_time': n * .1, 'session_num': 0, 'car_left_right': 2,
                'on_track': True, 'is_replay': False, 'on_pit_road': False, 'session_state': 3}, {'session_type': 'Race'})
        self.assertEqual(live.spotter.profile, 'ASPHALT_OVAL')
        self.assertEqual(live.spotter.events[0]['key'], 'left')
        old = live.run_id
        live.ingest({**IDENTITY, 'track_type': 'road course'}, {'session_time': 1., 'session_num': 0,
            'car_left_right': 1, 'on_track': True, 'is_replay': False, 'on_pit_road': False}, {'session_type': 'Practice'})
        self.assertNotEqual(old, live.run_id); self.assertFalse(live.spotter.events)


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.log = TrafficLog(self.folder / 'traffic'); self.addCleanup(self.log.close)
        self.sdk, self.sample, _ = fixture()

    def record(self, kind='Practice', **values):
        self.sample.update(values)
        snapshot = traffic_snapshot(self.sdk, set(self.sdk), self.sample, IDENTITY)
        snapshot['session_type'] = kind
        self.log.record(snapshot, 'oval-run', 10.)

    def test_practice_raw_values_and_tail_are_lossless_in_existing_export(self):
        self.record(pace_mode=0, pits_open=False, player_pace_flags=2, player_pace_row=0, car_left_right=4)
        self.assertTrue(self.log.flush_wait())
        header, rows = read(self.log.path)
        self.assertEqual(header['version'], 3)
        self.assertEqual(rows[0]['oval']['pits_open'], 0)
        self.assertIsNone(rows[0]['oval']['player_pace_line'])
        run = {'run_id': 'oval-run', 'session_type': 'Practice', 'started_at': time.time(), 'identity': IDENTITY}
        extra = nearby_cars(self.log.folder, None, prepare=lambda _: self.log.flush_wait())(run)
        packed = log_pack.unpack(extra.files['traffic.packed.xz'])
        original = log_pack.read_lines(self.log.path)
        self.assertEqual(packed, original)
        self.assertEqual(extra.info['traffic']['rows'], 1)
        self.assertNotIn('Private Driver', json.dumps(packed))

    def test_qualifying_and_replay_not_added_to_traffic(self):
        self.record(kind='Lone Qualify')
        self.record(is_replay=True)
        self.log.close()
        self.assertIsNone(self.log.path)

    def test_pending_export_is_explicit_and_never_reads_a_partial_gzip(self):
        provider = nearby_cars(self.log.folder, None, prepare=lambda _: False)
        run = {'run_id': 'oval-run', 'session_type': 'Race', 'started_at': time.time(), 'identity': IDENTITY}
        self.assertEqual(provider(run).info['reason'], 'write_pending')

    def test_export_wait_is_bounded_and_sdk_record_stays_nonblocking(self):
        entered, release = threading.Event(), threading.Event()
        original = self.log._write_rows
        def slow(run, rows):
            entered.set(); release.wait(2.); original(run, rows)
        with patch.object(self.log, '_write_rows', side_effect=slow):
            self.record(); self.log.flush(); self.assertTrue(entered.wait(1.))
            self.assertFalse(self.log.flush_wait(.02))
            release.set(); self.assertTrue(self.log.flush_wait())

    def test_silent_run_exports_classification_and_practice_traffic_together(self):
        store = VoiceNoteStore(str(self.folder / 'voice.sqlite3'))
        live = SimpleNamespace(lock=threading.RLock(), run_id='oval-run', state='CONNECTED', identity=IDENTITY,
            samples=[{'wall_time': time.time(), 'session_time': 10., 'on_track': True, 'is_replay': False}],
            radio=SimpleNamespace(metadata={'session_type': 'Practice'}))
        SessionHealthRecorder(live, store, '2.4.1').run_once()
        self.record()
        _, data = session_log.export(store, 'oval-run', '2.4.1',
            extras=[nearby_cars(self.log.folder, None, prepare=lambda _: self.log.flush_wait())])
        manifest, radio, _ = session_log.read(data)
        self.assertEqual(manifest['spotter']['profile'], 'ASPHALT_OVAL'); self.assertEqual(radio, [])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIn('traffic.packed.xz', archive.namelist())
            health = json.loads(archive.read('session-health.json'))
            self.assertEqual(health['identity']['track_direction'], 'left')


class OptionalAudioTests(unittest.TestCase):
    def test_optional_cache_is_used_without_synthesizing_new_phrases(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'IRACING_SPOTTER_AUDIO_DIR': folder}):
            speech = Mock(); speech.synthesize.return_value = base64.b64encode(b'A' * 101).decode()
            audio = SpotterAudio(speech, lambda: 'synthetic-voice')
            text = OPTIONAL_CLIPS['oval_inside']
            digest = hashlib.sha256(json.dumps(['synthetic-voice', spoken_form(text)], ensure_ascii=False).encode()).hexdigest()
            (Path(folder) / (digest + '.mp3')).write_bytes(b'B' * 101)
            manifest = audio.prepare()
            self.assertEqual(manifest['clips']['oval_inside']['text'], text)
            self.assertEqual(speech.synthesize.call_count, sum(map(len, VARIANTS.values())))
            self.assertNotIn('oval_outside', manifest['clips'])
            self.assertTrue(all(call.args[0] not in OPTIONAL_CLIPS.values() for call in speech.synthesize.call_args_list))
