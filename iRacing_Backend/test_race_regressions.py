"""Race findings reproduced without touching personal data or cloud services."""
import asyncio
import copy
import unittest

import test_race_engineer as engineer_fixture
import test_race_radio as radio_fixture
from race_engineer import choose_role, measured_answer
from race_radio import STATIC_CALLS
from voice_coach import ReportRequest, Utterance
from voice_notes import VoiceNoteStore


class RadioRegressions(unittest.TestCase):
    setUp = radio_fixture.RaceRadioTests.setUp
    tick = radio_fixture.RaceRadioTests.tick
    wait = radio_fixture.RaceRadioTests.wait
    events = radio_fixture.RaceRadioTests.events
    keys = radio_fixture.RaceRadioTests.keys
    def test_sebring_lap_time_delayed_one_second_does_not_read_old_lap(self):
        self.tick(lap_completed=3)
        self.wait(.9)
        self.assertNotIn('lap_time', self.keys())
        self.tick(lap_last_s=103.)
        self.assertIn('1분 43.00초', next(e for e in self.events() if e['key'] == 'lap_time')['text'])

    def test_equal_lap_time_requires_current_lap_timer_reset(self):
        self.tick(lap_completed=3, lap_current_s=101.1)
        self.wait(.8)
        self.assertNotIn('lap_time', self.keys())
        self.tick(lap_current_s=.9)
        self.assertIn('lap_time', self.keys())

    def test_gentle_full_throttle_curve_allows_only_short_reports(self):
        self.tick(lap_completed=3, lap_last_s=103., throttle=1, steering_rad=.22)
        self.assertFalse(self.radio.view(self.now)['quiet'])
        self.assertTrue(self.radio.view(self.now)['brief_quiet'])
        self.wait(20)
        self.assertIn('lap_time', self.keys())
        self.tick(brake=.2)
        self.assertFalse(self.radio.view(self.now)['brief_quiet'])

    def test_optional_repair_and_low_fuel_still_work_on_player_final_lap(self):
        self.tick(engine_warnings=0x100)
        self.assertIn('optional_repair', self.keys())
        self.tick(session_state=5, fuel_l=.4)
        self.assertIn('fuel_low', self.keys())
        self.tick(session_flags=1)
        self.assertNotIn('fuel_low', self.keys())

    def test_low_fuel_follows_the_cars_own_use_and_is_said_twice_a_stint_at_most(self):
        calls, emit = [], self.radio.emit
        self.radio.emit = lambda key, *a, **k: (calls.append(key) if key == 'fuel_low' else None, emit(key, *a, **k))[1]
        said = lambda: calls
        self.tick(session_state=4, fuel_l=6., fuel_l_per_lap=8.4)
        self.tick(fuel_l=4.1)                                           # 4-minute multiclass car: 4.1 L is under half a lap
        self.assertEqual(len(said()), 1)
        for fuel in (3.5, 2., 1.):
            self.wait(70); self.tick(fuel_l=fuel)
        self.assertEqual(len(said()), 1)                                # not every minute
        self.wait(70); self.tick(fuel_l=.4)
        self.assertEqual(len(said()), 2)                                # nearly dry
        self.wait(70); self.tick(fuel_l=60.)                            # refuelled
        self.wait(70); self.tick(fuel_l=3.)
        self.assertEqual(len(said()), 3)

    def test_low_fuel_waits_for_half_a_lap_of_a_frugal_car_and_falls_back_before_a_lap_is_measured(self):
        self.tick(session_state=4, fuel_l=1.2, fuel_l_per_lap=2.)      # 1.2 L is more than half a lap of 2 L
        self.assertNotIn('fuel_low', self.keys())
        self.tick(fuel_l=.9)
        self.assertIn('fuel_low', self.keys())
        self.setUp()
        self.tick(session_state=4, fuel_l=.9)                            # no measured lap yet: the old 0.5 L limit
        self.assertNotIn('fuel_low', self.keys())
        self.tick(fuel_l=.4)
        self.assertIn('fuel_low', self.keys())

    def test_flags_use_phonetic_names_and_cached_spotter_only(self):
        for key, name in [('blue', '블루 플래그'), ('white', '화이트 플래그'), ('red', '레드 플래그'), ('repair', '레드 닷')]:
            self.assertTrue(all(name in text for text in STATIC_CALLS[key]))


