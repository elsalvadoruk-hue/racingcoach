"""LMU measured cold pressures, LMU advice opt-in, and the double-stint wear projection."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tire_reader.history import TireHistoryStore
from tire_reader.installed import LmuInstalledPressure
from tire_reader.lmu_source import LmuTireReader
from tire_reader.snapshot import TireSnapshot, WHEELS
from tire_reader.stint_wear import projection, wear_rates
from test_tire_advisor import snapshot
from test_tire_lmu_source import frame


def wheels(pressure, wear):
    return {w: (pressure, wear) for w in WHEELS}


class LmuInstalledTests(unittest.TestCase):
    def test_garage_and_new_tire_pressures_are_measured(self):
        tracker = LmuInstalledPressure()
        self.assertEqual(set(tracker.update('s', wheels(170., .9), in_garage=False, in_pits=False, on_track=True, lap=3)
                             ['installed_cold_kpa'].values()), {None})  # joined on track: unknown
        tracker = LmuInstalledPressure()
        tracker.update('s', wheels(150., 1.), in_garage=True, in_pits=True, on_track=False, lap=1)
        tracker.update('s', wheels(151., 1.), in_garage=True, in_pits=True, on_track=False, lap=1)
        fields = tracker.update('s', wheels(153., 1.), in_garage=False, in_pits=True, on_track=True, lap=1)
        self.assertEqual(set(fields['installed_cold_kpa'].values()), {151.})
        self.assertEqual(set(fields['installed_cold_source'].values()), {'garage_measured'})
        tracker.update('s', wheels(185., .7), in_garage=False, in_pits=False, on_track=True, lap=20)
        tracker.update('s', wheels(184., .7), in_garage=False, in_pits=True, on_track=True, lap=21)
        fields = tracker.update('s', {**wheels(184., .7), 'FL': (149., 1.)}, in_garage=False, in_pits=True, on_track=True, lap=21)
        self.assertEqual(fields['installed_cold_kpa']['FL'], 149.)
        self.assertEqual(fields['installed_cold_source']['FL'], 'pit_new_tire_measured')
        self.assertEqual((fields['installed_lap']['FL'], fields['installed_lap']['FR']), (21, 1))

    def test_lmu_reader_puts_measured_pressures_on_the_snapshot(self):
        now = [0.]
        raw = frame(in_garage_stall=True, control_code=0)
        source = Mock(); source.read.side_effect = lambda **kw: raw
        reader = LmuTireReader(source_factory=lambda: source, clock=lambda: now[0])
        self.addCleanup(reader.close)
        for t, garage in ((0., True), (1., True), (2., False)):
            now[0] = t; raw.update(telemetry_et=10. + t, scoring_et=10. + t, in_garage_stall=garage)
            row = reader.sample()
        snap = TireSnapshot.from_dict(row['tire_snapshot'])
        self.assertEqual(snap.installed_cold_kpa, {'FL': 160., 'FR': 161., 'RL': 162., 'RR': 163.})
        self.assertEqual(set(snap.installed_cold_source.values()), {'garage_measured'})


def store_stints(path, *rows):
    store = TireHistoryStore(path)
    for row in rows:
        store.db.execute("INSERT INTO tire_stints(run,data,coach_run) VALUES('r',?,'')", (json.dumps(row),))
    store.db.commit(); store.close()


class DoubleStintTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'tires-history.sqlite3'
        TireHistoryStore(self.path).close()

    def iracing_stint(self, wear, changed=False, laps=20):
        return {'game': 'iracing', 'car': 'mclaren720sgt3', 'track': 'track-layout',
                'conditions': {'last_lap': laps, 'installed_lap': dict.fromkeys(WHEELS, 1),
                               'installed_cold_source': dict.fromkeys(WHEELS, 'garage_start')},
                'pit_snapshot': {'tire_changed': dict.fromkeys(WHEELS, changed),
                                 'wheels': {w: {'wear_lmr': [wear + .01, wear, wear + .02]} for w in WHEELS}}}

    def test_iracing_rate_only_from_stops_that_measured_the_driven_tire(self):
        store_stints(self.path, self.iracing_stint(.8), self.iracing_stint(.5, changed=True))
        rates = wear_rates(self.path, 'iracing', 'mclaren720sgt3', 'track-layout')
        self.assertEqual(len(rates['FL']), 1)
        self.assertAlmostEqual(rates['FL'][0], .01)  # (1 - 0.8) over laps 1..20

    def test_projection_for_this_stint_and_one_more(self):
        store_stints(self.path, self.iracing_stint(.8))
        s = replace(snapshot(0.), lap=16, installed_lap=dict.fromkeys(WHEELS, 1),
                    installed_cold_source=dict.fromkeys(WHEELS, 'pit_tire_change'))
        result = projection(self.path, s, fuel_laps=5)
        fl = result['wheels']['FL']
        # 15 laps on the set (estimated 0.85 now), 5 to the stop (0.80), then another 20.
        self.assertEqual((fl['wear_now'], fl['wear_at_stop'], fl['wear_after_next'], fl['stint_laps']), (.85, .8, .6, 20))
        self.assertEqual((fl['wear_now_basis'], result['status'], result['grip_confirmed']), ('estimated_from_rate', 'ready', False))

    def test_lmu_uses_live_wear_and_withholds_without_history_or_age(self):
        s = replace(snapshot(0.), game='lmu', lap=11, installed_lap=dict.fromkeys(WHEELS, 1),
                    installed_cold_source=dict.fromkeys(WHEELS, 'garage_measured'),
                    wheels={w: replace(v, wear_fraction=.9) for w, v in snapshot(0.).wheels.items()})
        result = projection(self.path, s, fuel_laps=None)
        self.assertEqual((result['wheels']['RR']['wear_now'], result['wheels']['RR']['wear_after_next']), (.9, .8))
        self.assertEqual(result['wheels']['RR']['wear_now_basis'], 'live')
        unknown = replace(s, installed_lap={})
        self.assertEqual(projection(self.path, unknown, fuel_laps=None)['status'], 'withheld')
        self.assertEqual(projection(self.path, replace(snapshot(0.), lap=5), fuel_laps=3)['reason'], 'no_wear_rate')


class LmuAdviceSettingTests(unittest.TestCase):
    def test_lmu_advice_needs_the_open_setup_confirmation(self):
        from tire_overlay_control import TireOverlayController
        with tempfile.TemporaryDirectory() as temp:
            lmu = TireOverlayController(Path(temp) / 'overlay/lmu/tires.json', game='lmu')
            garage = replace(snapshot(0.), game='lmu', active=False, in_garage=True)
            self.assertIsNone(lmu._advice_kind(garage))
            self.assertIsNone(lmu._fixed_setup(garage))
            self.assertTrue(lmu.update(lmu_open_setup=True)['lmu_open_setup'])
            self.assertEqual((lmu._advice_kind(garage), lmu._fixed_setup(garage)), ('garage', False))
            self.assertTrue(json.loads((Path(temp) / 'overlay/lmu/tires.json').read_text())['lmu_open_setup'])
            iracing = TireOverlayController(Path(temp) / 'overlay/iracing/tires.json')
            with self.assertRaises(ValueError):
                iracing.update(lmu_open_setup=True)


if __name__ == '__main__':
    unittest.main()
