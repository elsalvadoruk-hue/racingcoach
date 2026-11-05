import unittest
from tire_reader.cockpit_gate import CockpitGate,cockpit_context


def cockpit(**extra):
    return dict(dict(car_path='bmwm4gt3',fresh=True,IsOnTrack=True,IsOnTrackCar=True,
        IsInGarage=False,IsReplayPlaying=False,generation=1,SessionTime=1,SessionNum=0,
        session_id=3,subsession_id=4,track_id=7,track_config='test'),**extra)


class CockpitGateTests(unittest.TestCase):
    def test_ready_at_ten_seconds_after_entry_and_not_application_start(self):
        gate=CockpitGate()
        self.assertFalse(gate.update({},0)['ready'])
        self.assertEqual(gate.update(cockpit(),100)['remaining_s'],10)
        self.assertFalse(gate.update(cockpit(SessionTime=10),109.999)['ready'])
        self.assertTrue(gate.update(cockpit(SessionTime=11),110)['ready'])

    def test_exit_replay_stale_disconnect_and_other_car_require_new_wait(self):
        for invalid in [dict(IsOnTrack=False),dict(IsOnTrackCar=False),dict(IsInGarage=True),
                        dict(IsReplayPlaying=True),dict(fresh=False),dict(car_path='porsche992cup')]:
            with self.subTest(invalid=invalid):
                gate=CockpitGate();gate.update(cockpit(),0)
                self.assertTrue(gate.update(cockpit(SessionTime=11),10)['ready'])
                self.assertFalse(gate.update(cockpit(**invalid),11)['ready'])
                self.assertEqual(gate.update(cockpit(SessionTime=13),12)['remaining_s'],10)
                self.assertFalse(gate.update(cockpit(SessionTime=22),21)['ready'])

    def test_car_session_generation_track_and_time_rewind_reset_epoch(self):
        for changed in [dict(car_path='porsche9922cup'),dict(generation=2),dict(track_id=8),
                        dict(SessionNum=1),dict(session_id=5),dict(subsession_id=6),dict(SessionTime=0)]:
            with self.subTest(changed=changed):
                gate=CockpitGate();gate.update(cockpit(),0)
                old=gate.update(cockpit(SessionTime=12),11)
                self.assertTrue(old['ready'])
                new=gate.update(cockpit(**changed),12)
                self.assertFalse(new['ready'])
                self.assertEqual(new['epoch'],old['epoch']+1)

    def test_all_supported_cars_are_cockpits_and_pit_lane_is_allowed(self):
        from tire_reader.vehicle_dash import VEHICLES
        for car in VEHICLES:
            self.assertTrue(cockpit_context(cockpit(car_path=car,OnPitRoad=True,Speed=0)))


if __name__=='__main__':unittest.main()
