"""Result persistence is delegated; ordinary SDK samples do no result I/O."""
import unittest
from unittest.mock import Mock
from live_telemetry import LiveTelemetry


class LiveResultObserverTests(unittest.TestCase):
    def test_only_terminal_confirmed_non_replay_samples_offer_native_snapshots(self):
        observer=Mock()
        live=LiveTelemetry(clock=lambda:100.,result_observer=observer)
        live.run_id='synthetic-run'
        sdk={'WeekendInfo':{'SessionID':42},'DriverInfo':{'DriverCarIdx':0},'SessionInfo':{'Sessions':[]}}
        for sample in ({'session_state':3,'is_replay':False}, {'session_state':5,'is_replay':True},
                       {'session_state':5,'is_replay':None}):
            live._observe_final_result(sdk,sample)
        observer.assert_not_called()
        sample={'session_state':4,'is_replay':False,'session_num':0}
        live._observe_final_result(sdk,sample)
        observer.assert_called_once_with(sdk,game='iracing',sample=sample,run_id='synthetic-run')
        live._observe_final_result(sdk,sample)
        self.assertEqual(observer.call_count,1)

    def test_no_run_or_no_observer_never_reads_native_data(self):
        sdk=Mock()
        live=LiveTelemetry(result_observer=Mock())
        live._observe_final_result(sdk,{'session_state':5,'is_replay':False})
        live.run_id='run';live.result_observer=None
        live._observe_final_result(sdk,{'session_state':5,'is_replay':False})
        sdk.assert_not_called()

    def test_capture_failure_cannot_discard_live_sample(self):
        live=LiveTelemetry(result_observer=Mock(side_effect=RuntimeError('fixture failure')))
        live.run_id='run'
        with self.assertLogs('iRacingLive',level='WARNING'):
            live._observe_final_result({'WeekendInfo':{},'DriverInfo':{},'SessionInfo':{}},
                                      {'finish_status':1,'is_replay':False})


if __name__=='__main__':unittest.main()
