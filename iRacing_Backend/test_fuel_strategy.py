import copy
import unittest
from collections import deque

from race_engineer import RaceEngineer, fuel_advice, measured_answer, tank_capacity
from test_race_engineer import race_fixture

LAP_S, USE, LAPS = 480., 13.8, 28


class EnduranceFuelTests(unittest.TestCase):
    """A 28-lap race on an 8-minute lap with a 98-litre tank: seven laps to a stint."""

    def setUp(self):
        self.race = race_fixture()
        self.race.update(total_laps=LAPS, tank_capacity_l=tank_capacity({'DriverCarFuelMaxLtr': 100., 'DriverCarMaxFuelPct': .98}))
        self.wall = RaceEngineer()
        self.wall.ingest(self.race, 'endurance', 0)
        self.wall.paces = {i: deque([LAP_S] * 3) for i in range(3)}

    def at(self, lap, pct, fuel, **snapshot):
        race = copy.deepcopy(self.race)
        for car in race['cars']:
            car.update(lap_completed=lap, distance_pct=pct if car['idx'] == 1 else (pct + .3 * (1 - car['idx'])) % 1)
        self.wall.race = race
        return {'session_time': (lap + pct) * LAP_S, 'run_id': 'endurance', 'lap_number': lap + 1, 'distance_pct': pct,
                'fuel_l': fuel, 'speed_mps': 60., 'brake': 0., 'steering_rad': 0., 'on_pit_road': False,
                'session_flags': 0, 'speech_window_s': 20, **snapshot}

    def stint(self, first_lap, fuel, laps, **snapshot):
        """Drive whole laps and return every report the driver would hear, as (key, lap, text)."""
        heard = []
        for step in range(laps * 50):
            lap, pct = first_lap + step // 50, step % 50 / 50
            s = self.at(lap, pct, fuel - USE * step / 50, **snapshot)
            view = self.wall.view(s, USE, 3)
            report = self.wall.select(self.wall.candidates(view, s, {}), s, 'all', cached_audio=True)
            if report and report['engineer'] == 'strategy':
                heard.append((report['key'], lap, report.get('speech_text') or report['spoken_text']))
                self.wall.delivered(report, s['session_time'])
        return heard

    def test_the_fuel_limit_of_the_series_sets_the_tank(self):
        self.assertAlmostEqual(tank_capacity({'DriverCarFuelMaxLtr': 100., 'DriverCarMaxFuelPct': .98}), 98.)
        self.assertEqual(tank_capacity({'DriverCarFuelMaxLtr': 120.}), 120.)
        self.assertIsNone(tank_capacity({}))

    def test_a_stint_is_silent_until_the_stop_is_near(self):
        heard = self.stint(0, 98., 6, pit_sv_fuel=98., pit_sv_flags=0x10)
        keys = [key for key, _, _ in heard]
        self.assertNotIn('fuel_short', keys)        # 300 litres short is not news in a 4-hour race
        self.assertNotIn('pit_soon', keys)          # five laps are forty minutes here
        self.assertNotIn('pit_fuel_check', keys)    # the tank is set to full already
        self.assertEqual(keys, ['pit_two'])
        self.assertEqual(heard[0][1], 4)            # with two laps and a bit in the tank

    def test_the_in_lap_is_called_twice_and_never_from_the_pit_lane(self):
        self.stint(0, 98., 6)
        heard = self.stint(6, 98. - 6 * USE, 1)     # 15 litres at the line: one lap and no more
        self.assertEqual([key for key, _, _ in heard], ['fuel_critical', 'fuel_critical'])
        s = self.at(6, .98, 1., on_pit_road=True)
        self.assertEqual([r['key'] for r in self.wall.candidates(self.wall.view(s, USE, 3), s, {}) if r['engineer'] == 'strategy'], [])

    def test_a_low_fuel_setting_is_told_as_a_full_tank_not_as_hundreds_of_litres(self):
        heard = self.stint(0, 98., 6, pit_sv_fuel=60., pit_sv_flags=0x10)
        checks = [text for key, _, text in heard if key == 'pit_fuel_check']
        self.assertEqual(checks, ['급유 설정은 60리터입니다. 가득 채워야 합니다.'])   # once, although the litres keep changing
        view = self.wall.view(self.at(5, .5, 21.), USE, 3)
        self.assertEqual((view['stops_needed'], view['fuel_next_stop_full'], view['fuel_next_stop_l']), (4, True, 77.))
        answer = measured_answer('strategy', '연료 끝까지 돼?', view)
        self.assertIn('급유가 약 4번 필요합니다. 다음 피트에서는 가득 채우세요.', answer)
        self.assertNotIn('부족', answer)
        self.assertIsNone(view['fuel_save_target_l_per_lap'])   # no per-lap "target" of a few litres

    def test_the_last_stop_names_its_litres_and_a_small_shortage_is_said_once(self):
        # Four laps to go with 15 litres: the splash is 4.5 laps of fuel less those 15.
        s = self.at(24, 0., 15., pit_sv_fuel=30., pit_sv_flags=0x10)
        reports = {r['key']: r for r in self.wall.candidates(self.wall.view(s, USE, 3), s, {})}
        self.assertEqual(reports['pit_fuel_check']['speech_text'], '급유 설정은 30리터입니다. 47리터 넣어야 합니다.')
        # After the stop: 4 laps on 53 litres is a 4 percent saving, which the driver can act on.
        heard = self.stint(24, 53., 4)
        self.assertEqual([key for key, _, _ in heard][:1], ['fuel_short'])
        self.assertEqual(sum(key == 'fuel_short' for key, _, _ in heard), 1)
        self.assertIn('절약', fuel_advice(self.wall.view(self.at(24, 0., 53.), USE, 3)))

    def test_the_pit_call_adds_where_the_stop_puts_the_car_as_a_second_sentence(self):
        s = self.at(4, .6, 31.)   # two laps and a bit in the tank
        view = self.wall.view(s, USE, 3)
        plain = {r['key']: r for r in self.wall.candidates(view, s, {})}['pit_two']
        self.assertNotIn('speech_parts', plain)            # no pit loss entered: nothing is guessed
        report = {r['key']: r for r in self.wall.candidates({**view, 'rejoin_class_position_estimate': 14}, s, {})}['pit_two']
        self.assertEqual(report['speech_parts'], ['두 랩 안에 피트합니다.', '피트하면 클래스 14위로 나옵니다.'])
        self.assertEqual(report['speech_text'], ' '.join(report['speech_parts']))

    def test_the_tyre_plan_is_its_own_call_near_the_stop(self):
        s = self.at(4, .6, 31.)
        view = {**self.wall.view(s, USE, 3), 'tyre_decision': 'keep', 'tyre_parts': [['tyre_keep', {}], ['tyre_setting_on', {}]]}
        plan = {r['key']: r for r in self.wall.candidates(view, s, {})}['tyre_plan']
        self.assertEqual(plan['speech_parts'], ['타이어는 바꾸지 않습니다.', '지금 설정은 타이어 교체입니다.'])
        self.assertEqual((plan['engineer'], plan['priority']), ('strategy', 2))
        alone = {r['key']: r for r in self.wall.candidates({**view, 'tyre_decision': 'change', 'tyre_parts': [['tyre_change', {}]]}, s, {})}['tyre_plan']
        self.assertEqual((alone['speech_text'], 'speech_parts' in alone), ('타이어도 바꿉니다.', False))
        early = self.at(2, .1, 70.)   # five laps of fuel left: not yet
        self.assertNotIn('tyre_plan', {r['key'] for r in self.wall.candidates({**self.wall.view(early, USE, 3), 'tyre_parts': view['tyre_parts']}, early, {})})

    def test_a_thin_reserve_is_mentioned_once(self):
        heard = self.stint(24, 58., 4)   # enough to finish, not the half-lap reserve
        self.assertEqual([key for key, _, _ in heard], ['fuel_reserve'])


if __name__ == '__main__':
    unittest.main()
