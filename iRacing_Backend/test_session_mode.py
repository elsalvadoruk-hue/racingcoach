import unittest

from analysis_support import session_context, suggested_session_mode


def event(official, race_time='14400.0000 sec', race_laps='unlimited'):
    """A race event as an IBT header lists it; `official` marks the sessions already run."""
    return {'SessionInfo': {'Sessions': [
        {'SessionNum': 0, 'SessionType': 'Practice', 'SessionTime': '600.0000 sec', 'SessionLaps': 'unlimited', 'ResultsOfficial': int(official > 0)},
        {'SessionNum': 1, 'SessionType': 'Lone Qualify', 'SessionTime': '1800.0000 sec', 'SessionLaps': 'unlimited', 'ResultsOfficial': int(official > 1)},
        {'SessionNum': 2, 'SessionType': 'Race', 'SessionTime': race_time, 'SessionLaps': race_laps, 'ResultsOfficial': int(official > 2)}]}}


class SessionModeTests(unittest.TestCase):
    def mode(self, info, lap_time_s=None):
        context = session_context(info)
        return suggested_session_mode(context['session_type'], context['session_duration_s'], context['session_laps_limit'], lap_time_s)

    def test_the_session_being_driven_is_the_first_one_not_yet_official(self):
        self.assertEqual(session_context(event(0))['session_type'], 'Practice')
        self.assertEqual(session_context(event(1))['session_type'], 'Lone Qualify')
        race = session_context(event(2))
        self.assertEqual((race['session_type'], race['session_duration_s'], race['session_laps_limit']), ('Race', 14400., None))
        self.assertEqual(session_context(event(3))['session_type'], 'Race')   # recorded after the flag: still the race

    def test_qualifying_practice_and_race_length_choose_the_mode(self):
        self.assertEqual(self.mode(event(0)), 'BASELINE_3LAP')
        self.assertEqual(self.mode(event(1)), 'QUALIFYING')
        self.assertEqual(self.mode(event(2)), 'ENDURANCE')                                   # four hours
        self.assertEqual(self.mode(event(2, '3600.0000 sec')), 'ENDURANCE')                  # an hour counts
        self.assertEqual(self.mode(event(2, '2700.0000 sec')), 'BASELINE_3LAP')              # 45 minutes
        self.assertEqual(self.mode(event(2, '3540.0000 sec')), 'BASELINE_3LAP')              # 59 minutes
        self.assertEqual(self.mode({'SessionInfo': {'Sessions': [{'SessionType': 'Offline Testing', 'SessionTime': 'unlimited'}]}}), 'BASELINE_3LAP')

    def test_a_lap_limited_race_is_timed_by_the_estimated_lap(self):
        self.assertEqual(self.mode(event(2, 'unlimited', 40), lap_time_s=100.), 'ENDURANCE')      # 40 laps of 100 s
        self.assertEqual(self.mode(event(2, 'unlimited', 20), lap_time_s=100.), 'BASELINE_3LAP')
        self.assertEqual(self.mode(event(2, 'unlimited', 40)), 'BASELINE_3LAP')                   # length unknown: not called endurance

    def test_nothing_is_suggested_without_session_information(self):
        self.assertIsNone(self.mode({}))
        self.assertIsNone(self.mode({'SessionInfo': {'Sessions': []}}))
        self.assertIsNone(suggested_session_mode(None))


if __name__ == '__main__':
    unittest.main()
