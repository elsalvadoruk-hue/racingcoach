from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from lmu_replay import replay_laps
from lmu_telemetry import LmuTelemetryReader
from telemetry_core import ChannelTrace
from test_lmu_analysis import duckdb, make_analysis_file


@unittest.skipUnless(duckdb, 'LMU 전용 의존성을 설치하지 않은 환경입니다.')
class LmuReplayTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path=Path(folder.name)/'replay.duckdb'
        make_analysis_file(self.path)
        with duckdb.connect(str(self.path)) as con:
            con.execute("INSERT INTO metadata VALUES ('CarClass','GT3')")
        self.laps=[LmuTelemetryReader(self.path).analysis_lap(i) for i in (1,2)]

    def test_elapsed_time_distance_and_projection_are_analytic_and_json_safe(self):
        before=hashlib.sha256(self.path.read_bytes()).hexdigest()
        r=replay_laps(self.laps,101)
        json.dumps(r,allow_nan=False)
        self.assertEqual(r['duration_s'],10)
        self.assertEqual(len(r['frames']),101)
        self.assertEqual(r['alignment'],'LAP_EVENT_ELAPSED_TIME')
        self.assertTrue(all(p['valid'] is None for p in r['paths']))
        # Native test coordinate t=110: lat60,lon0; first sample at t110.0275.
        origin_lat=60+.0275*.00005
        for frame in r['frames']:
            elapsed=frame['elapsed_s']
            if not .1<=elapsed<=8.8:
                continue
            for i,state in enumerate(frame['laps']):
                self.assertEqual(state['status'],'SUPPORTED')
                duration=10 if i==0 else 9
                self.assertAlmostEqual(state['distance_m'],elapsed*1000/duration,places=6)
                self.assertAlmostEqual(state['speed_mps'],50)
                self.assertAlmostEqual(state['steering_ratio'],.25)
                shift=elapsed+10*i-.0275
                self.assertAlmostEqual(state['position']['x_m'],shift*.0001*math.pi/180*6371000*math.cos(math.radians(origin_lat)),places=6)
                self.assertAlmostEqual(state['position']['y_m'],shift*.00005*math.pi/180*6371000,places=6)
        self.assertEqual(r['frames'][0]['laps'][0]['status'],'NO_POSITION')
        self.assertEqual(r['frames'][90]['laps'][1]['status'],'FINISHED')
        self.assertTrue(all(s['status']=='FINISHED' and s['position'] is None for s in r['frames'][-1]['laps']))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(),before)
        self.assertFalse(self.path.with_suffix('.duckdb.wal').exists())

    def test_independent_session_clock_has_no_effect_on_replay(self):
        base=replay_laps(self.laps,31)
        shifted=[]
        for i,lap in enumerate(self.laps):
            offset=2000*(i+1)
            channels={key:replace(trace,time_s=trace.time_s+offset) for key,trace in lap.channels.items()}
            shifted.append(replace(lap,start_time_s=lap.start_time_s+offset,end_time_s=lap.end_time_s+offset,channels=channels))
        moved=replay_laps(shifted,31)
        for a,b in zip(base['frames'],moved['frames']):
            for p,q in zip(a['laps'],b['laps']):
                self.assertEqual(p['status'],q['status'])
                if p['position']:
                    self.assertAlmostEqual(p['position']['x_m'],q['position']['x_m'],places=5)
                    self.assertAlmostEqual(p['position']['y_m'],q['position']['y_m'],places=5)
                    self.assertAlmostEqual(p['distance_m'],q['distance_m'],places=5)

    def test_missing_sample_gap_and_jump_never_get_a_marker(self):
        lap=self.laps[0]
        original=lap.channels['longitude_deg']
        for scenario in ('nan','jump','gap'):
            values=original.values.copy()
            times=original.time_s.copy()
            if scenario=='nan':
                values[50]=np.nan
            elif scenario=='jump':
                values[50]+=1
            else:
                times=np.delete(times,50);values=np.delete(values,50)
            channels=lap.channels|{'longitude_deg':ChannelTrace(original.source_name,'deg',10,times,values,np.ones(len(times),bool))}
            modified=replace(lap,channels=channels)
            r=replay_laps([modified,self.laps[1]],1001)
            for elapsed in (5.01,5.03,5.09):
                index=round(elapsed/10*1000)
                self.assertEqual(r['frames'][index]['laps'][0]['status'],'NO_POSITION',scenario)
            self.assertEqual(r['frames'][520]['laps'][0]['status'],'SUPPORTED')

    def test_missing_input_is_null_and_never_zero_filled(self):
        channels=self.laps[1].channels.copy()
        del channels['brake_ratio']
        r=replay_laps([self.laps[0],replace(self.laps[1],channels=channels)],101)
        self.assertIsNone(r['frames'][20]['laps'][1]['brake_ratio'])
        self.assertEqual(r['frames'][20]['laps'][1]['status'],'SUPPORTED')

    def test_six_selections_and_display_grid_do_not_change_matching_frames(self):
        many=[self.laps[i%2] for i in range(6)]
        a=replay_laps(many,101)
        b=replay_laps(many,1001)
        self.assertEqual(len(a['paths']),6)
        for i,frame in enumerate(a['frames']):
            for p,q in zip(frame['laps'],b['frames'][i*10]['laps']):
                self.assertEqual(p['status'],q['status'])
                if p['position']:
                    self.assertAlmostEqual(p['position']['x_m'],q['position']['x_m'])

    def test_mismatch_missing_coordinates_and_arguments_are_rejected(self):
        for laps,points in ((self.laps[:1],501),(self.laps*4,501),(self.laps,2),(self.laps,1002),(self.laps,True)):
            with self.assertRaises(ValueError):
                replay_laps(laps,points)
        with self.assertRaisesRegex(ValueError,'메타데이터'):
            replay_laps([self.laps[0],replace(self.laps[1],metadata=self.laps[1].metadata|{'car_name':'Other'})])
        channels=self.laps[0].channels.copy()
        del channels['latitude_deg']
        with self.assertRaisesRegex(ValueError,'좌표'):
            replay_laps([replace(self.laps[0],channels=channels),self.laps[1]])


if __name__=='__main__':
    unittest.main()
