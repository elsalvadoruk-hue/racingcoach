"""Failure boundaries from the 2026-10-30 guidance handoff; synthetic inputs only."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from live_damage import DamageWatch
from live_energy import EnergyWatch
from lmu_irsdk import LmuIrsdk
from race_engineer import RaceEngineer
from race_radio import RaceRadio


def sample(at=9., lap=4, pct=.001, energy=2., **extra):
    return {'session_time': at, 'session_num': 0, 'session_state': 4, 'lap_number': lap,
            'distance_pct': pct, 'virtual_energy_pct': energy, 'on_track': True,
            'is_replay': False, 'on_pit_road': False, 'session_flags': 0,
            'speed_mps': 60., 'engine_warnings': 0, **extra}


def trained(line=.001):
    watch = EnergyWatch()
    for at in range(10):
        lap, part = divmod(at, 3)
        watch.ingest(sample(at, lap + 1, (line, .5, .99)[part], 20 - at * 2))
    return watch


def calls(view):
    found = []
    RaceEngineer()._energy_calls(view, lambda key, *args, **kwargs: found.append(key))
    return found


RACE = {'remaining_laps': 10., 'fuel_laps': 20.}


class EnergyValidityTests(unittest.TestCase):
    def test_missing_energy_cannot_generate_current_shortfall_calls(self):
        watch = trained()
        self.assertIn('ve_critical', calls(watch.view(RACE)))
        for at in (10., 11., 12.):
            watch.ingest(sample(at, energy=None))
            self.assertEqual(calls(watch.view(RACE)), [])
            self.assertNotIn('energy_pct', watch.view(RACE))
        self.assertEqual(list(watch.history), [6., 6., 6.])
        self.assertEqual((watch.last_valid_pct, watch.last_valid_at), (2., 9.))

    def test_line_zero_and_near_zero_learn_the_same_three_laps(self):
        exact, near = trained(0.), trained(.001)
        self.assertEqual(list(exact.history), [6., 6., 6.])
        self.assertEqual(exact.view(RACE), near.view(RACE))

    def test_zero_remaining_energy_is_valid_and_still_critical(self):
        watch = trained()
        watch.ingest(sample(9.1, energy=0.))
        self.assertEqual(watch.view(RACE)['energy_pct'], 0.)
        self.assertEqual(watch.view(RACE)['energy_laps'], 0.)
        self.assertIn('ve_critical', calls(watch.view(RACE)))

    def test_invalid_energy_and_missing_clock_are_not_current_values(self):
        for value in (None, True, False, -1., 101., float('nan'), float('inf')):
            with self.subTest(value=value):
                watch = trained()
                watch.ingest(sample(10., energy=value))
                self.assertEqual(watch.view(RACE), {})
        watch = trained()
        watch.ingest(sample(None))
        self.assertEqual(watch.view(RACE), {})

    def test_lookup_checks_source_age_and_does_not_mutate_the_rate(self):
        watch = trained()
        self.assertEqual(watch.view(RACE, now=11.01), {})
        self.assertEqual(watch.view(RACE, now=8.9), {})
        self.assertIn('ve_critical', calls(watch.view(RACE, now=9.1)))
        self.assertEqual(list(watch.history), [6., 6., 6.])

    def test_old_future_and_unknown_source_times_do_not_become_fresh_on_ingest(self):
        for source_time in (7., 11., None):
            with self.subTest(source_time=source_time):
                watch = trained()
                watch.ingest(sample(10., virtual_energy_sample_time=source_time))
                self.assertEqual(watch.view(RACE), {})
        watch = trained()
        watch.ingest(sample(10., virtual_energy_sample_time=9.5))
        self.assertEqual(watch.view(RACE, now=10.)['energy_sample_time'], 9.5)

    def test_recovery_uses_current_energy_without_learning_the_missing_interval(self):
        watch = trained()
        watch.ingest(sample(10., energy=None))
        watch.ingest(sample(11., pct=.5, energy=1.5))
        self.assertEqual(watch.view(RACE)['energy_pct'], 1.5)
        self.assertEqual(watch.view(RACE)['energy_pct_per_lap'], 6.)
        self.assertIn('ve_critical', calls(watch.view(RACE)))
        watch.ingest(sample(12., lap=5, energy=1.))
        self.assertEqual(list(watch.history), [6., 6., 6.])

    def test_refill_is_not_a_negative_consumption_lap(self):
        watch = trained()
        watch.ingest(sample(9.1, energy=80.))
        watch.ingest(sample(9.2, lap=5, energy=79.))
        self.assertEqual(watch.view(RACE)['energy_stint'], 1)
        self.assertEqual(list(watch.history), [6., 6., 6.])
        self.assertNotIn('ve_critical', calls(watch.view(RACE)))

    def test_caution_at_the_line_does_not_enter_a_clean_rate(self):
        watch = EnergyWatch()
        watch.ingest(sample(0., lap=1, pct=0., energy=20., session_flags=0x4000))
        watch.ingest(sample(1., lap=1, pct=.99, energy=15.))
        watch.ingest(sample(2., lap=2, pct=0., energy=14.))
        self.assertEqual(list(watch.history), [])

    def test_replay_and_explicitly_off_track_samples_cannot_drive_energy_calls(self):
        for extra in ({'is_replay': True}, {'on_track': False}):
            with self.subTest(extra=extra):
                watch = trained()
                watch.ingest(sample(10., **extra))
                self.assertEqual(watch.view(RACE), {})

    def test_clock_reversal_and_session_change_drop_cross_session_rates(self):
        for value in (sample(8.9), sample(10., session_num=1)):
            with self.subTest(value=value):
                watch = trained()
                watch.ingest(value)
                self.assertEqual(list(watch.history), [])
                self.assertEqual(calls(watch.view(RACE)), [])

    def test_clock_gap_cannot_complete_the_interrupted_lap(self):
        watch = trained()
        watch.ingest(sample(20., lap=5, energy=1.))
        self.assertEqual(list(watch.history), [6., 6., 6.])
        self.assertEqual(watch.view(RACE)['energy_pct'], 1.)

    def test_repeated_source_timestamp_cannot_complete_a_consumption_lap(self):
        watch = trained()
        watch.ingest(sample(9.1, pct=.9, energy=2., virtual_energy_sample_time=9.))
        watch.ingest(sample(9.2, lap=5, energy=1., virtual_energy_sample_time=9.))
        self.assertEqual(list(watch.history), [6., 6., 6.])


class LmuWarningMeaningTests(unittest.TestCase):
    def adapter(self):
        sdk = LmuIrsdk()
        sdk._car = lambda *args: ('synthetic', 'Synthetic')
        return sdk

    def frame(self, at=100., **extra):
        return dict(telemetry_et=at, scoring_et=at, vehicle_id=0,
                    session_key='synthetic-session', session_phase=5, session_type_code=10,
                    realtime=True, paused=False, control_code=0, in_pits=False,
                    overheating=False, parts_detached=False, speed_limiter_active=False, **extra)

    def test_lmu_flags_keep_their_own_meaning_through_sample_and_radio(self):
        from live_telemetry import CHANNELS
        for field, channel in (('parts_detached', 'lmu_parts_detached'), ('overheating', 'lmu_overheating')):
            with self.subTest(field=field):
                sdk, radio = self.adapter(), RaceRadio()
                for index, value in enumerate((False, True)):
                    raw = self.frame(100 + index * .1)
                    raw[field] = value
                    sdk._translate(raw, None)
                    current = {key: sdk[name] for key, name in CHANNELS.items()}
                    radio.ingest(current, 'synthetic', 1000 + index * .1, {'session_type': 'Race'})
                self.assertIs(current.get(channel), True)
                self.assertEqual(current.get('lmu_warning_sample_time'), 100.1)
                self.assertEqual(sdk['EngineWarnings'] & 0x101, 0)
                self.assertFalse({'water_temp', 'optional_repair'} & {e['key'] for e in radio.pending})

    def test_missing_lmu_flags_remain_unknown_but_limiter_still_works(self):
        from live_telemetry import CHANNELS
        sdk = self.adapter()
        raw = self.frame()
        raw.update(parts_detached=None, overheating=None, speed_limiter_active=True)
        sdk._translate(raw, None)
        self.assertIsNone(sdk[CHANNELS['lmu_parts_detached']])
        self.assertIsNone(sdk[CHANNELS['lmu_overheating']])
        self.assertIsNone(sdk[CHANNELS['lmu_warning_sample_time']])
        self.assertEqual(sdk['EngineWarnings'], 0x10)

    def test_iracing_water_temperature_and_optional_repair_calls_are_preserved(self):
        for bit, expected in ((1, 'water_temp'), (0x100, 'optional_repair')):
            with self.subTest(bit=bit):
                radio = RaceRadio()
                radio.ingest(sample(0., engine_warnings=0), 'iracing', 1000., {'session_type': 'Race'})
                radio.ingest(sample(.1, engine_warnings=bit), 'iracing', 1000.1, {'session_type': 'Race'})
                self.assertIn(expected, [event['key'] for event in radio.pending])

    def test_part_detachment_still_starts_the_existing_vehicle_comparison(self):
        watch = DamageWatch()
        watch.ingest(sample(0., lmu_parts_detached=False))
        watch.ingest(sample(.1, lmu_parts_detached=True))
        self.assertEqual(watch.contact_lap, 4)
        watch.contact_lap = None
        watch.ingest(sample(.2, lmu_parts_detached=True))
        self.assertIsNone(watch.contact_lap)
        watch.ingest(sample(.3, lmu_parts_detached=None))
        watch.ingest(sample(.4, lmu_parts_detached=True))
        self.assertIsNone(watch.contact_lap)


class LiveEnergyViewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='guidance-validity-')
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, IRACING_DATA_DIR=self.folder.name,
                         IRACING_TUNER_DB_PATH=str(Path(self.folder.name) / 'test.sqlite3'),
                         IRACING_AI_ENABLED='0', GARAGE61_AUTO_COLLECT='0')
        env.start()
        self.addCleanup(env.stop)

    def live(self):
        from live_telemetry import LiveTelemetry
        live = LiveTelemetry(clock=lambda: 100.)
        live.game = 'lmu'
        live.energy = trained()
        return live

    def test_pit_view_passes_the_snapshot_clock_to_energy_freshness_check(self):
        live = self.live()
        self.assertEqual(live._pit_view(sample(12.)).get('energy_pct'), None)
        self.assertEqual(live._pit_view(sample(9.1))['energy_pct'], 2.)

    def test_native_snapshot_keeps_the_observed_lmu_warning_fields(self):
        live = self.live()
        live.state = 'CONNECTED'
        live.samples.append({**sample(), 'wall_time': 100., 'lmu_parts_detached': True,
                             'lmu_overheating': False, 'lmu_warning_sample_time': 9.})
        view = live.overlay_state()['snapshot']
        self.assertIs(view.get('lmu_parts_detached'), True)
        self.assertIs(view.get('lmu_overheating'), False)
        self.assertEqual(view.get('lmu_warning_sample_time'), 9.)

    def test_real_tick_transmits_missing_energy_and_independent_lmu_flags(self):
        from live_telemetry import LiveTelemetry
        frames = []
        for index, energy in enumerate((.02, .02, None)):
            raw = LmuWarningMeaningTests().frame(100 + index * .1)
            raw.update(virtual_energy_raw=energy, parts_detached=index > 0,
                       overheating=index == 2, lap_distance_m=1000., track_length_m=5000.,
                       total_laps=3, speed_mps=60.)
            frames.append(raw)
        class Source:
            def open(self):
                return {}
            def read(self, **kwargs):
                return frames.pop(0)
        now = [1000.]
        sdk = LmuIrsdk(source_factory=Source, normalised=True, clock=lambda: now[0])
        sdk._car = lambda *args: ('synthetic', 'Synthetic')
        live = LiveTelemetry(clock=lambda: now[0])
        live._tick(sdk)
        live.energy = trained()
        now[0] += .1
        live._tick(sdk)
        self.assertEqual(live.race_view()['energy_pct'], 2.)
        now[0] += .1
        live._tick(sdk)
        view = live.race_view()
        self.assertNotIn('energy_pct', view)
        self.assertEqual(calls(view), [])
        current = live.overlay_state()['snapshot']
        self.assertIs(current['lmu_parts_detached'], True)
        self.assertIs(current['lmu_overheating'], True)
        self.assertEqual(current['lmu_warning_sample_time'], 100.2)
        self.assertFalse({'water_temp', 'optional_repair'} & {e['key'] for e in live.radio.pending})


if __name__ == '__main__':
    unittest.main()