class StrategyRegressions(unittest.TestCase):
    setUp = engineer_fixture.RaceTests.setUp
    view = engineer_fixture.RaceTests.view
    def test_shortfall_separate_from_reserve_and_actual_saving_target(self):
        view = self.view()
        self.assertEqual(view['fuel_finish_shortfall_l'], 3)
        self.assertEqual(view['fuel_reserve_l'], 1)
        self.assertAlmostEqual(view['fuel_target_l_per_lap'], 10/6.5, places=3)
        self.assertAlmostEqual(view['fuel_save_pct'], 23.1)
        answer = measured_answer('strategy', '연료 부족하지 않겠지?', view)
        for word in ('3.0리터', '4.0리터', '절약', '여유분'):
            self.assertIn(word, answer)

    def test_timer_expiration_does_not_drop_player_last_lap(self):
        self.race.update(state=4, total_laps=None, time_remaining_s=-1)
        self.assertEqual(self.view()['remaining_laps'], .5)
        self.race.update(state=5, total_laps=None, time_remaining_s=-10)
        self.wall.ingest(self.race, 'run', 101)
        self.assertEqual(self.view()['remaining_laps'], .5)
        self.race['cars'][1]['distance_pct'] = .75
        self.wall.ingest(self.race, 'run', 120)
        self.assertEqual(self.view()['remaining_laps'], .25)
        self.snapshot['session_flags'] = 1
        self.assertFalse(self.view()['active'])

    def test_flag_events_never_create_engineer_reports(self):
        self.snapshot['fuel_l'] = 25
        for flag in (8, 0x20, 0x2, 0x10, 0x1, 0x100000):
            self.race['flags'] = flag
            reports = self.wall.candidates(self.view(), self.snapshot, {'snapshot': self.snapshot})
            self.assertFalse(any(r['key'] in ('yellow', 'finish', 'repair') for r in reports))

    def test_ranked_rival_and_natural_question_routes(self):
        for text in ('지금 클래스 3위랑 거리 차이는', '데미지 리포트', '뒤에서 따라와?'):
            self.assertEqual(choose_role(text), 'race')
        answer = measured_answer('race', '클래스 3위랑 거리 차이는', self.view())
        self.assertIn('Driver 2', answer)
        self.assertIn('2.0초', answer)

    def test_pace_waits_for_actual_last_lap_time(self):
        race = copy.deepcopy(self.race)
        race['cars'][1]['lap_completed'] += 1
        self.wall.ingest(race, 'run', 101)
        self.assertEqual(list(self.wall.paces[1]), [100, 100, 100])
        race['cars'][1]['last_lap_s'] = 96
        self.wall.ingest(race, 'run', 102)
        self.assertEqual(list(self.wall.paces[1])[-3:], [100, 100, 96])


class IntegrationRegressions(unittest.TestCase):
    setUp = engineer_fixture.PitWallIntegrationTests.setUp
    def test_old_disconnected_memo_does_not_break_automatic_reports(self):
        self.store.save('old offline memo', {'connected': False, 'snapshot': None}, 'TEXT')
        report = asyncio.run(self.coach.report(ReportRequest(speak=False)))['report']
        self.assertIsNotNone(report)

    def test_damage_uses_repair_bits_without_ai(self):
        self.live.ingest({'track_id': '1', 'car_id': '2'}, {**self.sample, 'session_time': 101,
            'engine_warnings': 0x100, 'session_flags': 0, 'incidents': 4}, self.race)
        answer = asyncio.run(self.coach.utterance(Utterance(text='데미지 리포트', speak=False)))['note']
        self.assertEqual(answer['reply_source'], 'MEASURED')
        self.assertIn('선택 수리', answer['reply'])
        self.assertIn('4점', answer['reply'])
        self.assertNotIn('4개의 사고', answer['reply'])

    def test_delivery_records_survive_store_reopen(self):
        events = [{'event_id': 'run:lap', 'run_id': 'run', 'status': s} for s in ('started', 'interrupted')]
        self.store.record_radio(events)
        reopened = VoiceNoteStore(self.store.path)
        self.assertEqual([e['status'] for e in reopened.radio_log(run_id='run')], ['interrupted', 'started'])
