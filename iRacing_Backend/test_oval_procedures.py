"""Synthetic procedure boundaries, durable shadow data and conservative evidence."""
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from oval_procedures import OvalProcedures, phase
from banter_shadow import BanterShadow
from test_banter_shadow import SAMPLE, CLIENT
from session_health import SessionHealthRecorder
from voice_notes import VoiceNoteStore
import session_log


BASE = {**SAMPLE, 'session_time':0., 'player_pace_flags':0, 'pits_open':False,
        'player_pace_line':0, 'player_pace_row':0}


def tick(shadow, t, **changes):
    shadow.ingest({**BASE,'session_time':t, **changes}, 'run', 1000+t, True, 'Race')


class ProcedureTests(unittest.TestCase):
    def test_nonfinite_channels_are_missing_and_do_not_poison_storage(self):
        s=OvalProcedures()
        tick(s,0,speed_mps=float('nan'),session_laps_remaining=float('inf'))
        item=s.pending()[0]
        self.assertIsNone(item['sample']['speed_mps'])
        self.assertIsNone(item['sample']['session_laps_remaining'])
        self.assertIs(item['sample']['pits_open'],False)
        json.dumps(item,allow_nan=False)

    def test_live_wiring_keeps_pending_diagnostics_after_disconnect(self):
        from live_telemetry import LiveTelemetry
        from test_oval_spotter import IDENTITY
        now=[1000.]
        live=LiveTelemetry(clock=lambda:now[0])
        self.addCleanup(live.drill.history.close)
        for t,opened in [(0.,False),(.1,True)]:
            now[0]=1000+t
            live.ingest(IDENTITY,{**BASE,'session_num':0,'session_time':t,'pits_open':opened},{'session_type':'Race'})
        self.assertEqual(len(live.spotter_status()['oval_shadow']),1)
        count=len(live.oval_procedures.pending())
        live._reset_live('synthetic disconnect')
        self.assertEqual(live.oval_procedures.view(now[0]),[])
        self.assertEqual(len(live.oval_procedures.pending()),count)

    def test_api_observes_only_current_run_and_does_not_write_actual_radio(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import voice_coach
        with tempfile.TemporaryDirectory() as folder:
            store=VoiceNoteStore(Path(folder)/'notes.db')
            s=OvalProcedures();tick(s,0);tick(s,.1,pits_open=True)
            live=SimpleNamespace(lock=threading.RLock(),run_id='run',identity={},clock=lambda:1000.1,
                oval_procedures=s,samples=[],state='CONNECTED',pit_wall=SimpleNamespace(race={}))
            app=FastAPI()
            with patch.object(voice_coach,'SpotterAudio'):
                voice_coach.register_voice_routes(app,SimpleNamespace(live=live,store=store,speech=None),None,None,None,None)
            with TestClient(app) as client:
                item={**s.view(1000.1)[0],**CLIENT}
                url='/api/live-coach/radio-delivery'
                self.assertEqual(client.post(url,json={'events':[],'oval':[{**item,'muted':0}]}).status_code,422)
                self.assertEqual(client.post(url,json={'events':[],'oval':[{**item,'run_id':'old'}]}).status_code,200)
                self.assertEqual(client.post(url,json={'events':[],'oval':[item]}).status_code,200)
            self.assertEqual(sum(r['kind']=='client_observation' for r in s.pending()),1)
            self.assertEqual(store.radio_log(),[])

    def test_oval_voice_bindings_include_all_fixed_optional_texts_without_banter(self):
        from voice_catalog_diff import current_bindings
        from oval_spotter import OPTIONAL_CLIPS
        from banter_catalog import CATALOG
        bindings=set(current_bindings())
        self.assertEqual(len(OPTIONAL_CLIPS),14)
        self.assertTrue(all(('spotter',t) in bindings for t in OPTIONAL_CLIPS.values()))
        self.assertTrue(all(('spotter',p['text']) not in bindings for p in CATALOG))

    def test_sdk_modes_and_overlapping_caution_are_distinct(self):
        self.assertEqual(phase({**BASE,'pace_mode':0,'session_state':3}, 'Race'),'INITIAL_PACING')
        self.assertEqual(phase({**BASE,'pace_mode':3,'session_flags':0x4000}, 'Race'),'RESTART_PACING')
        self.assertEqual(phase(BASE,'Practice'),'NON_RACE')
        self.assertEqual(phase({**BASE,'pace_mode':None}, 'Race'),'UNKNOWN')

    def test_restart_cycle_resolves_and_does_not_cancel_normal_green(self):
        s=OvalProcedures()
        tick(s,0)
        tick(s,.1,session_flags=0x4000)
        tick(s,.2,session_flags=0x4200,pace_mode=3)
        tick(s,.3,session_flags=4)
        kinds=[r['kind'] for r in s.pending()]
        for kind in ('caution','one_lap_to_green','restart_resolved','restart_green'):
            self.assertIn(kind,kinds)
        self.assertNotIn('restart_cancelled',kinds)
        self.assertTrue(all(r['actual_playback'] is False for r in s.pending()))

    def test_mid_caution_join_is_restart_evidence_not_first_start(self):
        s=OvalProcedures()
        tick(s,0,session_flags=0x4000,pace_mode=3)
        tick(s,.1,session_flags=4)
        kinds=[r['kind'] for r in s.pending()]
        self.assertIn('restart_green',kinds)
        self.assertNotIn('initial_green',kinds)
        self.assertNotIn('caution',kinds)

    def test_one_lap_withdrawal_boundary_and_reset_preserve_unsaved_events(self):
        for action in ('withdraw','gap','reset'):
            s=OvalProcedures()
            tick(s,0,session_flags=0x4000)
            tick(s,.1,session_flags=0x4200)
            if action=='reset':s.reset()
            else:tick(s,1. if action=='gap' else .2,session_flags=0x4000)
            self.assertIn('restart_cancelled',[r['kind'] for r in s.pending()])
            if action=='reset':self.assertEqual(s.view(1000.2),[])

    def test_assignments_preserve_false_and_require_game_permission(self):
        s=OvalProcedures()
        tick(s,0,pits_open=None)
        tick(s,.1,pits_open=False,player_pace_flags=7,player_pace_line=1,player_pace_row=2)
        items={r['kind']:r for r in s.pending()}
        self.assertIs(items['pit_permission']['sample']['pits_open'],False)
        self.assertEqual(items['pit_permission']['exit_permission'],'UNKNOWN')
        for kind in ('free_pass','wave_around','end_of_line'):
            self.assertEqual(items[kind]['pass_permission'],'UNKNOWN')
            self.assertIn('FEATURE_DISABLED',items[kind]['reasons'])

    def test_client_busy_stale_wrong_run_and_expiry(self):
        s=OvalProcedures()
        tick(s,0)
        tick(s,.1,pits_open=True)
        p=s.view(1000.1)[0]
        s.feedback({**p,**CLIENT,'run_id':'old'},1000.1)
        self.assertNotIn('client_observation',[r['kind'] for r in s.pending()])
        s.feedback({**p,**CLIENT,'audio_busy':True},1000.1)
        r=s.pending()[-1]
        self.assertIn('AUDIO_BUSY',r['reasons'])
        s.feedback({**p,**CLIENT},1000.1)
        self.assertEqual(s.pending()[-1],r)

    def test_queue_bound_drop_count_and_storage_ack(self):
        s=OvalProcedures()
        for i in range(500):tick(s,i/10,pits_open=bool(i%2))
        self.assertEqual(len(s.pending()),s.MAX_PENDING)
        self.assertGreater(s.dropped,0)
        saved=s.pending()[:10]
        s.saved(saved)
        self.assertFalse({r['record_id'] for r in saved}&{r['record_id'] for r in s.pending()})

    def test_health_flush_after_disconnect_retries_outside_sdk_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            store=VoiceNoteStore(Path(folder)/'notes.db')
            s=OvalProcedures();tick(s,0);tick(s,.1,pits_open=True);s.reset()
            live=SimpleNamespace(lock=threading.Lock(),run_id=None,samples=[],oval_procedures=s)
            recorder=SessionHealthRecorder(live,store,'2.4.2',clock=lambda:1001.)
            with patch.object(store,'record_oval_procedures',side_effect=OSError('synthetic')):
                recorder.run_once()
            self.assertTrue(s.pending())
            original=store.record_oval_procedures
            def write(records):
                self.assertTrue(live.lock.acquire(False));live.lock.release()
                original(records);original(records)
            with patch.object(store,'record_oval_procedures',side_effect=write):recorder.run_once()
            self.assertEqual(s.pending(),[])
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM oval_procedure_reviews').fetchone()[0],3)
            self.assertEqual(store.radio_log(),[])

    def test_export_separates_shadow_and_reports_omission(self):
        with tempfile.TemporaryDirectory() as folder:
            store=VoiceNoteStore(Path(folder)/'notes.db')
            store.record_session_health('run',{'game':'iracing'}, {'at':1000.,'state':'CONNECTED'})
            s=OvalProcedures();tick(s,0);tick(s,.1,pits_open=True)
            store.record_oval_procedures(s.pending())
            _,data=session_log.export(store,'run','2.4.2')
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                self.assertIn('oval-procedures.json',z.namelist())
                m=json.loads(z.read('manifest.json'))
                self.assertEqual(m['radio_events'],0)
                self.assertIs(m['oval_procedures']['actual_playback'],False)
            # exported_at changes compression bytes between exports; use a material margin.
            _,limited=session_log.export(store,'run','2.4.2',limit=len(data)-200)
            with zipfile.ZipFile(io.BytesIO(limited)) as z:
                m=json.loads(z.read('manifest.json'))
                self.assertGreater(m['oval_procedures']['omitted_observations'],0)


class AdditionalBanterTests(unittest.TestCase):
    def test_unknown_speed_defers_without_unserializable_nan(self):
        from test_banter_shadow import proposal, CLIENT
        s=proposal()
        s.ingest({**SAMPLE,'session_time':14.1,'speed_mps':float('nan')},'run',1014.1,True)
        p=s.view(1014.1)[0]
        record=s.feedback({**p,**CLIENT,'session_time':14.1},1014.1)
        self.assertIn('WORKLOAD_OR_SPEED',record['reasons'])
        json.dumps(record,allow_nan=False)

    def test_six_additional_triggers_keep_observation_limits(self):
        s=BanterShadow()
        for i in range(1241):
            t=i/10
            sample={**SAMPLE,'session_time':t,'incidents':0,'lap_completed':i//300,
                'lap_best_s':40. if i<300 else 39.,'lap_last_s':39.,'class_position':5 if i<200 else 4,
                'pit_service_status':1 if i==1220 else 2 if i==1221 else 0,
                'session_flags':1 if i>=1230 else 0}
            s.ingest(sample,'run',1000+t,True)
        triggers={p['trigger']:p for p in s.proposals}
        for name in ('personal_best','position_gain','clean_stint','quiet_running','pit_service_done','race_finish'):
            self.assertIn(name,triggers)
        self.assertIn('PLAYER_FINISH_UNCONFIRMED',triggers['race_finish']['trigger_reasons'])
        self.assertIn('OVERTAKE_UNCONFIRMED',triggers['position_gain']['trigger_reasons'])

    def test_missing_counters_and_clock_boundary_never_create_pb_or_clean_stint(self):
        s=BanterShadow()
        for i in range(50):s.ingest({**SAMPLE,'session_time':i/10},'run',1000+i/10,True)
        self.assertEqual(list(s.proposals),[])
        s.ingest({**SAMPLE,'session_time':0.,'lap_completed':50,'lap_best_s':10.},'new',1010.,True)
        self.assertEqual(list(s.proposals),[])
