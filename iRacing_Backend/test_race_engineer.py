"""Synthetic races only: no SDK, personal database, credentials or cloud calls."""
import asyncio
import base64
from collections import deque
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from live_telemetry import LiveTelemetry
from race_engineer import RaceEngineer, ROLES, choose_role, role_voice, read_race, measured_answer
from voice_coach import VoiceCoach, ReportRequest, Utterance, register_voice_routes
from voice_notes import VoiceNoteStore, recording_key, review_notes
from voice_context import recent_conversation


def race_fixture():
    def car(idx, position, distance, pace, class_id=10):
        return {'idx': idx, 'name': f'Driver {idx}', 'class_id': class_id, 'position': position,
                'class_position': position, 'lap_completed': 3, 'distance_pct': distance,
                'last_lap_s': pace, 'on_pit_road': False, 'track_surface': 3}
    return {'session_type': 'Race', 'state': 4, 'flags': 0, 'total_laps': 10,
            'time_remaining_s': None, 'player_idx': 1, 'tank_capacity_l': 30,
            'cars': [car(0, 1, .8, 99), car(1, 2, .5, 100), car(2, 3, .48, 98)]}


class RaceTests(unittest.TestCase):
    def setUp(self):
        self.race = race_fixture()
        self.wall = RaceEngineer()
        self.wall.ingest(self.race, 'run', 100)
        self.wall.paces = {0: deque([99, 99, 99]), 1: deque([100, 100, 100]), 2: deque([98, 98, 98])}
        self.snapshot = {'session_time': 100, 'run_id': 'run', 'lap_number': 4, 'distance_pct': .5,
                         'fuel_l': 10, 'speed_mps': 50, 'brake': 0, 'steering_rad': 0, 'on_pit_road': False}

    def view(self, **kwargs):
        return self.wall.view(self.snapshot, 2, 3, **kwargs)

    def test_actual_race_fuel_and_reserve(self):
        view = self.view()
        self.assertEqual(view['remaining_laps'], 6.5)
        self.assertEqual(view['fuel_to_add_l'], 4)
        self.assertIn('4.0', measured_answer('strategy', '연료 끝까지 돼?', view))
        self.assertEqual(self.view(reserve_laps=1)['fuel_to_add_l'], 5)
        self.race['tank_capacity_l'] = 12
        self.assertFalse(self.view()['multiple_stops_needed'])

    def test_timed_race_includes_leader_and_final_player_crossing(self):
        self.race.update(total_laps=None, time_remaining_s=120)
        view = self.view()
        self.assertEqual(view['finish_method'], 'TIMED_ESTIMATE')
        self.assertEqual(view['remaining_laps'], 2.5)
        self.assertIsNone(self.wall.view(self.snapshot, 2, 3)['rejoin_class_position_estimate'])
        self.wall.paces.pop(0)
        self.assertIsNone(self.view()['remaining_laps'])

    def test_lapped_driver_finishes_after_overall_leader(self):
        self.race['cars'][0]['lap_completed'] = 8
        view = self.view()
        self.assertEqual(view['finish_method'], 'LEADER_FINISH_ESTIMATE')
        self.assertEqual(view['remaining_laps'], 1.5)

    def test_class_neighbors_and_distance_estimate(self):
        view = self.view()
        self.assertEqual(view['behind']['idx'], 2)
        self.assertEqual(view['behind']['gap_estimate_s'], 2)
        self.assertEqual(view['behind']['pace_advantage_s'], 2)
        self.race['cars'][2]['class_id'] = 20
        self.assertIsNone(self.view()['behind'])

    def test_rejoin_requires_configured_total_loss_and_all_rival_paces(self):
        self.assertIsNone(self.view()['rejoin_class_position_estimate'])
        self.assertEqual(self.view(pit_loss_s=25)['rejoin_class_position_estimate'], 3)
        self.wall.paces.pop(2)
        self.assertIsNone(self.view(pit_loss_s=25)['rejoin_class_position_estimate'])

    def test_missing_fuel_or_finish_information_never_invents_strategy(self):
        self.assertIsNone(self.wall.view(self.snapshot)['fuel_to_add_l'])
        self.race.update(total_laps=None, time_remaining_s=1e9)
        self.assertIsNone(self.view()['remaining_laps'])
        self.snapshot['fuel_l'] = None
        self.assertIsNone(self.view()['fuel_laps'])

    def test_practice_grid_and_finished_session_disable_race_reports(self):
        for session, state in [('Practice', 4), ('Race', 3), ('Race', 6)]:
            self.race.update(session_type=session, state=state)
            view = self.view()
            self.assertFalse(view['active'])
            self.assertEqual(self.wall.candidates(view, self.snapshot, {'snapshot': self.snapshot}), [])

    def test_pit_laps_missing_intervals_and_session_changes_do_not_supply_pace(self):
        wall = RaceEngineer()
        race = copy.deepcopy(self.race)
        wall.ingest(race, 'a', 1)
        race = copy.deepcopy(race); race['cars'][1]['on_pit_road'] = True
        wall.ingest(race, 'a', 2)
        race = copy.deepcopy(race); race['cars'][1]['on_pit_road'] = False
        wall.ingest(race, 'a', 3)
        race = copy.deepcopy(race); race['cars'][1]['lap_completed'] += 1
        wall.ingest(race, 'a', 4)
        self.assertNotIn(1, wall.paces)
        race = copy.deepcopy(race); race['cars'][1]['lap_completed'] += 1
        race['cars'][1]['last_lap_s'] = 101
        wall.ingest(race, 'a', 5)
        self.assertEqual(list(wall.paces[1]), [101])
        race = copy.deepcopy(race); race['cars'][1]['lap_completed'] += 1
        wall.ingest(race, 'a', 50)
        self.assertEqual(list(wall.paces[1]), [101])
        wall.ingest(race, 'b', 1)
        self.assertEqual(wall.paces, {})

    def test_priorities_corner_deferral_duplicate_suppression_and_off(self):
        self.wall.SAVE_LIMIT_PCT = 30   # a shortage the driver is told he could save
        reports = self.wall.candidates(self.view(), self.snapshot, {'snapshot': self.snapshot})
        self.assertEqual(reports[0]['engineer'], 'strategy')
        selected = self.wall.select(reports, self.snapshot, 'important')
        self.assertEqual(selected['key'], 'fuel_short')
        self.wall.delivered(selected, 100)
        self.assertIsNone(self.wall.select(reports, {**self.snapshot, 'session_time': 101}, 'all'))
        self.assertIsNone(self.wall.select(reports, {**self.snapshot, 'session_time': 130, 'brake': .5}, 'important'))
        self.assertIsNone(self.wall.select(reports, self.snapshot, 'off'))
        self.snapshot['fuel_l'] = 1
        urgent = self.wall.candidates(self.view(), self.snapshot, {'snapshot': self.snapshot})
        self.assertEqual(self.wall.select(urgent, {**self.snapshot, 'session_time': 106, 'brake': .5}, 'important')['key'], 'fuel_critical')

    def test_voice_roles_and_follow_up(self):
        self.assertEqual(choose_role('앞차랑 간격 얼마야?'), 'race')
        self.assertEqual(choose_role('끝까지 갈 수 있어?'), 'strategy')
        self.assertEqual(choose_role('몇 리터 넣어?'), 'strategy')
        self.assertEqual(choose_role('왜 그래?', [{'engineer': 'race'}]), 'race')
        self.assertEqual(choose_role('이 코너에서 앞이 밀려'), 'coach')
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(len({role_voice(k) for k in ROLES}), 3)

    def test_optional_sdk_metadata_and_missing_arrays(self):
        sdk = {'DriverInfo': {'DriverCarIdx': 1, 'Drivers': [{'CarIdx': 1, 'CarClassID': 10}]}}
        result = read_race(sdk, [], 0)
        self.assertIsNone(result['session_type'])
        self.assertIsNone(result['cars'][0]['last_lap_s'])
        self.assertIsNone(result['cars'][0]['on_pit_road'])

    def test_each_driver_brings_a_rating_and_the_pace_car_none(self):
        sdk = {'DriverInfo': {'DriverCarIdx': 1, 'Drivers': [
            {'CarIdx': 0, 'CarIsPaceCar': 1, 'IRating': 0}, {'CarIdx': 1, 'CarClassID': 10, 'IRating': 2412},
            {'CarIdx': 2, 'CarClassID': 10, 'IRating': 1350}, {'CarIdx': 3, 'CarClassID': 10}]}}
        self.assertEqual([car['irating'] for car in read_race(sdk, [], 0)['cars']], [None, 2412., 1350., None])


class PitWallIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store = VoiceNoteStore(Path(self.temp.name) / 'voice.sqlite3')
        self.live = LiveTelemetry(clock=lambda: 1000)
        self.sample = {'session_time': 100., 'session_num': 0, 'lap_number': 4, 'distance_pct': .5,
                       'speed_mps': 50., 'fuel_l': 10., 'brake': 0., 'steering_rad': 0., 'on_track': True,
                       'on_pit_road': False, 'is_replay': False}
        self.race = race_fixture()
        self.live.ingest({'track_id': '1', 'car_id': '2'}, self.sample, self.race)
        self.live.fuel_history.extend([2, 2, 2])
        self.live.pit_wall.paces = {0: deque([99, 99, 99]), 1: deque([100, 100, 100]), 2: deque([98, 98, 98])}
        self.speech = MagicMock(); self.speech.synthesize.return_value = base64.b64encode(b'ID3' + b'x' * 500).decode()
        audio_env = patch.dict('os.environ', {'IRACING_RADIO_AUDIO_DIR': ''})
        audio_env.start(); self.addCleanup(audio_env.stop)
        self.coach = VoiceCoach(self.live, self.store, speech=self.speech)

    def test_questions_auto_route_and_use_distinct_synthesis(self):
        for text, role in [('뒤차가 더 빨라?', 'race'), ('끝까지 연료 돼?', 'strategy'), ('이 코너가 어려워', 'coach')]:
            result = asyncio.run(self.coach.utterance(Utterance(text=text)))
            self.assertEqual(result['note']['context']['engineer'], role)
            if role != 'coach':
                self.assertEqual(self.speech.synthesize.call_args.kwargs['role'], role)
                self.assertEqual(result['note']['reply_source'], 'MEASURED')

    def test_unsolicited_report_is_persisted_once_without_llm(self):
        report = asyncio.run(self.coach.report(ReportRequest()))
        self.assertEqual(report['report']['engineer'], 'strategy')
        self.assertEqual(report['report']['note']['source'], 'AUTO_REPORT')
        self.assertEqual(self.speech.synthesize.call_args.kwargs['role'], 'strategy')
        again = asyncio.run(self.coach.report(ReportRequest()))
        self.assertEqual(again['report']['note']['id'], report['report']['note']['id'])
        self.speech.synthesize.assert_called_once()
        self.assertTrue(self.coach.acknowledge_report(report['report']['note']['id'])['accepted'])
        self.assertIsNone(asyncio.run(self.coach.report(ReportRequest()))['report'])
        self.assertEqual(len(self.store.list()), 1)
        self.assertEqual(recent_conversation(self.store, self.live.context()), [])

    def test_text_only_off_and_busy_do_not_call_cloud(self):
        self.assertIsNone(asyncio.run(self.coach.report(ReportRequest(level='off')))['report'])
        result = asyncio.run(self.coach.report(ReportRequest(speak=False)))
        self.assertIsNotNone(result['report'])
        self.speech.synthesize.assert_not_called()
        async def busy():
            async with self.coach.cloud_lock:
                self.assertIsNone((await self.coach.report(ReportRequest()))['report'])
        asyncio.run(busy())

    def test_a_report_already_on_disk_is_not_held_back_by_a_question(self):
        # A question (speech -> AI -> speech) holds the cloud lock for several seconds. A report whose clip is
        # already cached needs no cloud and goes out; one that still needs synthesis waits for the next poll.
        first = asyncio.run(self.coach.report(ReportRequest()))['report']          # synthesizes and caches the clip
        self.assertIsNotNone(first)
        self.coach.pending_report = None
        self.live.pit_wall.reported.clear(); self.live.pit_wall.last_report = -999
        self.speech.synthesize.reset_mock()
        async def during_question():
            async with self.coach.cloud_lock:
                return await self.coach.report(ReportRequest())
        again = asyncio.run(during_question())['report']
        self.assertIsNotNone(again)
        self.assertEqual(again['note']['reply'], first['note']['reply'])
        self.speech.synthesize.assert_not_called()

    def test_audio_on_disk_needs_the_first_multipart_clip(self):
        cache = self.coach.fixed_audio
        cache.directory.mkdir(parents=True, exist_ok=True)
        cache.path('race', '앞차 0.7초. 줄고 있습니다.').write_bytes(b'ID3' + b'x' * 200)
        self.assertTrue(self.coach.audio_on_disk({'engineer': 'race', 'speech_text': '앞차 0.7초. 줄고 있습니다.'}))
        self.assertTrue(self.coach.audio_on_disk({'engineer': 'race', 'speech_parts': ['앞차 0.7초. 줄고 있습니다.', '없는 문장입니다.']}))
        self.assertFalse(self.coach.audio_on_disk({'engineer': 'race', 'text': '합성해야 하는 답변'}))
        self.assertFalse(self.coach.audio_on_disk({'engineer': 'race', 'speech_key': 'no_such_call'}))

    def test_late_cloud_reply_does_not_announce_old_session(self):
        def change_session(*args, **kwargs):
            self.live.ingest({'track_id': '99'}, {**self.sample, 'session_time': 1}, self.race)
            return 'audio'
        self.speech.synthesize.side_effect = change_session
        result = asyncio.run(self.coach.report(ReportRequest()))
        self.assertIsNone(result['report'])
        self.assertEqual(self.store.list(), [])

    def test_report_rechecks_refueling_and_corner_workload_after_synthesis(self):
        def refuel(*args, **kwargs):
            self.live.ingest({'track_id': '1', 'car_id': '2'}, {**self.sample, 'session_time': 101, 'fuel_l': 30}, self.race)
            return 'audio'
        self.speech.synthesize.side_effect = refuel
        self.assertIsNone(asyncio.run(self.coach.report(ReportRequest()))['report'])
        self.assertEqual(self.store.list(), [])

    def test_report_has_delivery_state_and_ibt_review_keeps_strategy_out_of_setup_hypotheses(self):
        import test_partial_coaching as fixture
        report = asyncio.run(self.coach.report(ReportRequest(speak=False)))['report']
        self.assertEqual(report['note']['context']['report_delivery'], 'PREPARED')
        acknowledged = self.coach.acknowledge_report(report['note']['id'])
        self.assertEqual(acknowledged['note']['context']['report_delivery'], 'DELIVERED')
        telemetry = fixture.PartialCoachingTests.telemetry(SimpleNamespace(root=Path(self.temp.name)))
        lap = telemetry.laps[0].lap_number
        self.store.link(report['note']['id'], telemetry.path.name, recording_key(telemetry), 'MANUAL', lap, .5)
        reviewed = review_notes(self.store, SimpleNamespace(telemetry=telemetry), {}, None)['notes'][0]
        self.assertTrue(reviewed['is_engineer_report'])
        self.assertEqual(reviewed['report_context']['fuel_to_add_l'], 4)
        self.assertEqual(reviewed['setup_candidates'], [])

    def test_unacknowledged_report_is_replaced_when_conditions_change(self):
        first = asyncio.run(self.coach.report(ReportRequest(speak=False)))['report']
        self.live.ingest({'track_id': '1', 'car_id': '2'}, {**self.sample, 'session_time': 101, 'fuel_l': 1}, self.race)
        second = asyncio.run(self.coach.report(ReportRequest(speak=False)))['report']
        self.assertNotEqual(first['note']['id'], second['note']['id'])
        self.assertEqual(second['engineer'], 'strategy')
        self.assertEqual(second['note']['context']['report_key'], 'fuel_critical')
        self.assertFalse(self.coach.acknowledge_report(first['note']['id'])['accepted'])
        self.assertTrue(self.coach.acknowledge_report(second['note']['id'])['accepted'])

    def test_api_settings_validation_and_three_voice_samples(self):
        app = FastAPI()
        register_voice_routes(app, self.coach, lambda _: None, MagicMock(), MagicMock(), MagicMock())
        with TestClient(app) as client:
            self.assertEqual(len(client.get('/api/live-coach/status').json()['engineers']), 3)
            for role in ROLES:
                self.assertEqual(client.post(f'/api/live-coach/voices/{role}/test', json={}).status_code, 200)
                self.assertEqual(self.speech.synthesize.call_args.kwargs['role'], role)
            self.assertEqual(client.post('/api/live-coach/reports', json={'pit_loss_s': -1}).status_code, 422)
            self.assertEqual(client.post('/api/live-coach/reports', json={'level': 'spam'}).status_code, 422)
            result = client.post('/api/live-coach/reports', json={'level': 'off', 'reserve_laps': 1, 'pit_loss_s': 25}).json()
            self.assertEqual(result['pit_wall']['fuel_to_add_l'], 5)
            self.assertEqual(result['pit_wall']['rejoin_class_position_estimate'], 3)
            capture = client.post('/api/live-coach/captures', json={}).json()
            self.assertEqual(capture['context']['pit_wall']['reserve_laps'], 1)


if __name__ == '__main__':
    unittest.main()
