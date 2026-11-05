from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import session_log
from tire_reader.history import TireHistoryStore, TireHistoryWriter
from tire_reader.snapshot import from_iracing
from test_tire_advisor import snapshot


class TireLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tires-history.sqlite3'
        self.run = dict(run_id='selected-run-123', game='iracing', started_at=1., ended_at=100., identity={})

    def provider(self):
        from tire_reader.log_export import tire_data
        return tire_data(self.path)

    def unpack(self, extra):
        import lzma
        return json.loads(lzma.decompress(extra.files['tires.json.xz']))

    def test_full_export_has_only_explicitly_linked_run_and_keeps_missing_samples(self):
        with closing(TireHistoryStore(self.path)) as store:
            for run, t in [('previous-run', 0.), (self.run['run_id'], 1.), ('next-run', 2.)]:
                store.append(snapshot(t), run_id=run, diagnostics={'reader_status': 'reading'})
            missing = replace(snapshot(3.), wheels={w:replace(v,pressure_kpa=None,temp_c=None) for w,v in snapshot().wheels.items()})
            store.append(missing, run_id=self.run['run_id'], diagnostics={'reader_status': 'not_found', 'visible': True})
        extra = self.provider()(self.run)
        doc = self.unpack(extra)
        self.assertEqual([r['data']['t'] for r in doc['observations']], [1.])
        self.assertEqual([r['data']['reader_status'] for r in doc['diagnostics']], ['reading', 'not_found'])
        self.assertEqual(extra.info['observations'], 1)
        self.assertNotIn('session_key', json.dumps(doc))
        self.assertNotIn('observed_at', json.dumps(doc))
        self.assertIn('pit_forecast', doc['not_collected'])

    def test_coaching_export_keeps_only_numeric_evidence_and_fixed_semantics(self):
        with closing(TireHistoryStore(self.path)) as store:
            store.append(snapshot(),run_id=self.run['run_id'],diagnostics={'coaching':dict(ready=True,
                reason='own_matched_laps',candidate='input_front',front_change_c=8.,baseline_laps=[2,3],
                recent_laps=[4,5],wear_or_grip_confirmed=True,private='EXCLUDED')})
        data=self.unpack(self.provider()(self.run))['diagnostics'][0]['data']['coaching']
        self.assertEqual(data['front_change_c'],8.)
        self.assertEqual(data['baseline_laps'],[2,3])
        self.assertFalse(data['wear_or_grip_confirmed'])
        self.assertNotIn('EXCLUDED',json.dumps(data))

    def test_missing_or_legacy_database_is_not_created_or_assigned_by_timestamp(self):
        extra = self.provider()(self.run)
        self.assertFalse(extra.files)
        self.assertFalse(self.path.exists())
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE tire_observations(id INTEGER PRIMARY KEY, created REAL, run TEXT, data TEXT)')
            db.commit()
        before = self.path.read_bytes()
        self.assertEqual(self.provider()(self.run).info['reason'], 'legacy_unlinked_history')
        self.assertEqual(self.path.read_bytes(), before)

    def test_lap_and_stint_are_linked_and_never_cross_runs(self):
        with closing(TireHistoryStore(self.path)) as store:
            for lap in range(3,7):
                for n in range(31):
                    store.append(replace(snapshot((lap-3)*40.+n), lap=lap, session_type='Race'), run_id=self.run['run_id'])
            store.append(replace(snapshot(161.),lap=7,on_pit_road=True,session_type='Race'),run_id=self.run['run_id'])
        doc = self.unpack(self.provider()(self.run))
        self.assertEqual(len(doc['laps']), 4)
        self.assertEqual([r['eligible'] for r in doc['laps']], [False, False, True, True])
        self.assertEqual(len(doc['stints']), 1)

    def test_queue_flush_exports_pending_rows_without_stopping_collection(self):
        writer = TireHistoryWriter(self.path)
        self.addCleanup(writer.close)
        writer.submit(snapshot(), run_id=self.run['run_id'])
        from tire_reader.log_export import tire_data
        extra = tire_data(self.path, prepare=writer.flush)(self.run)
        self.assertEqual(len(self.unpack(extra)['observations']), 1)
        writer.submit(snapshot(1.),run_id=self.run['run_id'])
        self.assertTrue(writer.flush())
        self.assertTrue(writer.thread.is_alive())

    def test_size_fallback_keeps_real_samples_and_declares_discontinuity(self):
        with closing(TireHistoryStore(self.path)) as store:
            for t in range(20):
                store.append(snapshot(float(t)),run_id=self.run['run_id'])
        from tire_reader.log_export import tire_data
        with patch('tire_reader.log_export.REDUCED_ROWS', 4):
            full = tire_data(self.path)(self.run)
            reduced = full.fallback()
        doc = self.unpack(reduced)
        self.assertEqual([r['data']['t'] for r in doc['observations']], [0.,1.,18.,19.])
        self.assertEqual(doc['selection']['observations']['omitted'],16)
        self.assertFalse(doc['selection']['observations']['continuous'])

    def test_cold_pressure_is_setting_evidence_not_current_installed_value(self):
        row = dict(status='cockpit_warmup',overlay_ready=False,car_path='bmwm4gt3')
        ctx = dict(SessionTime=1., SessionNum=0, session_id=3,subsession_id=4,track_id=7,
                   track_config='test',IsInGarage=True,LFcoldPressure=151.,RFcoldPressure=0.)
        snap = from_iracing(row, ctx, 1.)
        self.assertEqual(snap.cold_pressure_kpa['FL'],151.)
        self.assertIsNone(snap.cold_pressure_kpa['FR'])
        with closing(TireHistoryStore(self.path)) as store:
            store.append(snap,run_id=self.run['run_id'])
        data = self.unpack(self.provider()(self.run))['observations'][0]['data']
        self.assertEqual(data['cold_pressure_kpa']['FL'],151.)
        self.assertFalse(data['cold_pressure_installed_verified'])
        self.assertFalse(data['active'])

    def test_migration_keeps_legacy_values_but_never_attaches_them(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('CREATE TABLE tire_observations(id INTEGER PRIMARY KEY,created REAL NOT NULL,run TEXT NOT NULL,game TEXT NOT NULL,car TEXT NOT NULL,track TEXT NOT NULL,lap INTEGER,t REAL,data TEXT NOT NULL)')
            db.execute('INSERT INTO tire_observations VALUES(1,1,?,?,?,?,?,?,?)',('old','iracing','oldcar','oldtrack',3,1.,'{}'))
            db.commit()
        with closing(TireHistoryStore(self.path)) as store:
            store.append(snapshot(3.),run_id=self.run['run_id'])
            self.assertEqual(store.db.execute('SELECT data,coach_run FROM tire_observations WHERE id=1').fetchone(),('{}',''))
        self.assertEqual(len(self.unpack(self.provider()(self.run))['observations']),1)

    def test_unknown_database_fields_and_raw_ids_do_not_enter_attachment(self):
        with closing(TireHistoryStore(self.path)) as store:
            store.append(snapshot(),run_id=self.run['run_id'],diagnostics={'reader_status':'reading'})
            row=store.db.execute('SELECT data FROM tire_observations').fetchone()[0]
            data=json.loads(row);data.update(UserName='Private Driver',image='image data',recognized_text='OCR content',session_id='private-native-id')
            store.db.execute('UPDATE tire_observations SET data=?',(json.dumps(data),));store.db.commit()
        text=json.dumps(self.unpack(self.provider()(self.run)))
        for forbidden in ('Private Driver','image data','OCR content','private-native-id','session_key'):
            self.assertNotIn(forbidden,text)

    def test_corrupt_database_preserves_base_log_and_marks_reason(self):
        self.path.write_bytes(b'not a database')
        self.assertEqual(self.provider()(self.run).info['reason'],'tire_history_unreadable')


class TireRunBindingTests(unittest.TestCase):
    def test_binding_requires_native_session_car_track_and_nearby_game_clock(self):
        from tire_reader.log_export import linked_run, native_session_ref
        from types import SimpleNamespace
        import threading
        identity=dict(game='iracing',car_path='bmwm4gt3',track_id='7',track_config='test',
                      session_id='3',subsession_id='4',session_num=0)
        s = replace(snapshot(20.),car='bmwm4gt3',track='iracing-track-7-test',
                    native_session_ref=native_session_ref('iracing','3','4',0))
        live=SimpleNamespace(lock=threading.Lock(),run_id='correct-run',identity=identity,
                             samples=[dict(wall_time=100.,session_time=20.)],clock=lambda:100.)
        self.assertEqual(linked_run(live,s),'correct-run')
        for changed in [dict(t=40.),dict(car='ferrari296gt3'),dict(track='iracing-track-8-test'),dict(native_session_ref='wrong')]:
            self.assertEqual(linked_run(live,replace(s,**changed)),'')
        live.identity={**identity,'game':'lmu'}
        self.assertEqual(linked_run(live,s),'')

    def test_offline_zero_ids_match_main_adapter_empty_ids(self):
        from tire_reader.snapshot import native_session_ref
        self.assertEqual(native_session_ref('iracing',0,0,0),native_session_ref('iracing','','',0))

    def test_lmu_uses_native_session_prefixes_not_process_local_session_number(self):
        from tire_reader.log_export import linked_run, native_session_ref
        from types import SimpleNamespace
        import threading
        ref='a'*64
        s=replace(snapshot(20.),game='lmu',car='lmu-car-bmw-m4',track='lmu-track-monza',
            native_session_ref=native_session_ref('lmu',ref[:16],ref[16:32],None))
        live=SimpleNamespace(lock=threading.Lock(),run_id='lmu-run',samples=[dict(wall_time=100.,session_time=20.)],clock=lambda:100.,
            identity=dict(game='lmu',session_id=ref[:16],subsession_id=ref[16:32],session_num=55,car_path=s.car,track_path=s.track))
        self.assertEqual(linked_run(live,s),'lmu-run')

    def test_prepared_audio_response_carries_the_linked_run_for_delivery_logs(self):
        from tire_overlay_control import TireOverlayController
        from tire_reader.advisor import TireAdvisor
        import time
        with tempfile.TemporaryDirectory() as temporary:
            c=TireOverlayController(Path(temporary)/'tires.json',audio=lambda _: 'QA-only-audio',run_link=lambda _:'linked-run')
            c.voice_enabled=True;c.advisor=TireAdvisor(clock=lambda:time.monotonic())
            for n in range(3):
                c.snapshot=replace(snapshot(float(n)),observed_at=time.monotonic())
                c.advisor.observe(c.snapshot.to_dict(),enabled=True)
            event=c.advisor.current(time.monotonic())
            rendered=c.render_voice(event['id'])
            self.assertEqual(rendered['event']['run_id'],'linked-run')


class TireSdkSettingTests(unittest.TestCase):
    def test_lap_fuel_and_wetness_context_keeps_unknowns_and_source_units(self):
        row={'status':'reading','overlay_ready':True,'car_path':'bmwm4gt3','raw_kpa_FL':170.}
        ctx={'SessionTime':2.,'Lap':3,'SessionNum':0,'FuelLevel':0.,'LapLastLapTime':90.5,
             'TrackWetness':3,'PlayerTireCompound':0}
        s=from_iracing(row,ctx,2.)
        self.assertEqual((s.fuel_l,s.lap_last_s,s.track_wetness_iracing,s.tire_compound_index),(0.,90.5,3,0))
        self.assertIsNone(s.track_wetness_fraction_lmu)
        unknown=from_iracing(row,{**ctx,'LapLastLapTime':-1.,'FuelLevel':float('nan')},2.)
        self.assertIsNone(unknown.lap_last_s);self.assertIsNone(unknown.fuel_l)

    def test_cold_sdk_channel_and_garage_fallback_are_kept_separate(self):
        from tire_reader.sdk import SdkReader
        from types import SimpleNamespace
        from unittest.mock import Mock
        values={'WeekendInfo':{'SessionID':3,'SubSessionID':4,'TrackID':7,'TrackConfigName':'test'},
                'DriverInfo':{'DriverCarIdx':0,'Drivers':[{'CarIdx':0,'CarPath':'bmwm4gt3'}]},
                'SessionInfo':{'Sessions':[{'SessionNum':0,'SessionType':'Practice'}]},
                'CarSetup':{'Chassis':{'LeftFront':{'ColdPressure':'150 kPa'},'RightFront':{'ColdPressure':'22.0 psi'}}},
                'SessionTime':1.,'SessionNum':0,'LFcoldPressure':151.}
        sdk=Mock();sdk.var_headers_names=['SessionTime','SessionNum','LFcoldPressure']
        sdk._header=SimpleNamespace(session_info_update=1);sdk._var_buffer_latest=SimpleNamespace(tick_count=1)
        sdk.__class__=dict
        class Sdk:
            var_headers_names=sdk.var_headers_names
            _header=sdk._header
            _var_buffer_latest=sdk._var_buffer_latest
            freeze_var_buffer_latest=Mock()
            unfreeze_var_buffer_latest=Mock()
            def __getitem__(self,key):return values.get(key)
        adapter=SdkReader(Sdk());ctx=adapter.snapshot(1)
        row={'status':'cockpit_warmup','overlay_ready':False,'car_path':'bmwm4gt3'}
        s=from_iracing(row,ctx,1.)
        self.assertEqual(s.cold_pressure_kpa['FL'],151.)
        self.assertEqual(s.cold_pressure_source['FL'],'sdk_cold_setting')
        self.assertAlmostEqual(s.cold_pressure_kpa['FR'],22*6.894757293,places=4)
        self.assertEqual(s.cold_pressure_source['FR'],'garage_setup_snapshot')
        self.assertIsNone(s.cold_pressure_kpa['RR'])


class TireZipTests(unittest.TestCase):
    def test_existing_download_reader_and_size_policy_include_or_explicitly_omit_tires(self):
        from voice_notes import VoiceNoteStore
        from tire_reader.log_export import tire_data
        from unittest.mock import Mock
        import time
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);store=VoiceNoteStore(str(folder/'voice.sqlite3'))
            run='11111111-2222-3333-4444-555555555555'
            store.record_session_health(run,{'game':'iracing','car_path':'mclaren720sgt3','track_path':'test'},
                {'at':time.time(),'state':'CONNECTED','on_track':True,'is_replay':False})
            path=folder/'tires.sqlite3'
            with closing(TireHistoryStore(path)) as history:
                for n in range(80):history.append(snapshot(float(n)),run_id=run,diagnostics={'reader_status':'reading'})
            name,full=session_log.export(store,run,'2.5.0',extras=[tire_data(path)])
            self.assertTrue(name.endswith('.zip'))
            manifest,_,_=session_log.read(full)
            self.assertTrue(manifest['extras']['tire_data']['included'])
            self.assertEqual(len(session_log.read_extras(full)['tires']['observations']),80)
            with patch('tire_reader.log_export.REDUCED_ROWS',4):
                base=session_log.export(store,run,'2.5.0')[1]
                tiny=session_log.export(store,run,'2.5.0',extras=[tire_data(path)],limit=len(base)+1400)[1]
            detail=session_log.read(tiny)[0]['extras']['tire_data']
            if detail['included']:
                self.assertLessEqual(detail['observations'],4)
            else:
                self.assertEqual(detail['reason'],'size_limit')

    def test_controller_pipe_records_semantics_failures_and_target_provenance(self):
        from tire_overlay_control import TireOverlayController
        from tire_reader.log_export import tire_data
        from types import SimpleNamespace
        s=snapshot(20.)
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'tires.json'
            c=TireOverlayController(path,run_link=lambda _: 'test-driving-run')
            c._targets().save(s.car,{'pressure_low_bar':1.6,'pressure_high_bar':1.8})
            messages=[dict(type='state',status='reading',visible=False,tire_snapshot=s.to_dict()),
                      dict(type='state',status='not_found',visible=True,tire_snapshot=replace(s,t=21.,
                        wheels={w:replace(v,pressure_kpa=None,temp_c=None,pressure_state=None,temp_state=None) for w,v in s.wheels.items()}).to_dict())]
            child=SimpleNamespace(stdout=[json.dumps(m) for m in messages])
            c.process=child;c.voice_enabled=True
            c._read(child)
            self.assertTrue(c.history.flush())
            extra=tire_data(path.with_name('tires-history.sqlite3'))({'game':'iracing','run_id':'test-driving-run'})
            import lzma
            doc=json.loads(lzma.decompress(extra.files['tires.json.xz']))
            self.assertEqual(len(doc['observations']),1)
            self.assertEqual(doc['diagnostics'][0]['data']['color_states']['FL']['pressure'],'high')
            self.assertEqual(doc['diagnostics'][0]['data']['configured_targets']['pressure_high_bar'],1.8)
            self.assertEqual(doc['diagnostics'][1]['data']['reader_status'],'not_found')
            self.assertFalse(doc['diagnostics'][1]['data']['source_ok']['FL']['pressure'])
            c.process=None;c.close()
