import unittest
from unittest.mock import Mock
from tire_reader.lmu_source import LmuTireReader
from tire_reader.snapshot import TireSnapshot


def frame(t=10., **patch):
    return dict(dict(session_id='test-session',vehicle_id=4,telemetry_et=t,scoring_et=t,
        vehicle_model='BMW M4 LMGT3',track_name='Monza - Grand Prix',
        telemetry_updated=True,scoring_updated=True,realtime=True,paused=False,
        in_garage_stall=False,in_pits=False,control_code=0,session_phase=5,
        session_type_code=10,total_laps=3,ambient_temp_c=22.,track_temp_c=31.,
        wheels=[dict(position=w,pressure_kpa=160.+i,
            temperature_c=[80.+i,82.+i,84.+i],wear_fraction=.8-i*.1)
            for i,w in enumerate(('FL','FR','RL','RR'))]),**patch)


class LmuSourceTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.raw=frame();self.source=Mock()
        self.source.read.side_effect=lambda **kw:self.raw
        self.reader=LmuTireReader(source_factory=lambda:self.source,clock=lambda:self.now)
        self.addCleanup(self.reader.close)

    def sample(self, now):
        self.now=now;self.raw['telemetry_et']=10.+now
        if self.raw['scoring_et'] is not None:self.raw['scoring_et']=10.+now
        return self.reader.sample()

    def test_ten_second_gate_then_fixed_wheel_order_kpa_and_celsius(self):
        for t in (0.,1.,9.9):self.assertFalse(self.sample(t)['overlay_ready'])
        row=self.sample(10.)
        self.assertTrue(row['overlay_ready']);self.assertEqual(row['status'],'reading')
        self.assertEqual([row['raw_kpa_'+w] for w in ('FL','FR','RL','RR')],[160,161,162,163])
        # The shared reader already converted Kelvin. Never subtract 273.15 twice.
        self.assertEqual(row['raw_temp_c_FL'],82.)
        snap=TireSnapshot.from_dict(row['tire_snapshot'])
        self.assertEqual(snap.wheels['RR'].surface_lcr_c,(83.,85.,87.))
        self.assertAlmostEqual(snap.wheels['FL'].wear_fraction,.8)
        self.assertEqual(snap.wheels['FL'].temp_kind,'surface_center')
        self.assertEqual(snap.car,'lmu-car-bmw-m4-lmgt3')

    def test_replay_ai_garage_unknown_context_never_produce_values(self):
        for patch in ({'realtime':False},{'control_code':1},{'in_garage_stall':True},
                      {'paused':True},{'realtime':None},{'control_code':None}):
            with self.subTest(patch=patch):
                self.raw=frame(**patch);row=self.sample(20.)
                self.assertFalse(row['overlay_ready']);self.assertNotIn('raw_kpa_FL',row)

    def test_missing_duplicate_or_stale_frame_is_not_a_new_measurement(self):
        self.sample(0.);self.sample(10.)
        self.now=11.;row=self.reader.sample()
        self.assertEqual(row['status'],'telemetry_waiting')
        self.assertTrue(row['overlay_ready'])
        self.assertNotIn('raw_kpa_FL',row)
        self.now=13.;row=self.reader.sample()
        self.assertFalse(row['overlay_ready'])
        self.source.read.return_value=None;self.source.read.side_effect=None
        self.now=14.;self.assertFalse(self.reader.sample()['overlay_ready'])

    def test_model_session_changes_reset_gate_without_livery_keys(self):
        self.sample(0.);self.sample(10.)
        self.raw.update(vehicle_model='Ferrari 296 LMGT3',car_name='Private driver #55')
        row=self.sample(11.)
        self.assertEqual(row['car_path'],'lmu-car-ferrari-296-lmgt3')
        self.assertFalse(row['overlay_ready']);self.assertNotIn('Private',str(row))
        self.assertTrue(self.sample(21.)['overlay_ready'])
        self.raw['session_id']='new';self.assertFalse(self.sample(22.)['overlay_ready'])

    def test_field_errors_and_scoring_gaps_do_not_invent_values_or_hide_window(self):
        self.sample(0.);self.sample(10.)
        self.raw['wheels'][0].update(pressure_kpa=float('nan'),temperature_c=[0.,None,2.])
        row=self.sample(11.)
        self.assertIsNone(row['raw_kpa_FL']);self.assertIsNone(row['raw_temp_c_FL'])
        self.raw.update(scoring_et=None,control_code=None,realtime=None,in_garage_stall=None,
                        in_pits=None,session_phase=None)
        row=self.sample(12.)
        self.assertTrue(row['overlay_ready'])
        self.assertEqual(row['status'],'reading')
        row=self.sample(20.)
        self.assertFalse(row['overlay_ready'])

    def test_connection_failure_retries_and_closes_only_owned_source(self):
        self.source.open.side_effect=OSError('unavailable')
        row=self.sample(0.);self.assertEqual(row['status'],'sdk_waiting')
        self.source.close.assert_called_once()
        self.source.open.side_effect=None
        self.assertEqual(self.sample(1.)['status'],'cockpit_warmup')
