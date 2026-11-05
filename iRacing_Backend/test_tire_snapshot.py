import unittest
from tire_reader.snapshot import TireSnapshot, TireReadings, from_iracing


class SnapshotTests(unittest.TestCase):
    def row(self, **patch):
        return dict(status='reading', overlay_ready=True, cockpit_epoch=1,
                    car_path='bmwm4gt3', raw_kpa_FL=159., raw_temp_c_FR=34., **patch)

    def context(self, **patch):
        return dict(SessionTime=25., SessionNum=2, session_id=1, subsession_id=2,
                    track_id=62, track_config='Grand Prix', Lap=3, AirTemp=22.,
                    TrackTempCrew=31., IsInGarage=False, OnPitRoad=False, **patch)

    def test_partial_ocr_has_separate_pressure_and_temperature_validity(self):
        snap=from_iracing(self.row(),self.context(),100.)
        self.assertEqual(snap.wheels['FL'].pressure_kpa,159.)
        self.assertIsNone(snap.wheels['FL'].temp_c)
        self.assertTrue(snap.source_ok['FL']['pressure'])
        self.assertFalse(snap.source_ok['FL']['temperature'])
        self.assertTrue(snap.source_ok['FR']['temperature'])
        self.assertEqual(snap.lap,3)
        self.assertEqual(TireSnapshot.from_dict(snap.to_dict()).to_dict(),snap.to_dict())

    def test_error_rows_do_not_claim_numeric_measurements(self):
        row=self.row();row['status']='pressure_area_occluded'
        snap=from_iracing(row,self.context(),100.)
        self.assertIsNone(snap.wheels['FL'].pressure_kpa)
        self.assertFalse(snap.source_ok['FL']['pressure'])

    def test_missing_pressure_and_old_temperature_have_independent_ages(self):
        readings=TireReadings()
        readings.update(from_iracing(self.row(),self.context(),100.))
        row=self.row();row['raw_kpa_FL']=161.;row['raw_temp_c_FR']=None
        readings.update(from_iracing(row,{**self.context(),'SessionTime':26.},102.))
        self.assertEqual(readings.value('FL','pressure',103.),161.)
        self.assertEqual(readings.age('FR','temperature',103.),3.)
        self.assertIsNone(readings.value('FR','temperature',131.))
        self.assertEqual(readings.value('FR','temperature',131.,max_age=None),34.)

    def test_context_switch_and_session_rewind_clear_previous_values(self):
        readings=TireReadings();readings.update(from_iracing(self.row(),self.context(),100.))
        row=self.row();row['raw_kpa_FL']=None
        readings.update(from_iracing(row,{**self.context(),'SessionTime':0.},101.))
        self.assertIsNone(readings.value('FL','pressure',101.,max_age=None))
        row=self.row();readings.update(from_iracing(row,self.context(),102.))
        row['car_path']='ferrari296gt3';row['raw_kpa_FL']=None
        readings.update(from_iracing(row,self.context(),103.))
        self.assertIsNone(readings.value('FL','pressure',103.,max_age=None))

    def test_non_finite_and_bool_values_cannot_become_fresh(self):
        row=self.row();row['raw_kpa_FL']=float('nan');row['raw_temp_c_FR']=True
        snap=from_iracing(row,self.context(),100.)
        self.assertFalse(any(v for wheel in snap.source_ok.values() for v in wheel.values()))
