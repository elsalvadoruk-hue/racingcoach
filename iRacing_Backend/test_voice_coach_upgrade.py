"""Live corner comparison, measured review answers, leaner AI context and new spotter calls."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from analysis_support import CornerDefinitionStore
from live_corners import LiveCornerCoach, corner_answer, lap_answer
from live_telemetry import LiveTelemetry
from race_radio import RaceRadio
from spotter_context import HazardWatch, TrafficApproach
from voice_coach import VoiceCoach
from voice_context import coaching_instructions, columnar, review_question
from voice_notes import VoiceNoteStore

IDENTITY = {'car_id': '7', 'track_id': '9', 'track_config': '', 'car_path': 'car', 'track_path': 'track'}
CORNER = {'id': 'C1', 'entry_pct': .30, 'apex_pct': .35, 'exit_pct': .40}
LENGTH = 4000.


class Session:
    """Consecutive laps at constant progress; only the corner inputs change between laps."""
    def __init__(self, sink):
        self.sink, self.clock, self.lap_number = sink, 0., 0

    def lap(self, extra_seconds=0., brake_at=.31, corner_speed=30., throttle_at=.36, beside=None, skip=None):
        self.lap_number += 1
        hz, pct, samples = 30, 0., []
        while pct < 1:
            inside = CORNER['entry_pct'] <= pct <= CORNER['exit_pct']
            # A slower corner: the clock keeps running while progress slows inside the segment.
            rate = (1 / 80.) * (8. / (8. + extra_seconds) if inside else 1.)
            sample = {'distance_pct': pct, 'session_time': self.clock, 'lap_number': self.lap_number,
                      'speed_mps': corner_speed if inside else 60., 'brake': .8 if brake_at <= pct < .34 else 0.,
                      'throttle': 1. if pct < brake_at or pct >= throttle_at else 0.,
                      'on_track': True, 'is_replay': False, 'on_pit_road': False, 'incidents': 0, 'session_flags': 0,
                      'car_left_right': beside if inside and beside else 1, 'lap_last_s': 80.}
            if not (skip and skip[0] < pct < skip[1]):
                self.sink(sample)
                samples.append(sample)
            self.clock, pct = self.clock + 1 / hz, pct + rate / hz
        return samples


def session(coach):
    return Session(lambda sample: coach.ingest(sample, IDENTITY, sample['session_time'], LENGTH))


class LiveCornerTests(unittest.TestCase):
    def coach(self):
        return LiveCornerCoach(lambda identity: [CORNER])

    def test_no_definitions_means_no_comparison_and_a_clear_reason(self):
        coach = LiveCornerCoach(lambda identity: None)
        session(coach).lap()
        view = coach.view()
        self.assertFalse(view['available'])
        self.assertIn('분석', view['reason'])
        self.assertIsNone(lap_answer(view))
        self.assertIsNone(corner_answer(view))

    def test_slower_pass_reports_measured_differences_against_own_best(self):
        coach = self.coach()
        run = session(coach)
        run.lap()
        run.lap()
        self.assertIsNone(coach.review)  # the first clean pass only supplied the reference
        run.lap(.4, brake_at=.305, corner_speed=27., throttle_at=.365)
        self.assertEqual(coach.review['lap'], 2)
        self.assertEqual(coach.review['losses'], [])
        run.lap()  # crossing the line completes lap 3
        review = coach.review
        self.assertEqual((review['lap'], review['compared'], len(review['losses'])), (3, 1, 1))
        loss = review['losses'][0]
        self.assertAlmostEqual(loss['delta_s'], .4, delta=.05)
        self.assertAlmostEqual(loss['brake_delta_m'], -20, delta=3)
        self.assertAlmostEqual(loss['min_speed_delta_kph'], -10.8, delta=.2)
        self.assertAlmostEqual(loss['throttle_delta_m'], 20, delta=3)
        self.assertEqual(loss['cause'], 'brake_early')
        answer = lap_answer(coach.view())
        for expected in ('3랩', '1번 구간', '제동을 약', '최저속도가 11킬로미터', '가속 시작이 약'):
            self.assertIn(expected, answer)

    def test_pass_beside_a_car_or_with_a_gap_is_never_compared_or_kept_as_reference(self):
        coach = self.coach()
        run = session(coach)
        run.lap()
        run.lap()
        best = coach.best[coach.key]['C1']['duration_s']
        run.lap(.5, beside=2)
        run.lap(.5, skip=(.33, .36))
        run.lap()
        self.assertEqual(coach.review['lap'], 2)  # laps 3 and 4 produced no comparison
        self.assertAlmostEqual(coach.best[coach.key]['C1']['duration_s'], best, places=6)  # never the 8.5s passes
        fresh = self.coach()
        session(fresh).lap(skip=(.33, .36))
        self.assertEqual(fresh.best.get(fresh.key, {}), {})

    def test_reminder_is_given_once_shortly_before_the_reference_braking_point(self):
        coach = self.coach()
        given = []

        def feed(sample):
            coach.ingest(sample, IDENTITY, sample['session_time'], LENGTH)
            call = coach.reminder(sample, 80.)
            if call:
                given.append((sample['distance_pct'], call))
        run = Session(feed)
        run.lap()
        run.lap()
        run.lap(.4, corner_speed=27.)
        self.assertEqual(given, [])
        run.lap()
        self.assertEqual(len(given), 1)
        pct, call = given[0]
        self.assertEqual(call['cause'], 'min_speed')
        self.assertTrue(6 <= (call['until_pct'] - pct) * 80 <= 12.5)
        self.assertIn('3랩 1번 구간', call['text'])
        run.lap()  # lap 4 was clean and level with the best: nothing left to mention
        self.assertEqual(len(given), 1)
        self.assertIsNone(coach.view()['focus'])

    def test_the_reminder_leaves_room_for_the_whole_sentence_at_the_speed_driven(self):
        def first_call(lengths):
            coach, given = self.coach(), []

            def feed(sample):
                coach.ingest(sample, IDENTITY, sample['session_time'], LENGTH)
                call = coach.reminder(sample, 80., LENGTH, lengths)
                if call:
                    given.append((sample['distance_pct'], call))
            run = Session(feed)
            run.lap()
            run.lap()
            run.lap(.4, corner_speed=27.)
            run.lap()
            self.assertEqual(len(given), 1)
            return given[0]
        # 60 m/s on a 4 km lap: the braking point comes at 0.015 of a lap a second, sooner than the lap average says.
        pct, call = first_call([6.4])
        seconds = (call['until_pct'] - pct) * LENGTH / 60.
        self.assertTrue(6.4 + 1.5 <= seconds * .9 <= 6.4 + 8)
        self.assertEqual(call['fits'], 0)
        # It may only start while the sentence still ends half a second before the braking point.
        self.assertAlmostEqual(call['margin_pct'], 6.9 * 60. / LENGTH / .9)
        # The whole call needs more straight than there is: the shorter wording is used, and later.
        short_pct, short = first_call([40., 4.2])
        self.assertEqual(short['fits'], 1)
        self.assertGreater(short_pct, pct)
        # Neither fits: nothing is said.
        coach = self.coach()
        run = session(coach)
        run.lap(); run.lap(); run.lap(.4, corner_speed=27.)
        self.assertFalse(any(coach.reminder(sample, 80., LENGTH, [200., 150.]) for sample in run.lap()))

    def test_live_telemetry_emits_the_reminder_and_survives_a_coach_fault(self):
        now = [0.]
        live = LiveTelemetry(clock=lambda: now[0])
        live.corners.lookup = lambda identity: [CORNER]
        seen, gone = {}, []

        def feed(sample):
            now[0] = sample['session_time']
            live.ingest(IDENTITY, {**sample, 'session_num': 0, 'session_state': 4, 'steering_rad': 0.})
            events = [e for e in live.radio.view(now[0])['events'] if e['key'] == 'corner_focus']
            seen.update({e['id']: e for e in events})
            if seen and not events:
                gone.append(sample['distance_pct'])
        run = Session(feed)
        run.lap()
        run.lap()
        run.lap(.4, corner_speed=27.)
        run.lap()
        self.assertEqual(len(seen), 1)
        event = next(iter(seen.values()))
        self.assertEqual((event['engineer'], event['speech_text']), ('coach', '다음 코너. 지난 랩에 0.4초 잃었습니다. 최저속도가 낮았습니다.'))
        # Withdrawn before the braking point, while there was still time to finish the sentence.
        self.assertLess(gone[0], .31)
        self.assertEqual(live.context()['corner_coach']['last_lap']['lap'], 3)
        live.corners.ingest = MagicMock(side_effect=RuntimeError('boom'))
        with self.assertLogs('iRacingLive', level='ERROR'):
            live.ingest(IDENTITY, {'session_time': now[0] + 1, 'session_num': 0, 'distance_pct': .5, 'lap_number': 5})
        self.assertEqual(live.state, 'CONNECTED')

    def test_saved_definitions_are_found_for_the_live_car_only(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CornerDefinitionStore(Path(folder) / 'corners.json')
            store.put(('7', 'car', 'Car', '9', 'track', 'Track', '', 'v1', 4000), [CORNER])
            self.assertEqual(store.find('7', '9', ''), [CORNER])
            self.assertEqual(store.find(7, 9, None), [CORNER])
            self.assertIsNone(store.find('7', '9', 'short'))
            self.assertIsNone(store.find('', '9', ''))


class ReviewAnswerTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.coach = VoiceCoach(LiveTelemetry(), VoiceNoteStore(Path(self.work.name) / 'notes.sqlite3'), speech=MagicMock())
        loss = {'id': 'C5', 'number': 5, 'position_pct': .4, 'delta_s': .31, 'min_speed_delta_kph': -4., 'brake_delta_m': -12.,
                'throttle_delta_m': 2., 'findings': ['제동을 약 12미터 일찍 시작했습니다', '최저속도가 4킬로미터 낮았습니다'], 'cause': 'brake_early'}
        self.view = {'available': True, 'reason': None, 'corner_count': 9, 'reference_corners': 9,
                     'last_lap': {'lap': 6, 'compared': 9, 'losses': [loss], 'loss_count': 1, 'best_gain': None, 'total_loss_s': .31},
                     'last_corner': {**loss, 'lap': 7}, 'focus': None}
        sample = {'session_time': 100., 'run_id': 'r', 'session_num': 0, 'lap_number': 7, 'distance_pct': .5, 'speed_mps': 40.,
                  'brake': 0., 'throttle': 1., 'steering_rad': 0., 'on_track': True, 'is_replay': False, 'on_pit_road': False}
        self.context = {'connected': True, 'snapshot': sample, 'recent': [{**sample, 'session_time': 99.8, 'gear': 4}, sample],
                        'identity': IDENTITY, 'corner_coach': self.view}

    def test_question_kinds(self):
        for text, kind in [('이전 코너 뭐 잘못했어?', 'corner'), ('방금 코너 왜 느렸어', 'corner'), ('방금 랩 어때?', 'lap'),
                           ('어디서 시간을 잃었어?', 'lap'), ('지난 랩 분석해줘', 'lap'), ('이 코너 어렵다', None),
                           ('연료 몇 랩 남았어?', None), ('트레일 브레이킹이 뭐야', None)]:
            self.assertEqual(review_question(text), kind, text)

    def test_measured_mode_answers_from_the_comparison_without_ai(self):
        self.coach.ai_client = MagicMock()
        answer, source = self.coach.answer('방금 랩 어때?', self.context)
        self.assertEqual(source, 'MEASURED')
        self.assertIn('6랩은 비교한 9개 구간 중 5번 구간에서 구간 최고보다 0.31초 느렸습니다', answer)
        answer, source = self.coach.answer('이전 코너 뭐 잘못했어?', {**self.context, 'engineer': 'race'})
        self.assertEqual(source, 'MEASURED')
        self.assertTrue(answer.startswith('방금 5번 구간에서 구간 최고보다 0.31초 느렸습니다. 제동을 약 12미터'))
        self.coach.ai_client.models.generate_content.assert_not_called()

    def test_without_comparison_the_previous_behaviour_is_kept(self):
        context = {**self.context, 'corner_coach': {'available': False}}
        answer, source = self.coach.answer('지난 랩 분석해줘', context)
        self.assertEqual(source, 'LOCAL')
        self.assertIn('IBT 분석', answer)
        _, source = self.coach.answer('방금 랩 어때?', {**self.context, 'connected': False})
        self.assertEqual(source, 'LOCAL')

    def test_ai_mode_receives_measured_comparison_and_columnar_samples(self):
        response = SimpleNamespace(text='답변입니다.', candidates=[SimpleNamespace(finish_reason='STOP')],
                                   usage_metadata=SimpleNamespace(prompt_token_count=900, cached_content_token_count=600, candidates_token_count=80))
        self.coach.ai_client = MagicMock()
        self.coach.ai_client.models.generate_content.return_value = response
        with self.assertLogs('iRacingVoiceCoach', level='INFO') as logs:
            answer, source = self.coach.answer('방금 랩 어때?', {**self.context, 'engineer': 'spotter'}, allow_ai=True)
        self.assertEqual((answer, source), ('답변입니다.', 'VERTEX_AI'))
        self.assertTrue(any('prompt=900 cached=600 output=80' in line for line in logs.output))
        call = self.coach.ai_client.models.generate_content.call_args.kwargs
        payload = json.loads(call['contents'])
        comparison = payload['lap_corner_comparison']
        self.assertTrue(comparison['available'])
        self.assertEqual(comparison['last_completed_lap']['losses'][0]['brake_delta_m'], -12.)
        self.assertEqual(payload['recent']['columns'][:3], ['session_time', 'distance_pct', 'speed_mps'])
        self.assertEqual(len(payload['recent']['rows']), 2)
        self.assertIn('lap_corner_comparison', call['config']['system_instruction'])

    def test_columnar_keeps_every_value_and_is_smaller(self):
        points = [{'session_time': float(i), 'distance_pct': i / 100, 'speed_mps': 40.5, 'brake': .1, 'throttle': .9,
                   'steering_rad': .01, 'lap_number': 3, 'gear': 4, 'incidents': 0} for i in range(24)]
        table = columnar(points)
        rebuilt = [dict(zip(table['columns'], row)) for row in table['rows']]
        self.assertEqual(rebuilt, points)
        self.assertLess(len(json.dumps(table)), len(json.dumps(points)) * .5)
        self.assertEqual(columnar([]), [])
        self.assertEqual(columnar([{'session_time': 1.}, {'session_time': 2., 'gear': 3}])['rows'], [[1., None], [2., 3]])

    def test_instructions_share_one_prefix_for_every_answer_length(self):
        short, long = coaching_instructions('짧게 말해줘'), coaching_instructions('왜 그런지 자세히 설명해줘')
        self.assertNotEqual(short, long)
        shared = next(i for i, (a, b) in enumerate(zip(short, long)) if a != b)
        self.assertGreater(shared / len(short), .9)
        self.assertIn('짧은 답변', short[shared - 20:])
        self.assertIn('자세한 설명', long[shared - 20:])


def race(t, others):
    own = {'idx': 0, 'name': 'me', 'class_id': 1, 'lap_completed': 3, 'distance_pct': (.2 + t * .01) % 1,
           'last_lap_s': 100., 'track_surface': 3, 'on_pit_road': False}
    return {'session_type': 'Race', 'player_idx': 0, 'cars': [own, *others]}


def other(idx, pct, surface=3, **extra):
    return {'idx': idx, 'name': f'driver {idx}', 'class_id': 1, 'lap_completed': 3, 'distance_pct': pct % 1,
            'last_lap_s': 100., 'track_surface': surface, 'on_pit_road': False, **extra}


class HazardTests(unittest.TestCase):
    def test_stopped_car_ahead_is_called_after_a_measured_interval(self):
        watch = HazardWatch()
        watch.ingest(race(0, [other(1, .26)]), 0.)
        self.assertEqual(watch.view(0.), [])
        watch.ingest(race(1, [other(1, .26)]), 1.)
        self.assertEqual([(h['idx'], h['kind']) for h in watch.view(1.)], [(1, 'hazard_slow_ahead')])
        self.assertAlmostEqual(watch.view(1.)[0]['gap_estimate_s'], 5.)
        self.assertEqual(watch.view(4.), [])  # stale positions are never repeated

    def test_a_car_in_a_slow_corner_is_not_a_hazard_once_the_corner_is_known(self):
        # The stretch at 26 percent is a hairpin: every car crawls there at a fifth of the player's pace.
        def hairpin(watch, t, pct):
            watch.ingest(race(t, [other(1, pct)]), float(t))
            return [h['kind'] for h in watch.view(float(t))]
        fresh = HazardWatch()
        hairpin(fresh, 0, .2600)
        self.assertEqual(hairpin(fresh, 1, .2620), [])   # unknown stretch and not stopped: nothing is claimed
        learned = HazardWatch()
        for lap in range(10):   # ten cars seen through the hairpin at that pace
            learned.ingest({'session_type': 'Race', 'player_idx': 0, 'cars': [
                {**race(0, [])['cars'][0], 'distance_pct': .7}, other(5, .2600)]}, lap * 10.)
            learned.ingest({'session_type': 'Race', 'player_idx': 0, 'cars': [
                {**race(0, [])['cars'][0], 'distance_pct': .71}, other(5, .2620)]}, lap * 10. + 1)
        learned.reset()   # pit stops and cautions reset the tracking, not what was learned
        self.assertIsNotNone(learned.usual(.262))
        hairpin(learned, 200, .2600)
        self.assertEqual(hairpin(learned, 201, .2620), [])                      # normal for that corner
        hairpin(learned, 300, .2600)
        self.assertEqual(hairpin(learned, 301, .2605), ['hazard_slow_ahead'])   # a quarter of the usual pace there

    def test_off_track_needs_two_observations_and_racing_pace_cars_are_ignored(self):
        watch = HazardWatch()
        watch.ingest(race(0, [other(1, .25, 0), other(2, .27)]), 0.)
        watch.ingest(race(1, [other(1, .26, 0), other(2, .28)]), 1.)
        self.assertEqual([(h['idx'], h['kind']) for h in watch.view(1.)], [(1, 'hazard_off_ahead')])
        once = HazardWatch()
        once.ingest(race(0, [other(1, .25, 3)]), 0.)
        once.ingest(race(1, [other(1, .26, 0)]), 1.)
        self.assertEqual(once.view(1.), [])

    def test_behind_far_ahead_pit_road_and_slow_player_do_not_call(self):
        for cars in ([other(1, .15)], [other(1, .40)], [other(1, .26, on_pit_road=True)], [other(1, .26, 1)]):
            watch = HazardWatch()
            watch.ingest(race(0, cars), 0.)
            watch.ingest(race(1, cars), 1.)
            self.assertEqual(watch.view(1.), [], cars)
        crawling = HazardWatch()  # the player is slow too (start, own spin): relative speed says nothing
        for t in range(2):
            data = race(0, [other(1, .26)])
            data['cars'][0]['distance_pct'] = .2 + t * .001
            crawling.ingest(data, float(t))
        self.assertEqual(crawling.view(1.), [])

    def test_radio_calls_hazard_under_local_yellow_but_not_under_caution_or_in_pits(self):
        base = {'session_num': 0, 'lap_number': 4, 'speed_mps': 50., 'brake': 0., 'throttle': 1., 'steering_rad': 0.,
                'on_track': True, 'is_replay': False, 'on_pit_road': False, 'session_state': 4}
        for flags, pit, expected in [(0, False, True), (0x8, False, True), (0x4000, False, False), (0, True, False)]:
            radio2 = RaceRadio()
            for t in range(2):
                sample = {**base, 'session_time': 10. + t * .45, 'distance_pct': .2 + t * .01, 'session_flags': flags, 'on_pit_road': pit}
                radio2.ingest(sample, 'run', t * .45, race(t, [other(1, .26)]))
            keys = [e['key'] for e in radio2.view(.45)['events']]
            self.assertEqual('hazard_slow_ahead' in keys, expected, (flags, pit))


class CloseBehindTests(unittest.TestCase):
    def feed(self, gaps):
        tracker, calls = TrafficApproach(), []
        for t, gap in enumerate(gaps):
            tracker.ingest(race(t, [other(1, .2 + t * .01 - gap / 100.)]), float(t))
            behind = tracker.view(float(t))['behind']
            calls.append(behind and behind['close_key'])
        return calls

    def test_called_once_on_arrival_and_again_only_after_dropping_back(self):
        calls = self.feed([1.2, .9, .45, .4, .4, .6, .45, 1.0, .9, .45, .4])
        self.assertEqual([i for i, call in enumerate(calls) if call], [2, 9])

    def test_radio_respects_the_traffic_switch_and_expires_when_the_car_drops_back(self):
        base = {'session_num': 0, 'lap_number': 4, 'speed_mps': 50., 'brake': 0., 'throttle': 1., 'steering_rad': 0.,
                'on_track': True, 'is_replay': False, 'on_pit_road': False, 'session_state': 4, 'session_flags': 0}
        for enabled in (True, False):
            radio = RaceRadio()
            radio.traffic_enabled = enabled
            for t, gap in enumerate([.9, .45]):
                now = t * .45
                radio.ingest({**base, 'session_time': 10. + now, 'distance_pct': .2 + t * .01}, 'run', now,
                             race(t, [other(1, .2 + t * .01 - gap / 100.)]))
            keys = [e['key'] for e in radio.view(.45)['events']]
            self.assertEqual('close_behind' in keys, enabled)
        radio = RaceRadio()
        for t, gap in enumerate([.9, .45, 3.]):
            now = t * .45
            radio.ingest({**base, 'session_time': 10. + now, 'distance_pct': .2 + t * .01}, 'run', now,
                         race(t, [other(1, .2 + t * .01 - gap / 100.)]))
        self.assertNotIn('close_behind', [e['key'] for e in radio.view(.9)['events']])


if __name__ == '__main__':
    unittest.main()
