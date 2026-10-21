"""Raw LMU energy capture before any unit conversion or spoken decisions."""
import json
from pathlib import Path
import tempfile
import unittest

from lmu_frame import normalise
from lmu_journal import FrameJournal
from race_engineer import ENERGY_CALLS_ENABLED


class RawEnergyTests(unittest.TestCase):
    def test_raw_values_are_preserved_without_unit_conversion(self):
        for raw in (0, .75, 75., 125.):
            with self.subTest(raw=raw):
                self.assertEqual(normalise({'virtual_energy_raw': raw})['virtual_energy_raw'], raw)

    def test_missing_or_invalid_raw_values_remain_unknown(self):
        self.assertIsNone(normalise({})['virtual_energy_raw'])
        for raw in (None, True, '75', float('nan'), float('inf')):
            with self.subTest(raw=raw):
                self.assertIsNone(normalise({'virtual_energy_raw': raw})['virtual_energy_raw'])

    def test_diagnostic_journal_keeps_raw_values_and_calls_are_on(self):
        self.assertTrue(ENERGY_CALLS_ENABLED)
        with tempfile.TemporaryDirectory() as folder:
            journal = FrameJournal(folder, clock=lambda: 0.)
            journal.start()
            for raw in (80., 72., 64.):
                journal.write(normalise({'virtual_energy_raw': raw}))
            status = journal.stop()
            rows = [json.loads(line) for line in Path(status['path']).read_text(encoding='utf-8').splitlines()]
            self.assertEqual([row['snapshot']['virtual_energy_raw'] for row in rows if row['type'] == 'SNAPSHOT'],
                             [80., 72., 64.])


if __name__ == '__main__':
    unittest.main()


from live_energy import EnergyWatch
from lmu_irsdk import virtual_energy_pct
import race_engineer
import radio_phrases


def drive(watch, uses, start=100., time=0., lap=1, extra=None):
    """One sample at the line and one mid-lap per lap, 1 s apart in session time steps."""
    value = start
    for use in uses:
        for pct in (.01, .5, .99):
            sample = {'lap_number': lap, 'distance_pct': pct, 'session_time': time, 'on_pit_road': False,
                      'is_replay': False, 'session_flags': 0, 'virtual_energy_pct': value - use * (pct - .01)}
            sample.update((extra or {}).get(lap, {}))
            watch.ingest(sample)
            time += 1
        value -= use
        lap += 1
    watch.ingest({'lap_number': lap, 'distance_pct': .01, 'session_time': time, 'on_pit_road': False,
                  'is_replay': False, 'session_flags': 0, 'virtual_energy_pct': value})
    return watch


class EnergyUnitTests(unittest.TestCase):
    def test_the_live_fraction_becomes_percent_and_anything_else_is_unknown(self):
        self.assertEqual(virtual_energy_pct(.425), 42.5)
        self.assertEqual(virtual_energy_pct(1), 100.)
        for raw in (None, -.1, 1.5, 75., float('nan'), True):
            with self.subTest(raw=raw):
                self.assertIsNone(virtual_energy_pct(raw))


class EnergyWatchTests(unittest.TestCase):
    def test_five_clean_laps_give_the_rate_laps_left_and_shortfall(self):
        watch = drive(EnergyWatch(), [3.0, 3.2, 3.4, 3.0, 3.4])
        view = watch.view({'remaining_laps': 30, 'fuel_laps': 40})
        self.assertAlmostEqual(view['energy_pct_per_lap'], 3.2)
        self.assertAlmostEqual(view['energy_pct'], 84.0)
        self.assertAlmostEqual(view['energy_laps'], 84 / 3.2, places=2)
        self.assertAlmostEqual(view['energy_finish_shortfall_pct'], 30 * 3.2 - 84, places=1)
        self.assertAlmostEqual(view['energy_target_pct_per_lap'], 84 / 30, places=2)
        self.assertTrue(view['energy_limits_stint'])

    def test_pit_yellow_refill_and_idle_laps_are_left_out(self):
        watch = drive(EnergyWatch(), [3, 3, .1, 3, 3], extra={2: {'session_flags': 0x4000}, 4: {'on_pit_road': True}})
        self.assertEqual(list(watch.history), [3.0, 3.0])
        self.assertIsNone(watch.view({'remaining_laps': 10})['energy_pct_per_lap'])

    def test_fewer_than_three_laps_say_nothing(self):
        view = drive(EnergyWatch(), [3, 3]).view({'remaining_laps': 40})
        self.assertIsNone(view['energy_laps'])
        self.assertIsNone(view['energy_target_pct_per_lap'])


class EnergyCallTests(unittest.TestCase):
    def calls(self, view):
        engineer = race_engineer.RaceEngineer()
        reports = []
        def add(key, role, text=None, priority=1, **kwargs):
            reports.append(key)
        engineer._energy_calls(view, add)
        return reports

    def test_calls_follow_the_measured_numbers(self):
        short = {'energy_pct': 20., 'energy_laps': 3.5, 'energy_pct_per_lap': 5.7, 'energy_finish_shortfall_pct': 8.,
                 'energy_target_pct_per_lap': 5.2, 'energy_limits_stint': True, 'energy_last_lap_pct': 5.6,
                 'energy_laps_counted': 6, 'remaining_laps': 5}
        self.assertEqual(set(self.calls(short)), {'ve_laps_left', 've_limits_stint', 've_half', 've_save', 've_lap'})
        self.assertIn('ve_critical', self.calls({**short, 'energy_laps': .6}))
        self.assertIn('ve_need_stop', self.calls({**short, 'energy_target_pct_per_lap': 3.}))
        self.assertEqual(self.calls({'energy_pct': 80., 'energy_laps': None, 'energy_pct_per_lap': None}), [])

    def test_iracing_or_switched_off_energy_never_reaches_candidates(self):
        engineer = race_engineer.RaceEngineer()
        calls = []
        engineer._energy_calls = lambda view, add: calls.append(view.get('game'))
        view = {'active': True, 'game': 'iracing', 'fuel_laps': None, 'fuel_to_add_l': None}
        snapshot = {'session_time': 10., 'on_pit_road': False}
        engineer.candidates(view, snapshot, {})
        engineer.candidates({**view, 'game': 'lmu'}, snapshot, {})
        self.assertEqual(calls, ['lmu'] if race_engineer.ENERGY_CALLS_ENABLED else [])


class EnergyPhraseTests(unittest.TestCase):
    def test_every_energy_sentence_has_no_comma_and_ends_after_its_number(self):
        keys = [key for key in radio_phrases.PHRASES if key.startswith('ve_')]
        self.assertEqual(len(keys), 10)
        for key in keys:
            role, templates = radio_phrases.PHRASES[key]
            self.assertEqual(role, 'strategy')
            self.assertGreaterEqual(len(templates), 2)
            for template in templates:
                self.assertNotIn(',', template)
                if '{' in template:
                    self.assertRegex(template, r'\}(입니다)?\.$')
