"""The formation lap briefing: only while rolling on a parade lap, a topic at a time, only what is known."""
import unittest

import radio_phrases
import tempfile
from pathlib import Path

from live_formation import FormationBrief, RaceHistory, TOPICS, CLOSING, history_facts
from live_telemetry import LiveTelemetry
from radio_audio import shown_text

FACTS = {'race_min': 45, 'race_laps': None, 'class_position': 7, 'class_cars': 23, 'fuel_l': 55., 'pit_speed_kph': 80, 'pit_transit_s': 24.6,
         'track_temp_c': 20.4, 'air_temp_c': 19., 'wet': False, 'dry': True, 'incident_limit': 17, 'multiclass': True}


def sample(pct, state=3, speed=30., **more):
    return {'session_state': state, 'speed_mps': speed, 'distance_pct': pct % 1, 'on_pit_road': False, 'on_track': True, 'is_replay': False, **more}


class FormationBriefTests(unittest.TestCase):
    def lap(self, facts=FACTS, seconds=240, calls=5, run='run', **more):
        brief, said = FormationBrief(calls), []
        for t in range(seconds):
            driven = t / seconds * .97
            found = brief.step(sample(.95 + driven, **more), facts, run, float(t))
            if found:
                said.append((round(driven, 2), *found))
        return said

    def test_the_chosen_number_of_calls_is_spread_over_the_lap_and_the_send_off_is_last(self):
        said = self.lap()
        names = [name for _, name, _, _ in said]
        self.assertEqual(len(names), 5)
        self.assertEqual((names[0], names[-1]), ('race', 'closing'))
        self.assertEqual(len(names), len(set(names)))                                  # nothing twice
        places = [place for place, *_ in said]
        self.assertLess(places[0], .05)
        self.assertAlmostEqual(places[-1], .78, delta=.02)                             # the send-off at about four fifths
        gaps = [later - earlier for earlier, later in zip(places, places[1:])]
        self.assertTrue(all(abs(gap - .19) < .03 for gap in gaps), gaps)               # evenly, with quiet in between
        self.assertEqual(dict(said[0][3]), {'form_hello': {}, 'form_length_min': {'min': 45}, 'form_grid': {'pos': 7}, 'form_field': {'count': 23}})
        self.assertEqual(dict(said[-1][3]), {'form_ready': {}, 'form_good_luck': {}})

    def test_the_number_is_the_drivers_choice(self):
        self.assertEqual(self.lap(calls=0), [])
        self.assertEqual([name for _, name, _, _ in self.lap(calls=1)], ['race'])
        self.assertEqual([name for _, name, _, _ in self.lap(calls=2)], ['race', 'closing'])
        self.assertEqual(len(self.lap(calls=8)), 8)                                    # every topic and the send-off
        # With fewer calls than topics the middle ones differ from race to race; the first and last never do.
        middles = {tuple(name for _, name, _, _ in self.lap(calls=4, run=f'race {n}'))[1:-1] for n in range(12)}
        self.assertGreater(len(middles), 2)
        for n in range(12):
            names = [name for _, name, _, _ in self.lap(calls=4, run=f'race {n}')]
            self.assertEqual((len(names), names[0], names[-1]), (4, 'race', 'closing'))
        # A short formation lap still gets its calls, each after the one before has had time to end.
        short = self.lap(seconds=80)
        self.assertEqual(short[-1][1], 'closing')

    def test_only_what_is_known_is_said(self):
        bare = {**{name: None for name in FACTS}, 'wet': False, 'dry': False, 'multiclass': False}
        said = {name: dict(sentences) for _, name, _, sentences in self.lap(bare, calls=8)}
        for topic in ('race', 'fuel', 'weather', 'rules'):
            self.assertNotIn(topic, said)       # no measured fact for it: the opening words alone are not said
        self.assertEqual(set(said['start']), {'form_rolling', 'form_hold_station', 'form_first_corner'})
        self.assertIn('closing', said)
        wet = {name: dict(sentences) for _, name, _, sentences in self.lap({**FACTS, 'wet': True, 'dry': False}, calls=8)}
        self.assertIn('form_wet', wet['weather'])
        self.assertNotIn('form_dry', wet['weather'])
        laps = dict(self.lap({**FACTS, 'race_min': None, 'race_laps': 30})[0][3])
        self.assertEqual(laps['form_length_laps'], {'laps': 30})
        self.assertNotIn('form_length_min', laps)

    def test_nothing_without_a_formation_lap(self):
        self.assertEqual(self.lap(speed=0.), [])                     # waiting on the grid
        self.assertEqual(self.lap(state=4), [])                      # racing
        self.assertEqual(self.lap(state=2), [])                      # warm-up of a standing start
        self.assertEqual(self.lap(on_pit_road=True), [])
        self.assertEqual(self.lap(is_replay=True), [])

    def test_every_sentence_builds_is_prepared_and_reads_cleanly_on_screen(self):
        prepared = {text for _, text in radio_phrases.catalog()}
        values = {'min': 45, 'laps': 30, 'pos': 7, 'count': 23, 'liters': 55, 'kph': 80, 'sec': 25, 'temp': 20, 'points': 17, 'nth': 3,
                  'lap': 239.6, 'turn': '5번 코너'}
        for name, role, sentences in [*TOPICS, CLOSING]:
            for key, _ in sentences:
                owner, wordings = radio_phrases.PHRASES[key]
                self.assertEqual(owner, role, key)                                   # one voice for one topic
                self.assertGreaterEqual(len(wordings), 1 if key in radio_phrases.SINGLE_WORDING else 3, key)
                for seed in range(len(wordings)):
                    text = radio_phrases.build(key, seed, **values)['text']
                    self.assertIn(text, prepared, key)
                    self.assertNotIn('[', shown_text(text))
                    self.assertNotIn('  ', shown_text(text))


