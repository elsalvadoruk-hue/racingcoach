"""Filename-only web references: real reader and unchanged HUD admission checks."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from garage61_filename import garage61_filename_metadata
from garage61_reader import Garage61ReferenceReader
from overlay_reference import OverlayReference,live_request
from test_overlay_reference import live,wait_for,metadata

class WebReferenceTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.fetch=Mock(side_effect=AssertionError('No API calls for local CSVs'))
        self.loader=lambda path:Garage61ReferenceReader(path,metadata_lookup=garage61_filename_metadata).parse()
        self.service=OverlayReference(self.root,self.loader,fetch=self.fetch)
        self.addCleanup(self.service.close)
        self.enterContext(patch('overlay_reference.g61._token_from_sources',return_value=(None,None)))
        self.view=live();self.view['identity'].update(car_name='Test Car',track_name='Test Track',track_config='Full')

    def csv(self,ident='WEB',car='Test Car',track='Test Track (Full)',time='01.40.000'):
        path=self.root/f'Garage 61 - Driver - {car} - {track} - {time} - {ident}.csv'
        path.write_text('Speed,LapDistPct,Brake,Throttle,SteeringWheelAngle\n'+'\n'.join(f'50,{i/1000},0.2,0.8,0' for i in range(1001)))
        return path

    def test_worker_reads_unverified_dry_web_csv_and_uses_actual_pedal_curve(self):
        path=self.csv();before=path.read_bytes()
        self.service.observe(self.view,True)
        wait_for(lambda:self.service.status()['state']=='READY')
        state=self.service.status()
        self.assertTrue(state['unverified_conditions'])
        self.assertIn('조건 미확인 기준',state['message'])
        self.assertIn('180일',state['condition_note'])
        self.assertIsNotNone(self.service.enrich(self.view)['comparison'])
        self.assertNotIn('clean',self.service.reference.meta)
        self.assertEqual(path.read_bytes(),before)
        self.fetch.assert_not_called()
        self.service.observe({**self.view,'snapshot':{**self.view['snapshot'],'track_wetness':3}},True)
        wait_for(lambda:self.service.status()['state']!='LOADING')
        self.assertIsNone(self.service.reference)

    def test_wet_unknown_lmu_and_mismatched_layout_are_withheld(self):
        self.csv()
        for wet in (True,None):
            context=live_request(self.view);context['conditions']['weather_declared_wet']=wet
            self.assertIsNone(self.service._web_fallback(None,context,5000))
        context=live_request(self.view);context['game']='lmu'
        self.assertIsNone(self.service._web_fallback(None,context,5000))
        for field,value in [('car_name','Different Car'),('track_config','Short'),('track_name','Different Track')]:
            context=live_request(self.view);context['identity'][field]=value
            self.assertIsNone(self.service._web_fallback(None,context,5000))

    def test_metadata_rejection_cannot_reenter_as_unverified_and_bad_csv_is_skipped(self):
        path=self.csv('REJECTED')
        for suffix in ('.meta.json','.csv.meta.json'):
            side=path.with_suffix(suffix);side.write_text(json.dumps({'clean':False}))
            self.assertIsNone(self.service._web_fallback(None,live_request(self.view),5000));side.unlink()
        (self.root/'garage61_catalog.json').write_text(json.dumps({'laps':{'REJECTED':{'clean':False}}}))
        self.assertIsNone(self.service._web_fallback(None,live_request(self.view),5000))
        bad=self.csv('BAD',time='01.39.000');bad.write_text(bad.read_text().replace('0.2,0.8','2,0.8'))
        self.csv('GOOD')
        result=self.service._web_fallback(None,live_request(self.view),5000)
        self.assertEqual(result[1]['lap_id'],'GOOD')

    def test_verified_metadata_and_personal_reference_take_priority(self):
        self.csv('WEB')
        verified=self.csv('VERIFIED',time='01.41.000')
        verified.with_suffix('.meta.json').write_text(json.dumps(metadata(lap_id='VERIFIED',lap_time_s=100)))
        self.assertEqual(self.service._prepare(None,live_request(self.view),5000)[1]['lap_id'],'VERIFIED')
        verified.with_suffix('.meta.json').unlink();verified.unlink()
        self.service.personal_loader=Mock(return_value=('personal',{'state':'READY','source':'PERSONAL'}))
        self.assertEqual(self.service._prepare(None,live_request(self.view),5000)[1]['source'],'PERSONAL')
        self.service.personal_loader.return_value=None
        self.assertEqual(self.service._prepare(None,live_request(self.view),5000)[1]['lap_id'],'WEB')

    def test_pinned_task_never_falls_back_and_physical_consistency_is_required(self):
        path=self.csv();context=live_request(self.view)
        context['pinned_reference']={'source':'GARAGE61'}
        self.assertIsNone(self.service._web_fallback(None,context,5000))
        # A complete but physically inconsistent record is rejected by the existing gate.
        path.write_text(path.read_text().replace('50,','5,'))
        self.assertIsNone(self.service._web_fallback(None,live_request(self.view),5000))

    def test_only_latest_1500_filename_only_exports_are_considered(self):
        path=self.csv();os.utime(path,(1,1))
        for index in range(1500):
            other=self.root/f'Garage 61 - Driver - Other Car - Test Track (Full) - 01.40.000 - X{index}.csv'
            other.write_text('unused');os.utime(other,(2,2))
        context=live_request(self.view)
        self.assertIsNone(self.service._web_fallback(None,context,5000))
        os.utime(path,(3,3))
        self.assertEqual(self.service._web_fallback(None,context,5000)[1]['lap_id'],'WEB')
