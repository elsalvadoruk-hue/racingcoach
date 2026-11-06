"""Cold pressure advice from evidenced stints, the advice voice calls and the controller wiring."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tire_reader import pressure_advice as advice
from tire_reader.advisor import ADVICE_PHRASES, TireAdvisor
from tire_reader.history import TireHistoryStore
from tire_reader.snapshot import WHEELS
from test_tire_advisor import snapshot

CAR, TRACK = 'mclaren720sgt3', 'iracing-track-1-gp'
ATM = advice.ATM_KPA


def stint(cold, hot, track_temp, *, laps=4, session='Race', source='garage_start'):
    return {'game': 'iracing', 'car': CAR, 'track': TRACK,
            'conditions': {'track_temp_c': track_temp, 'session_types': [session],
                           'installed_cold_kpa': dict.fromkeys(WHEELS, cold),
                           'installed_cold_source': dict.fromkeys(WHEELS, source)},
            'eligible_laps': [{'lap': n, **{f'{w}_kpa': hot for w in WHEELS}} for n in range(laps)]}


class Database:
    def __init__(self, test):
        temp = tempfile.TemporaryDirectory(); test.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'tires-history.sqlite3'
        TireHistoryStore(self.path).close()

    def stints(self, *rows):
        store = TireHistoryStore(self.path)
        for row in rows:
            store.db.execute("INSERT INTO tire_stints(run,data,coach_run) VALUES('r',?,'')", (json.dumps(row),))
        store.db.commit(); store.close()

    def observations(self, values):
        store = TireHistoryStore(self.path)
        for kpa, state in values:
            data = {'wheels': {w: {'pressure_kpa': kpa, 'pressure_state': state} for w in WHEELS}}
            store.db.execute("INSERT INTO tire_observations(created,run,game,car,track,lap,t,data,coach_run) "
                             "VALUES(0,'r','iracing',?,?,1,0,?,'')", (CAR, TRACK, json.dumps(data)))
        store.db.commit(); store.close()

    def laps(self, installed, rows):
        store = TireHistoryStore(self.path)
        for lap, hot, track in rows:
            data = {'game': 'iracing', 'car': CAR, 'track': TRACK,
                    'wheels': {w: {'pressure_kpa': {'median': hot}} for w in WHEELS},
                    'conditions': {'installed_cold_kpa': installed, 'track_temp_c': track, 'session_types': ['Race']}}
            store.db.execute("INSERT INTO tire_laps(run,lap,eligible,data,coach_run) VALUES('r',?,1,?,'')", (lap, json.dumps(data)))
        store.db.commit(); store.close()


def ask(db, **overrides):
    values = dict(game='iracing', car=CAR, track=TRACK, targets={'pressure_low_bar': 1.8, 'pressure_high_bar': 1.9},
                  fixed_setup=False, track_temp=30., kind='garage')
    values.update(overrides)
    return advice.recommendation(db.path, **values)


class AdviceTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(self)

    def test_fixed_or_unknown_setup_and_missing_target_or_evidence_withhold(self):
        self.db.stints(stint(160., 185., 30.))
        self.assertEqual(ask(self.db, fixed_setup=True)['reason'], 'fixed_or_unknown_setup')
        self.assertEqual(ask(self.db, fixed_setup=None)['reason'], 'fixed_or_unknown_setup')
        self.assertEqual(ask(self.db, targets={})['reason'], 'no_target_range')
        self.assertEqual(ask(self.db, car='bmwm4gt3')['reason'], 'no_evidence')

    def test_one_stint_uses_its_ratio_with_the_default_slope(self):
        self.db.stints(stint(160., 185., 30.))
        result = ask(self.db, track_temp=34.)
        ratio = (185. + ATM) / (160. + ATM) + advice.DEFAULT_SLOPE * 4
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['wheels']['FL']['model'], 'nearest_with_default_slope')
        self.assertAlmostEqual(result['wheels']['FL']['cold_kpa'], (185. + ATM) / ratio - ATM, places=1)
        self.assertEqual((result['target_kpa'], result['target_source']), (185., 'driver_range'))

    def test_several_stints_over_a_temperature_range_are_fitted(self):
        # True model: ratio = 1.08 + 0.002 * (T - 30); each stint ran 160 kPa cold.
        rows = []
        for t in (26., 30., 34.):
            ratio = 1.08 + .002 * (t - 30)
            rows.append(stint(160., (160. + ATM) * ratio - ATM, t))
        self.db.stints(*rows)
        result = ask(self.db, track_temp=32.)
        self.assertEqual(result['wheels']['RR']['model'], 'fitted')
        expected = (185. + ATM) / (1.08 + .002 * 2) - ATM
        self.assertAlmostEqual(result['wheels']['RR']['cold_kpa'], expected, places=1)
        self.assertEqual(result['wheels']['RR']['track_temp_range_c'], [26., 34.])

    def test_only_evidenced_race_or_practice_stints_count(self):
        self.db.stints(stint(150., 190., 30., session='Open Qualify'), stint(150., 190., 30., source='pit_unconfirmed'),
                       stint(150., 190., 30., laps=2), stint(150., 190., 30., source=None))
        self.assertEqual(ask(self.db)['reason'], 'no_evidence')

    def test_warns_below_the_lowest_pressure_ever_used(self):
        self.db.stints(stint(160., 200., 30.))
        self.assertEqual(ask(self.db)['wheels']['FL']['warning'], 'below_lowest_used')

    def test_dash_colors_give_a_target_when_no_range_is_saved(self):
        self.db.stints(stint(160., 185., 30.))
        self.db.observations([(170., 'low')] * 5 + [(180., 'ok')] * 20 + [(190., 'ok')] * 20 + [(200., 'high')] * 5)
        result = ask(self.db, targets={})
        self.assertEqual(result['target_source'], 'dash_color')
        self.assertEqual(result['target_kpa'], 185.)
        low, high, _ = advice.learned_window(self.db.path, CAR)
        self.assertEqual((low, high), (175., 195.))

    def test_next_pit_puts_the_current_tire_set_first(self):
        self.db.stints(stint(160., 200., 20.))  # an old, colder session
        installed = dict.fromkeys(WHEELS, 165.)
        self.db.laps(dict.fromkeys(WHEELS, 150.), [(2, 170., 30.)])  # earlier set: ignored
        self.db.laps(installed, [(5, 188., 30.), (6, 189., 30.), (7, 190., 30.)])
        result = ask(self.db, kind='next_pit', installed=installed, installed_sources=dict.fromkeys(WHEELS, 'pit_tire_change'))
        wheel = result['wheels']['FL']
        self.assertEqual((wheel['stints'], wheel['model']), (2, 'nearest_with_default_slope'))
        ratio = (189. + ATM) / (165. + ATM)  # nearest stint is the current set at 30 °C
        self.assertAlmostEqual(wheel['cold_kpa'], (185. + ATM) / ratio - ATM, places=1)


class AdviceVoiceTests(unittest.TestCase):
    def test_garage_call_waits_for_the_garage_and_is_said_once(self):
        now = [0.]
        advisor = TireAdvisor(clock=lambda: now[0])
        advisor.offer('advice_garage', 'session', garage=True)
        event = advisor.current(0.)
        self.assertEqual(event['text'], ADVICE_PHRASES['advice_garage'])
        self.assertFalse(event['quiet'])  # no garage observation yet
        garage = replace(snapshot(0.), active=False, in_garage=True)
        advisor.observe(garage.to_dict(), enabled=True)
        self.assertTrue(advisor.current(0.)['quiet'])
        self.assertTrue(advisor.ack(event['id'], 0.))
        advisor.offer('advice_garage', 'session', garage=True)
        self.assertIsNone(advisor.current(1.))
        advisor.offer('advice_garage', 'next-session', garage=True)
        self.assertIsNotNone(advisor.current(1.))
        self.assertIsNone(advisor.current(62.))  # expires after a minute


class ControllerAdviceTests(unittest.TestCase):
    def test_ready_garage_advice_reaches_status_window_and_voice(self):
        from test_tire_overlay_control import Child
        from tire_overlay_control import TireOverlayController
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'overlay/iracing/tires.json'
        child = Child()
        controller = TireOverlayController(path, popen=Mock(return_value=child))
        self.addCleanup(controller.close)
        controller.update(enabled=True, voice_enabled=True)
        db = path.with_name('tires-history.sqlite3')
        TireHistoryStore(db).close()
        store = TireHistoryStore(db)
        row = stint(160., 185., 30.); row['car'] = 'mclaren720sgt3'; row['track'] = 'track-layout'
        store.db.execute("INSERT INTO tire_stints(run,data,coach_run) VALUES('r',?,'')", (json.dumps(row),)); store.db.commit(); store.close()
        controller._targets().save('mclaren720sgt3', {'pressure_low_bar': 1.8, 'pressure_high_bar': 1.9})
        garage = replace(snapshot(0.), active=False, in_garage=True, fixed_setup=False, track_temp_c=30.)
        controller._compute_advice(garage, 'garage')
        status = controller.status()
        self.assertEqual((status['advice']['status'], status['advice']['kind']), ('ready', 'garage'))
        written = [json.loads(call.args[0]) for call in child.stdin.write.call_args_list]
        sent = [line for line in written if line.get('action') == 'advice']
        self.assertEqual(sent[-1]['values']['FL'], f"{status['advice']['wheels']['FL']['cold_kpa'] / 100:.2f}")
        self.assertEqual(controller.advisor.advice_event['key'], 'advice_garage')
        self.assertEqual(controller._advice_kind(garage), 'garage')
        self.assertIsNone(controller._advice_kind(replace(garage, in_garage=False, active=False)))


if __name__ == '__main__':
    unittest.main()
