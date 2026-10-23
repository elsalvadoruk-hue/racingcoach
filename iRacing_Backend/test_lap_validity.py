"""iRacing lap validity: an incident voids a lap, a brief run over the kerb does not.

Synthetic channels only; no recordings or personal data.
"""
import os
import tempfile
import unittest

os.environ.setdefault('IRACING_TUNER_DB_PATH', os.path.join(tempfile.mkdtemp(), 'lap-validity.sqlite3'))

import numpy as np

from ibt_reader import IBTReader

RATE, LAP = 60, 6000     # 100 s laps at 60 Hz


def channels(surface_by_lap, incidents_by_lap=None, incident_log=True):
    laps = len(surface_by_lap)
    n = RATE * 100 * (laps + 1)
    lap = np.repeat(np.arange(1, laps + 2), LAP)[:n]
    pct = np.tile(np.linspace(0, .9999, LAP), laps + 1)[:n]
    surface = np.full(n, 3)
    incidents = np.zeros(n, dtype=np.int64)
    for index, values in enumerate(surface_by_lap):
        for value, at, length in values:
            start = index * LAP + int(at * LAP)
            surface[start:start + length] = value
    for index, penalty in (incidents_by_lap or {}).items():
        incidents[index * LAP + 3000] = penalty
    last = np.zeros(n)
    for index in range(laps + 1):
        last[index * LAP:] = 0 if index == 0 else 100. + index * .01   # a new published time each lap
    data = {'Lap': lap, 'LapDistPct': pct, 'SessionTime': np.arange(n) / RATE, 'PlayerTrackSurface': surface,
            'OnPitRoad': np.zeros(n, dtype=bool), 'LapLastLapTime': last, 'LapCompleted': lap - 1}
    if incident_log:
        data['PlayerIncidents'] = incidents
    return data


def statuses(data):
    reader = IBTReader.__new__(IBTReader)
    return [(lap.status, lap.invalid_reason) for lap in reader._build_lap_segments(data, RATE) if lap.lap_number <= 3]


class LapValidityTests(unittest.TestCase):
    def test_cumulative_incident_catches_a_missing_transient_without_voiding_later_laps(self):
        for event_log in (True, False):
            with self.subTest(event_log=event_log):
                data = channels([[], [], []], incident_log=event_log)
                count = np.zeros(len(data['Lap']))
                count[LAP + 3000:] = 1
                data['PlayerCarMyIncidentCount'] = count
                self.assertEqual(statuses(data), [('VALID', None), ('INVALID', 'incident during the lap'), ('VALID', None)])

    def test_count_increment_at_line_belongs_to_new_lap_and_reset_is_not_incident(self):
        data = channels([[], [], []])
        count = np.full(len(data['Lap']), 4.)
        count[LAP:] = 5.
        count[2 * LAP:] = 0.
        data['PlayerCarMyIncidentCount'] = count
        self.assertEqual(statuses(data), [('VALID', None), ('INVALID', 'incident during the lap'), ('VALID', None)])

    def test_zero_x_contact_still_counts_and_bad_counts_do_not_manufacture_incidents(self):
        data = channels([[], [], []], {0: 0x0107})
        count = np.zeros(len(data['Lap']))
        count[200:202] = [float('nan'), -1.]
        data['PlayerCarMyIncidentCount'] = count
        self.assertEqual(statuses(data), [('VALID', None)] * 3)

    def test_a_lap_counts_unless_the_sim_gave_an_incident(self):
        # Lap 1: a second over the kerb (60 samples, 1% of the lap) with no incident: counted, as in the sim.
        # Lap 2: an off with 1x (penalty byte 2). Lap 3: light contact, 0x (penalty byte 1): counted.
        data = channels([[(0, .5, 60)], [(0, .5, 60)], []], {1: 0x0202, 2: 0x0107})
        self.assertEqual(statuses(data), [('VALID', None), ('INVALID', 'incident during the lap'), ('VALID', None)])

    def test_approaching_the_pits_is_a_pit_lap_not_an_off(self):
        data = channels([[(2, .96, 240)], [], []])
        self.assertEqual(statuses(data)[0], ('INVALID', 'pit road samples present'))

    def test_a_recording_without_the_incident_log_keeps_the_surface_rule(self):
        data = channels([[(0, .5, 60)], [], []], incident_log=False)
        self.assertEqual(statuses(data)[0], ('INVALID', 'off-track samples present'))


if __name__ == '__main__':
    unittest.main()
