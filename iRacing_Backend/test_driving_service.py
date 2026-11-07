import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from driving_service import DrivingLearningService, public_view
from driving_observations import scope, windows
from driving_learning import learn
from driving_phrases import VARIANTS
from test_driving_learning import passes
from test_driving_observations import frames, CORNERS, IDENTITY
from live_telemetry import LiveTelemetry


class DrivingServiceTests(unittest.TestCase):
    def test_approved_voice_catalog_requires_an_installed_selected_recording(self):
        from radio_audio import fixed_catalog
        from voice_coach import VoiceCoach
        from voice_notes import VoiceNoteStore
        live=LiveTelemetry()
        self.assertFalse(live.driving_audio_ready(VARIANTS['release_earlier'][0]))
        for choices in VARIANTS.values():
            for text in choices:self.assertIn(('coach',text),fixed_catalog())
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',IRACING_RADIO_AUDIO_DIR=''):
            speech=Mock()
            coach=VoiceCoach(live,VoiceNoteStore(Path(folder)/'notes.db'),speech=speech)
            text=VARIANTS['release_earlier'][0]
            self.assertFalse(live.driving_audio_ready(text))
            path=coach.fixed_audio.path('coach',text)
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'ID3'+b'x'*200)
            self.assertTrue(live.driving_audio_ready(text))
            # A selected pack missing this binding must not fall back to the default voice.
            with patch.object(coach.custom_packs,'has_selection',return_value=True),patch.object(coach.custom_packs,'has',return_value=False):
                self.assertFalse(live.driving_audio_ready(text))
            with patch.object(coach.custom_packs,'has_selection',return_value=True),patch.object(coach.custom_packs,'has',return_value=True):
                self.assertTrue(live.driving_audio_ready(text))
            speech.synthesize.assert_not_called()

    def test_changed_prepared_audio_never_falls_back_at_render(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from voice_coach import VoiceCoach,register_voice_routes
        from voice_notes import VoiceNoteStore
        with tempfile.TemporaryDirectory() as folder,patch.dict('os.environ',IRACING_RADIO_AUDIO_DIR=''):
            live=LiveTelemetry(clock=lambda:100.)
            live.radio.run_id='run';live.radio.last_sample=100.
            live.radio.latest={'on_track':True,'is_replay':False}
            live.radio.driving_valid=lambda token,now:True
            speech=Mock()
            coach=VoiceCoach(live,VoiceNoteStore(Path(folder)/'notes.db'),speech=speech)
            text=VARIANTS['release_earlier'][0]
            app=FastAPI();register_voice_routes(app,coach,None,None,None,None)
            for scenario in ('deleted','selected_pack_missing'):
                with self.subTest(scenario=scenario):
                    path=coach.fixed_audio.path('coach',text)
                    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'ID3'+b'x'*200)
                    self.assertTrue(live.driving_audio_ready(text))
                    live.radio.emit('learned_technique',100.,'observed',role='coach',cooldown=0,
                        condition=('driving_learning','token'),speech_text=text,prepared_only=True)
                    identifier=live.radio.view(100.)['events'][-1]['id']
                    if scenario=='deleted':path.unlink()
                    with TestClient(app) as client,patch.object(coach.custom_packs,'has_selection',return_value=scenario=='selected_pack_missing'),patch.object(coach.custom_packs,'has',return_value=False):
                        result=client.post(f'/api/live-coach/spotter/radio/{identifier}/render').json()
                    self.assertEqual(result,{'event':None,'missing_prepared_audio':True})
            speech.synthesize.assert_not_called()

    def prepared(self):
        service=DrivingLearningService()
        sample=dict(frames()[0],lap_number=20,distance_pct=.01,speed_mps=20.)
        service.ingest(IDENTITY,sample,'run',CORNERS,1000,100.)
        definition=service.definitions[0]
        rows=passes()
        for row in rows:
            row.update(scope=scope(IDENTITY,'LIVE'),window=definition['id'],entry_pct=.2)
        service.results={scope(IDENTITY,'LIVE'):learn(rows)}
        return service,sample

    def test_completed_windows_processed_in_background_and_persisted_once(self):
        with tempfile.TemporaryDirectory() as folder:
            service=DrivingLearningService(Path(folder)/'history.json')
            try:
                for frame in frames()[:620]:
                    service.ingest(IDENTITY,dict(frame,lap_number=1),'run',CORNERS,1000,100.)
                service.queue.join()
                self.assertEqual(service.view()['observations'],1)
                self.assertTrue(service.thread.is_alive())
                self.assertEqual(len(service.history.rows()),1)
            finally:service.close()
            self.assertFalse(service.thread.is_alive())
            self.assertFalse(list(Path(folder).glob('*.tmp')))

    def test_current_condition_and_session_cancel_queued_advice(self):
        service,sample=self.prepared()
        item=service.choose(100.,{k:v[0] for k,v in VARIANTS.items()})
        self.assertIsNotNone(item)
        self.assertTrue(service.valid(item['token'],100.))
        self.assertFalse(service.valid(item['token'],103.))
        service.current[1]['abs_setting']=7.
        self.assertFalse(service.valid(item['token'],100.))
        service.reset()
        self.assertFalse(service.valid(item['token'],100.))

    def test_no_replay_in_same_lap_and_no_public_evidence_dump(self):
        service,_=self.prepared()
        phrases={k:v[0] for k,v in VARIANTS.items()}
        self.assertIsNotNone(service.choose(100.,phrases))
        service.mark_announced()
        self.assertIsNone(service.choose(100.,phrases))
        public=service.view()['technique'][0]
        self.assertNotIn('support',public)
        self.assertNotIn('evidence_ids',public)

    def test_model_for_later_corner_never_calls_it_the_next_corner(self):
        service,_=self.prepared()
        result=service.results[scope(IDENTITY,'LIVE')]
        result['technique'][0]['members']=['C2']
        self.assertIsNone(service.choose(100.,{k:v[0] for k,v in VARIANTS.items()}))

    def test_worker_failure_is_reported_and_does_not_stop_sdk_collection(self):
        service=DrivingLearningService()
        try:
            with patch.object(service.history,'update',side_effect=OSError('synthetic')):
                with self.assertLogs('iRacingLive',level='ERROR'):
                    for frame in frames()[:620]:
                        service.ingest(IDENTITY,dict(frame,lap_number=1),'run',CORNERS,1000,100.)
                    service.queue.join()
            self.assertEqual(service.view()['failure'],'PROFILE_UNAVAILABLE')
        finally:service.close()

    def test_new_automatic_categories_cancel_pending_and_stay_prepared_only(self):
        from race_radio import RaceRadio
        radio=RaceRadio();radio.run_id='run';radio.last_sample=100.
        radio.latest={'on_track':True,'is_replay':False}
        radio.driving_valid=lambda token,now:True
        for category in ('technique','chain','impact'):
            key='learned_'+category
            radio.configure_alerts(pitwall={key:True})
            radio.emit(key,100.,'synthetic',role='coach',cooldown=0,
                condition=('driving_learning','token'),speech_text='prepared',prepared_only=True)
            event=radio.view(100.)['events'][-1]
            self.assertTrue(event['prepared_only'])
            radio.configure_alerts(pitwall={key:False})
            self.assertIsNone(radio.get(event['id'],100.))
            radio.configure_alerts(pitwall={key:True})
            self.assertIsNone(radio.get(event['id'],100.))

    def test_lmu_withholds_new_learning_without_changing_existing_live_features(self):
        service=DrivingLearningService()
        service.ingest(dict(IDENTITY,game='lmu'),dict(frames()[0],lap_number=1),'run',CORNERS,1000,100.)
        self.assertIsNone(service.current)
        self.assertIsNone(service.thread)


if __name__=='__main__':unittest.main()
