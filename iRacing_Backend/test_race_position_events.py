"""Evidence boundaries for race chart annotations, using temporary synthetic files."""
from types import SimpleNamespace
import unittest

from race_position_events import observe, finalize
import test_race_positions as fixtures
from timing_log import TimingLog


class EventTests(unittest.TestCase):
    def test_pit_and_incident_require_continuous_known_values(self):
        row = {}
        observe(row, 10, 1, distance=.1, pit=1, incidents=4)
        self.assertEqual(row['events'], [])  # Initial pit/count is a baseline.
        observe(row, 11, 1, distance=.2, pit=0, incidents=6)
        self.assertEqual([e['kind'] for e in row['events']], ['pit_out', 'incident'])
        self.assertEqual(row['events'][1]['delta'], 2)
        self.assertEqual(row['events'][1]['lap'], 2)
        self.assertEqual(row['events'][1]['x'], 1.2)
        observe(row, 12, 1, distance=.3, pit=1, incidents=0, gap=True)
        observe(row, 13, 1, distance=.4, pit=0, incidents=0)
        self.assertNotIn('duration_s', row['events'][-1])
        self.assertEqual(len([e for e in row['events'] if e['kind'] == 'incident']), 1)

    def test_pit_span_stops_at_gap_missing_pit_or_counter_jump(self):
        for broken in ({'gap':True}, {'pit':None}, {'completed':8}):
            row={}
            observe(row, 10, 1, distance=.1, pit=0)
            observe(row, 11, 1, distance=.2, pit=1)
            args=dict(at=12,completed=1,distance=.3,pit=1)
            args.update(broken)
            observe(row, **args)
            observe(row, 13, 1, distance=.4,pit=0)
            observe(row, 14, 1, distance=.5,pit=1)
            observe(row, 15, 1, distance=.6,pit=0)
            self.assertNotIn('end_x', row['events'][0], broken)

    def test_pit_span_records_observed_lane_interval(self):
        row={}
        for at,pct,pit in [(10,.1,0),(11,.2,1),(12,.3,1),(13,.4,0)]:
            observe(row,at,1,distance=pct,pit=pit)
        self.assertEqual(row['events'][0]['end_x'],1.4)
        self.assertEqual(row['events'][0]['duration_s'],2)

    def test_class_best_needs_every_class_cars_known_best_at_that_time(self):
        for scope,known,expected in [(False,True,'personal'),(True,False,'personal'),(True,True,'fastest')]:
            own=dict(own=True,events=[dict(kind='personal',at_s=20,x=2,lap=2,time_s=80)],_best_history=[(20,80)])
            other=dict(own=False,_best_history=[(10 if known else 30,81)])
            result=dict(status='READY',series=[own,other],_event_best_scope=scope)
            finalize(result,SimpleNamespace(channels={}))
            self.assertEqual(own['events'][0]['kind'],expected)


class SourceTests(unittest.TestCase):
    setUp=fixtures.RacePositionsTests.setUp
    native=fixtures.RacePositionsTests.native
    own_native=fixtures.RacePositionsTests.own_native
    history=fixtures.RacePositionsTests.history
    race=fixtures.RacePositionsTests.race

    def test_native_optional_signals_preserve_first_and_unfinished_lap_events(self):
        import numpy as np
        import struct
        from telemetry_types import VariableHeader
        telemetry=self.native()
        old=telemetry.path.read_bytes()
        width=88
        raw=np.zeros((telemetry.record_count,width),dtype='u1')
        raw[:,:48]=np.frombuffer(old[144:],dtype='u1').reshape(-1,48)
        fields=[('OnPitRoad',1,48,1),('LapDistPct',4,52,1),('PlayerCarMyIncidentCount',2,56,1),
                ('CarIdxBestLapTime',4,60,3),('CarIdxLastLapTime',4,72,3)]
        from telemetry_channels import IRSDK_DTYPES
        for name,kind,offset,count in fields:
            telemetry.variables[name]=VariableHeader(kind,offset,count,False,name,'','')
            view=np.ndarray((telemetry.record_count,count),dtype=IRSDK_DTYPES[kind],buffer=raw,offset=offset,strides=(width,IRSDK_DTYPES[kind].itemsize))
            if name=='OnPitRoad':view[60:240]=True
            elif name=='LapDistPct':view[:]=.3
            elif name=='PlayerCarMyIncidentCount':view[60:]=2;view[540:]=3
            else:view[:]=[80,70,82];view[360:,0]=79
        header=bytearray(old[:144]);struct.pack_into('<i',header,36,width)
        telemetry.path.write_bytes(header+raw.tobytes());telemetry.buffer_length=width
        result=self.history(telemetry)
        own=result['series'][0]
        self.assertEqual([e['delta'] for e in own['events'] if e['kind']=='incident'],[2,1])
        self.assertTrue(any(e['kind']=='fastest' for e in own['events']))
        self.assertLess(result['start_lap'],1)
        self.assertGreater(result['end_lap'],4)
        self.assertEqual(own['points'][0][0],1)  # No extrapolated starting rank.

    def test_existing_lap_verdicts_are_attached_only_to_matching_crossings(self):
        telemetry=self.native()
        telemetry.laps=[SimpleNamespace(lap_number=i,start_idx=(i-1)*120,end_idx=i*120-1,
                         complete=True,valid=i!=2,status='INVALID' if i==2 else 'VALID',
                         invalid_reason='incident during the lap',lap_time_s=2-i*.01) for i in range(1,5)]
        result=self.history(telemetry)
        own=result['series'][0]
        self.assertEqual([e['kind'] for e in own['events']],['personal','invalid','personal','personal'])
        self.assertEqual(own['events'][1]['x'],2)
        self.assertEqual(result['series'][1]['events'],[])
        self.assertFalse(any(key.startswith('_') for key in result))
        self.assertFalse(any(key.startswith('_') for key in own))

    def test_timing_pit_events_do_not_invent_others_incidents_or_validity(self):
        telemetry=self.own_native()
        log=TimingLog(self.timing)
        for at in range(100,110):
            race=self.race(at)
            for car in race['cars']:
                car.update(distance_pct=.2+((at-100)%2)*.4,on_pit_road=104<=at<107,last_lap_s=90)
            log.record(race,'events',at,identity=self.identity,wall_time=1800000000+at-100)
        log.flush()
        result=self.history(telemetry)
        other=result['series'][1]
        self.assertEqual([e['kind'] for e in other['events']],['pit_in','pit_out'])
        self.assertEqual(other['events'][0]['duration_s'],3)


if __name__ == '__main__':
    unittest.main()