class RaceHistoryTests(unittest.TestCase):
    IDENTITY = {'car_id': '188', 'track_id': '268', 'track_config': '', 'car_path': 'mclaren720sgt3', 'track_name': 'Le Mans'}

    def test_a_finished_race_is_kept_per_car_and_track_and_read_back_after_a_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'race-history.json'
            history = RaceHistory(path)
            self.assertIsNone(history.last(self.IDENTITY))
            self.assertTrue(history.add(self.IDENTITY, {'class_position': 6, 'class_cars': 23, 'best_lap_s': 241.2, 'track_temp_c': 28., 'at': 100.}))
            self.assertTrue(history.add(self.IDENTITY, {'class_position': 4, 'class_cars': 21, 'best_lap_s': 239.6, 'track_temp_c': 25., 'at': 200.}))
            self.assertFalse(history.add({'car_id': '', 'track_id': ''}, {'class_position': 1}))          # nothing to file it under
            again = RaceHistory(path)
            self.assertEqual(again.last(self.IDENTITY), {'class_position': 4., 'class_cars': 21., 'best_lap_s': 239.6, 'track_temp_c': 25., 'at': 200.})
            self.assertIsNone(again.last({**self.IDENTITY, 'car_id': '132'}))                               # another car is another history

    def test_last_time_is_told_from_the_newer_record_and_only_what_it_holds(self):
        race = {'class_position': 4, 'best_lap_s': 239.6, 'track_temp_c': 25., 'at': 200.}
        analysed = {'best_lap_s': 240.4, 'track_temp_c': 31., 'at': 150., 'losses': ['5번 코너', '13번 코너']}
        facts = history_facts(race, analysed, 20.)
        self.assertEqual((facts['hist_lap'], facts['hist_pos'], facts['hist_temp_lower'], facts['hist_temp_higher']), (239.6, 4, 5, None))
        self.assertEqual((facts['hist_loss_turn'], facts['hist_loss_turn2']), ('5번 코너', '13번 코너'))
        # Analysed after that race (a practice since): its lap and temperature, and no place to go with them.
        newer = history_facts(race, {**analysed, 'at': 300.}, 32.)
        self.assertEqual((newer['hist_lap'], newer['hist_pos'], newer['hist_temp_same'], newer['hist_temp_lower']), (240.4, None, True, None))
        # Never here before: nothing, and the topics are skipped.
        nothing = history_facts(None, None, 20.)
        self.assertEqual([name for name, value in nothing.items() if value], ['hist_first'])   # only that there is no record
        self.assertFalse(any(history_facts(None, None, 20., known=False).values()))             # records unreadable: say nothing
        # The second to the tenth time are counted; from the eleventh on it is a track he knows well.
        told = lambda races, sessions: (lambda f: (f['hist_nth'], f['hist_familiar']))(history_facts(race, {**analysed, 'sessions': sessions}, 20., races=races))
        self.assertEqual([told(1, 0), told(1, 1), told(2, 1), told(1, 4), told(9, 2), told(10, 2), told(0, 25)],
                         [(2, False), (2, False), (3, False), (5, False), (10, False), (None, True), (None, True)])
        brief = FormationBrief(8)
        names = [topic[0] for topic in brief.planned({**FACTS, **{k: v for k, v in nothing.items() if v}}, 'run')]
        self.assertNotIn('history', [topic[0] for topic in brief.planned(FACTS, 'run')])        # nothing known about the past at all
        self.assertIn('history', names)                                                        # "no record yet" is worth saying once
        self.assertNotIn('history_race', names)

    def test_the_history_topics_say_the_lap_the_place_the_track_and_the_corners(self):
        facts = {**FACTS, **history_facts({'class_position': 4, 'best_lap_s': 239.6, 'track_temp_c': 25., 'at': 200.},
                                          {'best_lap_s': 240.4, 'at': 150., 'losses': ['5번 코너', '13번 코너'], 'sessions': 7}, 20., races=3)}
        known = {name: value for name, value in facts.items() if value is not None and value is not False}
        plan = {topic[0]: topic for topic in FormationBrief(10).planned(known, 'run')}
        from live_formation import usable
        said = [radio_phrases.build(key, 0, **values)['text'] for key, values in usable(plan['history'], known)]
        self.assertEqual(said, ['이 트랙은 이번이 여덟 번째네요.', '이 트랙의 지난번 베스트입니다.', '3분 59초 6입니다.', '오늘 노면은 그때보다 5도 낮습니다.'])
        loss = [radio_phrases.build(key, 0, **values)['text'] for key, values in usable(plan['history_race'], known)]
        self.assertEqual(loss, ['지난번에는 클래스 4위로 마쳤습니다.', '지난번에는 5번 코너에서 가장 많이 잃었습니다.', '13번 코너에서도 잃었습니다.'])


