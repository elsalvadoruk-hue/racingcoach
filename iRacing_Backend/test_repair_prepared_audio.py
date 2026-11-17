"""Cold repair details never hold up an existing warning or request synthesis."""
import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import MagicMock
import test_radio_optional_parts as fixtures
from voice_coach import VoiceCoach, ReportRequest, RadioPlaybackDiagnostics, RadioDeliveryEvent
from voice_notes import VoiceNoteStore
import session_log
from pydantic import ValidationError


class RepairPreparedAudioTests(unittest.TestCase):
    def fixture(self, parts, key='repair_check'):
        f = fixtures.OptionalRadioPartsTests()
        f.setUp()
        f.event.update(key=key, engineer='race', text=' '.join(parts), speech_text=' '.join(parts), speech_parts=parts)
        f.coach.fixed_audio.read_prepared = MagicMock(side_effect=lambda role, text: 'cached:' + text if text in f.stored else (_ for _ in ()).throw(FileNotFoundError()))
        return f

    def test_long_repairs_use_prepared_warning_keep_exact_screen(self):
        for kind, seconds in (('optional_repair', 278), ('mandatory_repair', 2062)):
            with self.subTest(kind=kind):
                label = '선택' if kind == 'optional_repair' else '필수'
                sentence = f'{label} 수리는 {seconds}초입니다.'
                f = self.fixture([sentence])
                f.stored.clear()
                result = asyncio.run(f.render('run:1'))
                self.assertEqual(result['audio_fallback_key'], kind)
                self.assertEqual(result['event']['text'], sentence)
                self.assertTrue(result['prepared_detail_omitted'])
                f.synthesize.assert_not_called()
                f.coach.fixed_audio.phrase.assert_not_called()

    def test_cached_damage_parts_survive_cold_first_and_optional_parts(self):
        f = self.fixture(['cold damage', 'prepared detail', 'cold optional'], 'damage_check')
        f.stored = {'prepared detail'}
        result = asyncio.run(f.render('run:1'))
        self.assertEqual(result['audio_parts'], ['cached:prepared detail'])
        self.assertEqual(result['dropped_parts'], 2)
        f.coach.fixed_audio.phrase.assert_not_called()

    def test_prepared_exact_repair_bypasses_cloud_and_never_synthesizes(self):
        f = self.fixture(['필수 수리는 2062초입니다.', 'cached advice'])
        f.stored = set(f.event['speech_parts'])
        async def busy():
            async with f.coach.cloud_lock:
                return await asyncio.wait_for(f.render('run:1'), .2)
        result = asyncio.run(busy())
        self.assertEqual(result['audio_parts'], ['cached:' + p for p in f.event['speech_parts']])
        f.coach.fixed_audio.phrase.assert_not_called()

    def test_missing_warning_and_all_damage_never_synthesize(self):
        for key in ('damage_check',):
            f = self.fixture(['필수 수리는 2062초입니다.'], key)
            f.stored.clear()
            result = asyncio.run(f.render('run:1'))
            self.assertTrue(result['missing_prepared_audio'])
            f.coach.fixed_audio.phrase.assert_not_called()
            f.synthesize.assert_not_called()

    def test_cache_deleted_after_check_does_not_synthesize(self):
        f = self.fixture(['필수 수리는 2062초입니다.'])
        f.stored = set(f.event['speech_parts'])
        f.coach.fixed_audio.read_prepared.side_effect = FileNotFoundError()
        self.assertEqual(asyncio.run(f.render('run:1'))['audio_fallback_key'], 'mandatory_repair')
        f.coach.fixed_audio.phrase.assert_not_called()


class DeferredReportTests(unittest.TestCase):
    def test_deferred_decision_keeps_other_urgent_reports_and_does_not_ack(self):
        coach = VoiceCoach.__new__(VoiceCoach)
        old = dict(key='last_lap', token='finish', text='보고')
        urgent = dict(key='fuel_critical', token='empty', text='급유 필요')
        coach.prepared_reports = [dict(run_id='run', decision=old, response={'note': {'id': 'note'}})]
        request = ReportRequest(deferred_report_ids=['note'])
        self.assertEqual(coach.available_reports([old, urgent], request, 'run'), [urgent])
        self.assertEqual(coach.available_reports([old], request, 'new-run'), [old])
        self.assertEqual(len(coach.prepared_reports), 1)
        self.assertEqual(coach.available_reports([old], ReportRequest(), 'run'), [old])

    def test_deferred_list_and_diagnostic_accept_only_bounded_public_fields(self):
        with self.assertRaises(ValidationError):
            ReportRequest(deferred_report_ids=['n'] * 33)
        with self.assertRaises(ValidationError):
            ReportRequest(deferred_report_ids=['private/device/path'])
        fields = dict(attempt_id='a:1', owner_generation='a:0', game='lmu', created_at=1,
                      stage='play', elapsed_ms=0)
        for result in ('pending', 'decoded', 'source_failed', 'both_failed', 'timeout', 'unavailable', 'oversize'):
            self.assertEqual(RadioPlaybackDiagnostics(**fields, decode_probe=result).model_dump()['decode_probe'], result)
        with self.assertRaises(ValidationError):
            RadioPlaybackDiagnostics(**fields, decode_probe='private error text')
        playback = {**fields, 'decode_probe': 'decoded', 'decode_probe_attempt_id': 'a:0'}
        event = RadioDeliveryEvent(event_id='run:1', run_id='run', key='repair_check', status='failed', playback=playback).model_dump()
        with tempfile.TemporaryDirectory() as folder:
            store = VoiceNoteStore(Path(folder) / 'voice.sqlite3')
            store.record_radio([event])
            exported = session_log.export(store, 'run', '2.5.6')
            with zipfile.ZipFile(io.BytesIO(exported[1])) as archive:
                recorded = json.loads(archive.read('radio.jsonl'))['playback']
                self.assertEqual(recorded['decode_probe'], 'decoded')
                self.assertEqual(recorded['decode_probe_attempt_id'], 'a:0')