class FormationRadioTests(unittest.TestCase):
    META = {'session_type': 'Race', 'session_duration_s': 2700., 'pit_speed_limit_kph': 80., 'incident_limit': 17}

    def test_a_topic_reaches_the_radio_with_its_sentences_and_is_gone_at_the_green(self):
        now = [0.]
        live = LiveTelemetry(clock=lambda: now[0])
        identity = {'car_id': '7', 'track_id': '9', 'track_config': '', 'car_path': 'car', 'track_path': 'track'}
        base = {'session_num': 0, 'lap_number': 0, 'brake': 0., 'throttle': .4, 'steering_rad': 0., 'incidents': 0, 'session_flags': 0,
                'fuel_l': 55., 'class_position': 7, 'track_temp_c': 20., 'air_temp_c': 19., 'track_wetness': 1, 'declared_wet': False}
        heard = []
        for tick in range(0, 80):
            now[0] = tick / 3
            live.radio.metadata.update(self.META)
            live.ingest(identity, {**sample(.95 + tick / 600), **base, 'session_time': now[0]})
            live.radio.metadata.update(self.META)
            for event in live.radio.view(now[0])['events']:
                if event['key'] == 'formation_brief' and event['id'] not in [e['id'] for e in heard]:
                    heard.append(event)
                    live.radio.ack(event['id'], now[0])
        self.assertGreaterEqual(len(heard), 1)
        first = heard[0]
        self.assertEqual(first['engineer'], 'race')
        self.assertIn('45분', first['speech_text'])
        self.assertIn('클래스 7위', first['speech_text'])
        # The wording is drawn for the race, not counted up from the first one.
        opening = {radio_phrases.build('form_hello', __import__('random').Random(f'race {n}:form_hello').randrange(1000))['text'] for n in range(20)}
        self.assertEqual(len(opening), 3)
        self.assertNotIn('[pause', first['text'])                    # the screen text has no marks
        self.assertGreaterEqual(len(first['speech_parts']), 3)
        self.assertTrue(live.radio.view(now[0])['quiet'])            # the formation lap counts as a calm stretch
        # A topic still waiting when the green comes is withdrawn.
        live.formation.next_at, live.formation.said, live.formation.driven = 0., 1, .5
        now[0] += .3
        live.radio.metadata.update(self.META)
        live.ingest(identity, {**sample(.2), **base, 'session_time': now[0]})
        waiting = [e for e in live.radio.pending if e['key'] == 'formation_brief']
        self.assertTrue(waiting)
        now[0] += .3
        live.ingest(identity, {**sample(.21, state=4), **base, 'session_time': now[0]})
        self.assertFalse(any(live.radio.valid(e, now[0]) for e in waiting))


if __name__ == '__main__':
    unittest.main()
