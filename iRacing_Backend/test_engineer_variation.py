"""Varied engineer wording, number fragments, and the calls added on 2026-10-10."""
import asyncio
import base64
import copy
import io
import math
import os
import tempfile
import unittest
import wave
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import radio_phrases
from live_controls import ControlAdvisor, control_answer
from live_telemetry import LiveTelemetry
from race_engineer import RaceEngineer, read_race
from race_radio import RaceRadio
from radio_audio import CALLS, RadioAudioCache, VARIANTS, fixed_catalog, speech_fields, spoken_form, texts
from race_radio import STATIC_CALLS
from spotter import VARIANTS as SPOTTER_VARIANTS
from test_adaptive_pitwall import race_fixture, sample
from voice_coach import FEELING_SAVED, GoogleSpeech, Utterance, VoiceCoach, control_question
from voice_notes import VoiceNoteStore


def wav(seconds, rate=24000, value=1000):
    data = io.BytesIO()
    with wave.open(data, 'wb') as clip:
        clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(rate)
        clip.writeframes(value.to_bytes(2, 'little', signed=True) * int(rate * seconds))
    return base64.b64encode(data.getvalue()).decode()


class FakeSpeech:
    def __init__(self):
        self.calls = []

    def synthesize(self, text, role='coach', encoding='mp3'):
        self.calls.append((role, text, encoding))
        return wav(.5) if encoding == 'wav' else base64.b64encode(b'ID3' + text.encode() + b'x' * 200).decode()


class PhraseTests(unittest.TestCase):
    def test_every_spotter_voice_has_the_same_wordings_and_its_own_clips(self):
        with patch.dict(os.environ, {'IRACING_TTS_SPOTTER_VOICE': ''}):
            self.assertEqual(radio_phrases.spotter_voice(), 'Fenrir')
            default = {radio_phrases.build('lap_time_read', seed, lap=102.3)['text'] for seed in range(12)}
            everything = len(radio_phrases.catalog())
        with patch.dict(os.environ, {'IRACING_TTS_SPOTTER_VOICE': 'ko-KR-Chirp3-HD-Aoede'}):
            self.assertEqual(radio_phrases.spotter_voice(), 'Aoede')
            fewer = {radio_phrases.build('lap_time_read', seed, lap=102.3)['text'] for seed in range(12)}
            self.assertEqual(fewer, default)                                     # all six in every voice (the driver's choice)
            self.assertEqual(len(radio_phrases.catalog()), everything)
            # The engineers keep all their wordings whatever the spotter sounds like.
            self.assertEqual(len(radio_phrases.wordings('gap_ahead_closing')[1]), len(radio_phrases.PHRASES['gap_ahead_closing'][1]))
        self.assertEqual(len(default), 6)
        with patch.dict(os.environ, {'IRACING_TTS_SPOTTER_VOICE': 'ko-KR-Chirp3-HD-Somebody'}):
            self.assertEqual(radio_phrases.spotter_voice(), 'Fenrir')            # not one that can be chosen

    def test_every_variant_builds_and_missing_values_are_never_guessed(self):
        values = {name: 3.4 for name in radio_phrases.FORMATS}
        for key, (role, variants) in radio_phrases.PHRASES.items():
            self.assertIn(role, ('coach', 'race', 'strategy', 'spotter'))
            self.assertGreaterEqual(len(variants), 1 if key in radio_phrases.SINGLE_WORDING else 2, key)
            self.assertEqual(len(set(variants)), len(variants), key)
            for seed in range(len(variants)):
                built = radio_phrases.build(key, seed, **values)
                self.assertEqual(built['engineer'], role)
                self.assertNotIn('{', built['text'])
        with self.assertRaises(KeyError):
            radio_phrases.build('gap_ahead_closing')

    def test_spoken_text_has_no_commas(self):
        # Heard in the listening pilots: "3.1초, 랩마다" ran together as "3.1초랩". A number either
        # ends its sentence or carries on in a natural one ("5분 남았습니다"); it is never followed by a comma.
        for key, (_, variants) in radio_phrases.PHRASES.items():
            for template in variants:
                self.assertNotIn(',', template, key)
        spoken = [text for _, text in fixed_catalog()] + [text for options in STATIC_CALLS.values() for text in options]
        spoken += [text for options in SPOTTER_VARIANTS.values() for text in options]
        # The one approved comma: a repeated call word, which a full stop split too far apart.
        self.assertEqual([text for text in spoken if ',' in text.replace('박스, 박스.', '')], [])

    def test_wording_rotates_and_lap_times_are_read_in_tenths(self):
        first = radio_phrases.build('gap_behind_closing', 0, gap=1.84)['text']
        second = radio_phrases.build('gap_behind_closing', 1, gap=1.84)['text']
        self.assertEqual((first, second), ('뒤차 1.8초. 가까워지고 있습니다.', '뒤차 간격 1.8초. 줄고 있습니다.'))
        self.assertEqual(radio_phrases.build('quali_result', 0, pos=3, gap=.31)['text'], '클래스 3위. 선두와 0.3초 차.')
        for seconds, spoken in [(102.34, '1분 42초 3'), (58.3, '58초 3'), (59.96, '1분 0초 0'), (492.4, '8분 12초 4'), (238.6, '3분 58초 6')]:
            self.assertEqual(radio_phrases.lap_time(seconds), spoken)
        self.assertEqual(radio_phrases.build('lap_time_read', 0, lap=102.34), {'engineer': 'spotter', 'text': '1분 42초 3입니다.'})

    def test_lap_is_joined_to_the_word_before_it_only_in_the_voice(self):
        # Heard as "최근 … 랩", "두 … 랩" with a space; the screen text keeps the space.
        for shown, voiced in [('같은 클래스 뒤차의 최근 랩 페이스가 더 빠릅니다.', '같은 클래스 뒤차의 최근랩 페이스가 더 빠릅니다.'),
                              ('두 랩 안에 피트합니다.', '두랩 안에 피트합니다.'), ('마지막 랩. 그대로 마무리하세요.', '마지막랩. 그대로 마무리하세요.'),
                              ('랩타임. 1분 42초 3.', '랩타임. 1분 42초 3.'), ('지금 랩타임을 벌어 둡시다.', '지금 랩타임을 벌어 둡시다.'),
                              ('다섯 랩쯤 뒤에 피트합니다.', '다섯랩쯤뒤에 피트합니다.'),
                              # Digits before 위 and 랩 were stretched ("오…위", "오…랩"); written out they were not.
                              ('5랩 남았습니다.', '다섯랩 남았습니다.'), ('이제 10랩 남았습니다.', '이제 열랩 남았습니다.'), ('21랩', '스물한랩'),
                              ('20랩', '스무랩'), ('클래스 5위로 내려갔습니다.', '클래스 오위로 내려갔습니다.'), ('현재 클래스 25위입니다.', '현재 클래스 이십오위입니다.'),
                              ('클래스 1위입니다. 2위와 0.3초 차.', '클래스 일위입니다. 이위와 0.3초 차.'), ('클래스 12위.', '클래스 십이위.'),
                              ('남은 연료 6.5랩분.', '남은 연료 6.5랩분.'), ('1분 45초 5입니다.', '1분 45초 5입니다.'),
                              ('완주까지 5리터 부족합니다.', '완주까지 오리터 부족합니다.'), ('급유 설정은 20리터입니다. 35리터 넣어야 합니다.', '급유 설정은 이십리터입니다. 삼십오리터 넣어야 합니다.'),
                              ('이번 랩은 2.3리터 썼습니다. 목표는 2.5리터입니다.', '이번랩은 2.3리터 썼습니다. 목표는 2.5리터입니다.'),
                              ('견인 중입니다. 45초 남았습니다.', '견인 중입니다. 45초 남았습니다.'), ('5분 남았습니다.', '5분 남았습니다.')]:
            self.assertEqual(spoken_form(shown), voiced)
        speech = GoogleSpeech()
        self.assertEqual(RadioAudioCache(speech, 'x').path('race', '두 랩 안에 피트합니다.'), RadioAudioCache(speech, 'x').path('race', '두랩 안에 피트합니다.'))

    def test_repeated_feeling_names_the_input_to_change(self):
        for key in ('coach_understeer', 'coach_understeer_entry', 'coach_understeer_exit', 'coach_oversteer',
                    'coach_oversteer_entry', 'coach_oversteer_exit', 'coach_braking'):
            text = CALLS[key][1]
            self.assertNotIn('입력 하나', text)
            self.assertIn('다음에는', text)  # "다음 통과에서는" read poorly
            self.assertTrue(any(word in text for word in ('브레이크', '가속', '핸들', '진입 속도', '제동을')), key)
        self.assertEqual(len({CALLS[key][1] for key in CALLS if key.startswith('coach_')}), 7)
        self.assertNotIn('오케이.', radio_phrases.ACKS)

    def test_fixed_calls_have_variants_that_keep_the_first_wording_first(self):
        for key in VARIANTS:
            self.assertIn(key, CALLS)
            self.assertEqual(texts(key)[0], CALLS[key][1])
            self.assertEqual(len(set(texts(key))), len(texts(key)))
        self.assertEqual(speech_fields('fuel_short'), {'speech_key': 'fuel_short', 'spoken_text': CALLS['fuel_short'][1]})
        self.assertEqual(speech_fields('fuel_short', 1), {'speech_key': 'fuel_short#1', 'spoken_text': VARIANTS['fuel_short'][0]})
        self.assertEqual(speech_fields('fuel_short', len(texts('fuel_short')))['speech_key'], 'fuel_short')
        self.assertEqual(speech_fields('unknown'), {'speech_key': None, 'spoken_text': None})


class ClipCacheTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        env = patch.dict('os.environ', {'IRACING_RADIO_AUDIO_DIR': ''})
        env.start(); self.addCleanup(env.stop)
        self.speech = FakeSpeech()
        self.cache = RadioAudioCache(self.speech, Path(self.work.name))

    def test_each_whole_sentence_is_one_request_cached_per_voice_and_wording(self):
        sentence = radio_phrases.build('gap_ahead_closing', 0, gap=1.8)
        first = self.cache.phrase(sentence['engineer'], sentence['text'])
        self.assertEqual(self.speech.calls, [('race', '앞차 1.8초. 줄고 있습니다.', 'mp3')])
        self.assertEqual(self.cache.phrase('race', sentence['text']), first)
        self.assertNotEqual(self.cache.phrase('race', radio_phrases.build('gap_ahead_closing', 0, gap=1.7)['text']), first)
        self.cache.phrase('strategy', sentence['text'])  # another voice is another clip
        self.assertEqual(len(self.speech.calls), 3)
        restarted = RadioAudioCache(FakeSpeech(), Path(self.work.name))
        self.assertTrue(restarted.cached('race', sentence['text']))
        self.assertEqual(restarted.phrase('race', sentence['text']), first)
        self.assertEqual(restarted.speech.calls, [])

    def test_bad_audio_is_rejected_and_not_kept(self):
        broken = MagicMock()
        broken.synthesize.return_value = base64.b64encode(b'short').decode()
        cache = RadioAudioCache(broken, Path(self.work.name) / 'other')
        with self.assertRaises(ValueError):
            cache.phrase('race', '뒤차 1.8초. 가까워지고 있습니다.')
        self.assertEqual(list((Path(self.work.name) / 'other').glob('*.mp3')), [])
        self.assertEqual(cache.pending, {})
        broken.synthesize.return_value = base64.b64encode(b'ID3' + b'x' * 300).decode()
        self.assertTrue(cache.phrase('race', '뒤차 1.8초. 가까워지고 있습니다.'))

    def test_variant_clip_keys_and_plain_phrases(self):
        self.assertEqual(self.cache.clip('fuel_short#1'), self.cache.phrase('strategy', VARIANTS['fuel_short'][0]))
        self.assertEqual(len(self.speech.calls), 1)
        self.assertNotEqual(self.cache.clip('fuel_short'), self.cache.clip('fuel_short#1'))


class EngineerReportTests(unittest.TestCase):
    def setUp(self):
        self.race = race_fixture()
        self.wall = RaceEngineer()
        self.wall.ingest(self.race, 'battle', 0)
        self.wall.paces = {i: deque([100.] * 3) for i in range(3)}

    def anchor(self, lap, gap):
        for pct, at in ((.98, lap * 100 - 2), (.01, lap * 100)):
            race = copy.deepcopy(self.race)
            own_lap = lap + 2 if pct == .98 else lap + 3
            race['cars'][1].update(lap_completed=own_lap, distance_pct=pct)
            behind = own_lap + pct - gap / 100
            race['cars'][2].update(lap_completed=math.floor(behind), distance_pct=behind % 1)
            self.wall.ingest(race, 'battle', at)
        self.snapshot = sample(at, lap + 4, pct, fuel_l=40., run_id='battle')

    def reports(self, **snapshot):
        self.snapshot.update(snapshot)
        return {r['key']: r for r in self.wall.candidates(self.wall.view(self.snapshot, 2, 3), self.snapshot, {})}

    def test_only_a_shrinking_gap_is_reported_with_the_gap_measured_at_the_line(self):
        for lap, gap in enumerate((4., 3.5, 3.), 1):
            self.anchor(lap, gap)
        report = self.reports()['battle_behind']
        self.assertEqual((report['speech_text'], report['spoken_text'], report['engineer']),
                         ('뒤차 3.0초. 가까워지고 있습니다.', '뒤차 3.0초. 가까워지고 있습니다.', 'race'))
        self.assertIn('랩당 약 0.5초씩 줄고', report['text'])
        self.assertIsNone(report['speech_key'])
        # The wording changes only after the call was really delivered.
        self.assertEqual(self.reports()['battle_behind']['spoken_text'], report['spoken_text'])
        self.wall.delivered(report, self.snapshot['session_time'])
        self.assertEqual(self.reports()['battle_behind']['spoken_text'], '뒤차 간격 3.0초. 줄고 있습니다.')

    def test_a_growing_gap_is_not_reported(self):
        for lap, gap in enumerate((3., 3.5, 4.), 1):
            self.anchor(lap, gap)
        self.assertEqual(self.wall.view(self.snapshot, 2, 3)['behind']['gap_trend'], 'opening')
        self.assertNotIn('battle_behind', self.reports())

    def test_pit_countdown_and_fuel_setting_check_follow_measured_fuel(self):
        self.anchor(1, 4.)
        self.assertNotIn('pit_soon', self.reports(fuel_l=40.))
        soon = self.reports(fuel_l=10.)
        self.assertEqual(list(soon)[:1], ['pit_soon'])  # too short to save: the countdown is the fuel message
        self.assertNotIn('fuel_short', soon)
        self.assertEqual(soon['pit_soon']['priority'], 2)
        self.assertNotIn('pit_two', soon)
        two = self.reports(fuel_l=4.6)
        self.assertIn('pit_two', two)
        self.assertNotIn('pit_fuel_check', two)  # pit service values are not available
        need = self.wall.view(self.snapshot, 2, 3)['fuel_to_add_l']
        low = self.reports(pit_sv_fuel=2., pit_sv_flags=0x10)['pit_fuel_check']
        self.assertEqual(low['speech_text'], f'급유 설정은 2리터입니다. {round(need)}리터 넣어야 합니다.')
        self.assertNotIn('pit_fuel_check', self.reports(pit_sv_fuel=need + 1, pit_sv_flags=0x10))
        unticked = self.reports(pit_sv_fuel=need + 1, pit_sv_flags=0)['pit_fuel_check']
        self.assertIn('설정은 0리터입니다.', unticked['speech_text'])

    def test_halfway_and_last_lap(self):
        self.anchor(1, 4.)
        view = self.wall.view(self.snapshot, 2, 3)
        self.assertIsNotNone(view['race_fraction'])
        build = lambda view, **snap: {r['key']: r for r in self.wall.candidates(view, {**self.snapshot, **snap}, {})}
        self.assertNotIn('halfway', build({**view, 'race_fraction': .45}))
        self.assertEqual(build({**view, 'race_fraction': .52})['halfway']['spoken_text'], '절반 지났습니다. 클래스 2위.')
        self.assertNotIn('last_lap', build(view))
        last = build({**view, 'behind': {**view['behind'], 'gap_estimate_s': 6.}}, session_flags=0x2)['last_lap']
        self.assertEqual((last['priority'], last['spoken_text']), (2, '마지막 랩입니다. 끝까지 깔끔하게.'))
        defend = build({**view, 'behind': {**view['behind'], 'gap_estimate_s': .8}}, session_flags=0x2)['last_lap']
        self.assertIn('뒤차가 바로 뒤', defend['spoken_text'])

    def test_read_race_carries_limits_tyres_and_best_laps(self):
        sdk = {'DriverInfo': {'DriverCarIdx': 0, 'Drivers': [{'CarIdx': 0, 'CarClassID': 1}, {'CarIdx': 1, 'CarClassID': 1}],
                              'DriverTires': [{'TireIndex': 0, 'TireCompoundType': 'Hard'}, {'TireIndex': 1, 'TireCompoundType': 'Wet'}]},
               'SessionInfo': {'Sessions': [{'SessionNum': 0, 'SessionType': 'Lone Qualify'}]},
               'WeekendInfo': {'WeekendOptions': {'IncidentLimit': 17}}, 'CarIdxBestLapTime': [91.2, 90.9]}
        data = read_race(sdk, {'CarIdxBestLapTime'}, 0)
        self.assertEqual((data['incident_limit'], data['wet_compounds'], data['tire_types_known']), (17., [1], True))
        self.assertEqual([car['best_lap_s'] for car in data['cars']], [91.2, 90.9])
        sdk['WeekendInfo']['WeekendOptions']['IncidentLimit'] = 'unlimited'
        sdk['DriverInfo'].pop('DriverTires')
        data = read_race(sdk, set(), 0)
        self.assertEqual((data['incident_limit'], data['wet_compounds'], data['tire_types_known']), (None, [], False))


def car(idx, best=None, class_id=1):
    return {'idx': idx, 'name': f'driver {idx}', 'class_id': class_id, 'lap_completed': 3, 'distance_pct': .5,
            'last_lap_s': 90., 'best_lap_s': best, 'track_surface': 3, 'on_pit_road': False}


class RadioCallTests(unittest.TestCase):
    def setUp(self):
        self.radio, self.now = RaceRadio(), 1000.
        self.sample = {'session_time': 0., 'session_num': 0, 'session_state': 4, 'session_flags': 0, 'lap_number': 4, 'lap_completed': 3,
                       'distance_pct': .5, 'speed_mps': 50., 'brake': 0., 'throttle': 1., 'steering_rad': 0., 'incidents': 0,
                       'engine_warnings': 0, 'lap_last_s': 90., 'lap_best_s': 90., 'lap_current_s': 30., 'class_position': 2,
                       'on_track': True, 'is_replay': False, 'on_pit_road': False}
        self.meta = {'session_type': 'Race', 'player_idx': 0, 'cars': [car(0, 90.)]}

    def tick(self, advance=.1, **patch):
        self.now += advance
        self.sample.update(patch, session_time=self.now - 1000)
        self.radio.ingest(dict(self.sample), 'run', self.now, self.meta)

    def wait(self, seconds):
        for _ in range(round(seconds / .1)):
            self.tick()

    def events(self):
        return {e['key']: e for e in self.radio.view(self.now)['events']}

    def lap(self, seconds):
        # As in the sim: the lap counter moves first, the published lap time follows.
        self.tick(lap_completed=self.sample['lap_completed'] + 1, lap_number=self.sample['lap_number'] + 1)
        self.tick(lap_last_s=seconds, lap_best_s=min(seconds, self.sample['lap_best_s']), lap_current_s=.1)

    def test_qualifying_speaks_only_the_lap_its_result_coaching_and_warnings(self):
        self.meta.update(session_type='Lone Qualify', cars=[car(0, 91.), car(1, 90.7), car(2, 90.9), car(3, 80., class_id=2)])
        self.tick()
        self.lap(91.)
        self.assertEqual(list(self.events()), [])  # the lap time waits a moment for the standings
        # Other cars are on their own laps: never called, although these are urgent calls in a race.
        for key in ('hazard_slow_ahead', 'hazard_off_ahead', 'close_behind', 'traffic_faster_behind'):
            self.radio.emit(key, self.now, priority=2, cooldown=0)
        self.radio.emit('position', self.now, '클래스 3위.', cooldown=0)
        self.radio.emit('corner_focus', self.now, '다음 코너. 제동이 일렀습니다.', role='coach', cooldown=0)
        self.assertEqual(sorted(self.events()), ['corner_focus'])
        self.wait(1.6)
        result = self.events()['quali_result']
        # One call: the lap time, then where it stands.
        self.assertEqual((result['speech_parts'], result['engineer']), (['1분 31초 0입니다.', '클래스 3위. 선두와 0.3초 차.'], 'race'))
        self.assertTrue(result['text'].endswith('클래스 3위. 선두와 0.3초 차.'))
        self.assertNotIn('lap_time', self.events())
        self.wait(1)
        self.assertEqual(len([e for e in self.radio.pending if e['key'] == 'quali_result']), 1)  # once per completed lap
        self.tick(session_flags=0x100000)
        self.assertIn('repair', self.events())
        # Of the flags only green, white and the chequered flag matter on a lone lap.
        self.radio.pending.clear()
        for bits in (0x8, 0x4000, 0x20, 0x40, 0x10):   # yellow, caution, blue, debris, red
            self.tick(session_flags=bits)
            self.tick(session_flags=0)
            self.wait(11)
        self.assertEqual(self.events(), {})
        for bits, key in ((0x4, 'green'), (0x2, 'white'), (0x1, 'finish')):
            self.tick(session_flags=bits)
            self.assertIn(key, self.events())
            self.tick(session_flags=0)
            self.wait(11)
        self.radio.pending.clear()
        self.meta['cars'][0]['best_lap_s'] = 90.5
        self.lap(90.5)
        self.wait(1.6)
        pole = self.events()['quali_p1']
        self.assertIn('1분 30초 5', pole['speech_parts'][0])
        self.assertEqual(pole['speech_parts'][1], '클래스 1위입니다. 2위와 0.2초 차.')

    def test_places_lost_during_a_pit_stop_are_said_once_back_on_track(self):
        self.tick(class_position=3)
        self.wait(6)
        self.tick(on_pit_road=True)
        for place in (4, 6, 9):
            self.tick(class_position=place)
            self.wait(8)
            self.assertNotIn('position', self.events())   # no running commentary while stationary
        self.tick(on_pit_road=False)
        self.wait(4)
        self.assertNotIn('position', self.events())       # the place must hold for five seconds first
        self.wait(1.5)
        self.assertEqual(self.events()['position']['speech_text'], '클래스 9위로 내려갔습니다.')

    def test_debris_is_called_every_time_the_sim_raises_the_flag(self):
        def flag(pct):
            self.tick(distance_pct=pct, session_flags=0x40)
            called = 'debris' in self.events()
            self.radio.pending.clear()
            self.tick(session_flags=0)
            self.wait(11)
            return called
        self.assertTrue(flag(.80))
        self.assertTrue(flag(.81))     # the same debris a lap later: the sim says it is still there
        self.assertTrue(flag(.30))

    def test_practice_and_race_do_not_get_the_qualifying_result(self):
        self.meta['cars'] = [car(0, 91.), car(1, 90.7)]
        self.tick()
        self.lap(91.)
        self.wait(2)
        lap_call = self.events()['lap_time']
        # The spotter now reads the time itself; the screen keeps the lap number and hundredths.
        self.assertEqual((lap_call['speech_text'], lap_call['engineer']), ('1분 31초 0입니다.', 'spotter'))
        self.assertIn('랩', lap_call['text'])
        self.assertFalse([key for key in self.events() if key.startswith('quali')])

    def test_incident_limit_and_repair_signal_follow_up(self):
        self.meta['incident_limit'] = 17.
        self.tick()
        self.tick(incidents=4)
        self.assertIn('incident', self.events())  # far from the limit: the plain call
        self.wait(9)
        self.tick(incidents=12)
        call = self.events()['incident_limit']
        self.assertEqual((call['text'], call['priority']), ('인시던트 12점. 한도까지 5점.', 1))
        self.wait(4.2)
        self.assertEqual(self.events()['no_repair']['engineer'], 'race')
        self.wait(9)
        self.tick(incidents=14, engine_warnings=0x80)
        self.assertEqual(self.events()['incident_limit']['priority'], 2)
        self.wait(4.2)
        self.assertNotIn('no_repair', [e['key'] for e in self.radio.pending if e['at'] > self.now - 2])

    def test_numbers_that_were_only_on_screen_are_now_spoken(self):
        self.tick(session_time_remaining_s=601., session_laps_remaining=6)
        self.tick(session_time_remaining_s=599.)
        self.assertEqual(self.events()['time_600']['speech_text'], '10분 남았습니다.')
        self.tick(session_laps_remaining=5)
        self.assertEqual(self.events()['laps_5']['speech_text'], '5랩 남았습니다.')  # voiced as 다섯랩
        self.wait(1)
        self.tick(class_position=4)
        self.wait(5.2)
        down = self.events()['position']
        self.assertEqual((down['speech_text'], down['text']), ('클래스 4위로 내려갔습니다.', '현재 클래스 4위입니다.'))
        self.wait(16)
        self.tick(class_position=3)
        self.wait(5.2)
        self.assertEqual(self.events()['position']['speech_text'], '클래스 3위로 올라섰습니다.')
        for seconds in (90.3, 90.2, 90.4):
            self.wait(1)
            self.lap(seconds)
        self.wait(1)
        self.lap(91.6)
        self.wait(5.2)
        lap_call = self.events()['lap_time']   # the time and the pace are one call now
        self.assertEqual(lap_call['speech_parts'][1:], ['최근 페이스보다 1.3초 느렸습니다.'])
        self.assertIn('1분 31초 6', lap_call['speech_parts'][0])
        self.wait(1)
        self.lap(89.5)
        self.assertEqual(self.events()['personal_best']['speech_text'], '세션 베스트입니다. 1분 29초 5입니다.')
        self.tick(tow_time_s=0.)
        self.tick(tow_time_s=45.)
        self.assertEqual(self.events()['tow']['speech_text'], '견인 중입니다. 45초 남았습니다.')

    def test_three_consistent_laps_are_praised_once(self):
        self.tick()
        for seconds in (90.3, 90.2, 90.1):
            self.wait(1)
            self.lap(seconds)
        self.wait(6.2)
        self.assertEqual(self.events()['consistent']['text'], '페이스 일정합니다. 최근 세 랩 편차 0.2초.')
        self.wait(1)
        self.lap(91.5)
        self.wait(6.2)
        self.assertNotIn('consistent', self.events())  # the praise belonged to the earlier lap

    def test_wet_track_and_tyre_advice_need_measured_state(self):
        self.tick(track_wetness=1, declared_wet=False, track_temp_c=30.)
        self.tick(track_wetness=3)
        self.assertIn('track_wet_start', self.events())
        self.assertNotIn('rain_prepare', self.events())  # wets are not allowed yet
        self.tick(declared_wet=True)
        self.assertNotIn('rain_prepare', self.events())  # the fitted compound is unknown: no tyre call
        self.tick(track_wetness=5)
        events = self.events()
        self.assertFalse([key for key in events if key.startswith('rain_')])
        self.assertEqual((events['track_very_wet']['engineer'], events['track_very_wet']['text']), ('race', '노면이 많이 젖었습니다. 그립 주의하세요.'))
        self.meta.update(tire_types_known=True, wet_compounds=[1])
        self.tick(track_wetness=4, tire_compound=0)
        self.wait(92)
        self.tick(track_wetness=5)
        self.assertIn('rain_box', self.events())
        self.tick(tire_compound=1)
        self.assertNotIn('rain_box', self.events())
        self.tick(track_wetness=2)
        self.assertIn('slick_box', self.events())
        self.tick(track_temp_c=24.6)
        self.assertEqual(self.events()['track_temp_down']['text'], '노면 온도가 내려갔습니다. 하락 폭 5도.')

    def test_light_rain_calls_the_stop_only_when_dry_pace_is_lost(self):
        self.tick(track_wetness=1, declared_wet=True)
        for seconds in (90., 90.1):
            self.wait(1)
            self.lap(seconds)
        self.meta.update(tire_types_known=True, wet_compounds=[1])
        self.tick(track_wetness=4, tire_compound=0)
        self.assertIn('rain_prepare', self.events())
        self.wait(1)
        self.lap(95.)
        self.assertIn('rain_box', self.events())

    def test_result_is_given_after_taking_the_chequered_flag(self):
        self.tick()
        self.tick(session_flags=0x1)
        self.tick(lap_completed=4, lap_number=5, class_position=3)
        self.wait(3.2)
        self.assertEqual(self.events()['race_result']['text'], '체커 받았습니다. 클래스 3위. 수고하셨습니다.')
        self.assertEqual(len([e for e in self.radio.pending if e['key'] == 'race_result']), 1)


def laps(advisor, count, brake_counter=0, power_counter=0, abs_share=.2, start_lap=1, clock=0., **settings):
    """Constant 60 s laps: 20 s of cornering, 8 s of braking, the rest on power."""
    settings = {'brake_bias': 54., 'abs_setting': 4, 'tc_setting': 5, **settings}
    for lap in range(start_lap, start_lap + count + 1):  # one extra crossing completes the last lap
        for index in range(1800):
            t = index / 30
            pct = index / 1800
            cornering, braking = 20 <= t < 40, 5 <= t < 13
            steer = yaw = .3 if cornering else 0.
            # A correction: steering against the rotation for 0.4 s.
            if any(6 + n * 2 <= t < 6.4 + n * 2 for n in range(brake_counter)) or any(45 + n * 2 <= t < 45.4 + n * 2 for n in range(power_counter)):
                steer, yaw = -.2, .3
            advisor.ingest({'distance_pct': pct, 'session_time': clock, 'lap_number': lap, 'speed_mps': 45.,
                            'steering_rad': steer, 'yaw_rate': yaw, 'brake': .8 if braking else 0., 'throttle': 0. if braking else 1.,
                            'abs_active': braking and (t - 5) / 8 < abs_share, 'on_track': True, 'is_replay': False, 'on_pit_road': False,
                            'incidents': 0, 'session_flags': 0, 'car_left_right': 1, **settings})
            clock += 1 / 30
            if lap == start_lap + count and index == 2:
                return clock
    return clock


class ControlAdviceTests(unittest.TestCase):
    def advice(self, advisor):
        return {item['control']: item for item in advisor.view()['advice']}

    def test_needs_three_clean_laps_with_one_setting(self):
        advisor = ControlAdvisor()
        clock = laps(advisor, 3)  # the lap being driven when the advisor starts is never counted
        self.assertEqual(len(advisor.laps), 2)
        self.assertFalse(advisor.view()['ready'])
        self.assertIn('세 랩', control_answer(advisor.view(), 'TC 어떻게 해?'))
        clock = laps(advisor, 1, start_lap=4, clock=clock)
        self.assertTrue(advisor.view()['ready'])
        laps(advisor, 1, start_lap=5, clock=clock, tc_setting=6)
        self.assertFalse(advisor.view()['ready'])  # evidence from the old setting is dropped

    def test_quiet_laps_only_offer_a_test_and_volunteer_nothing(self):
        advisor = ControlAdvisor()
        laps(advisor, 4)
        advice = self.advice(advisor)
        self.assertEqual({k: v['action'] for k, v in advice.items()}, {'brake_bias': 'rearward_test', 'tc': 'less_test', 'abs': 'hold'})
        self.assertTrue(all(item['call'] is None for item in advice.values()))
        self.assertEqual(advisor.view()['abs_duty_pct'], 20)

    def test_repeated_corrections_under_braking_and_power_are_volunteered(self):
        advisor = ControlAdvisor()
        laps(advisor, 5, brake_counter=2, power_counter=3, abs_share=.05)
        advice = self.advice(advisor)
        self.assertEqual((advice['brake_bias']['action'], advice['brake_bias']['call']), ('forward', 'control_bias_forward'))
        self.assertEqual((advice['tc']['action'], advice['tc']['call']), ('more', 'control_tc_more'))
        self.assertEqual(advice['abs']['action'], 'more')
        self.assertIn('랩당 2회', advice['brake_bias']['text'])
        answer = control_answer(advisor.view(), '브레이크 바이어스 추천해줘')
        self.assertIn('앞쪽으로 한 단계', answer)
        self.assertNotIn('TC', answer)

    def test_one_off_events_heavy_abs_missing_controls_and_traffic(self):
        advisor = ControlAdvisor()
        clock = laps(advisor, 2)
        laps(advisor, 2, brake_counter=1, start_lap=3, clock=clock, abs_share=.7)
        advice = self.advice(advisor)
        self.assertEqual(advice['brake_bias']['action'], 'hold')
        no_tc = ControlAdvisor()
        laps(no_tc, 4, abs_share=.7, tc_setting=None)
        self.assertEqual({k: v['action'] for k, v in self.advice(no_tc).items()}, {'brake_bias': 'rearward_test', 'abs': 'driver'})
        self.assertIn('읽지 못해', control_answer(no_tc.view(), 'TC 어떻게 해?'))
        beside = ControlAdvisor()
        clock = laps(beside, 3)
        beside.ingest({'distance_pct': .5, 'session_time': clock + .03, 'lap_number': 4, 'speed_mps': 45., 'steering_rad': 0., 'yaw_rate': 0.,
                       'brake': 0., 'throttle': 1., 'on_track': True, 'is_replay': False, 'on_pit_road': False, 'incidents': 0,
                       'session_flags': 0, 'car_left_right': 2, 'brake_bias': 54., 'abs_setting': 4, 'tc_setting': 5})
        self.assertFalse(beside.current['clean'])

    def test_question_detection_and_measured_answer(self):
        for text, expected in [('TC 어떻게 해야 해?', True), ('브레이크 바이어스 추천해줘', True), ('ABS 올려야 해?', True),
                               ('바이어스 지금 얼마야', False), ('TC 몇이야', False), ('연료 어떻게 돼', False)]:
            self.assertEqual(control_question(text), expected, text)
        advisor = ControlAdvisor()
        laps(advisor, 4)
        with tempfile.TemporaryDirectory() as folder:
            coach = VoiceCoach(LiveTelemetry(), VoiceNoteStore(Path(folder) / 'n.sqlite3'), speech=MagicMock())
            context = {'connected': True, 'snapshot': {}, 'controls': advisor.view(), 'engineer': 'race', 'pit_wall': {'active': True}}
            answer, source = coach.answer('TC 어떻게 해야 해?', context)
            self.assertEqual(source, 'MEASURED')
            self.assertIn('TC 개입을 한 단계 줄여', answer)

    def test_live_telemetry_volunteers_repeated_instability_once(self):
        now = [0.]
        live = LiveTelemetry(clock=lambda: now[0])

        class Feed:
            def ingest(self, sample_):
                now[0] = sample_['session_time']
                live.ingest({'car_id': '1', 'track_id': '1'}, {**sample_, 'session_num': 0, 'session_state': 4})
        laps(Feed(), 4, brake_counter=2)
        calls = [e for e in live.radio.pending if e['key'] == 'control_bias_forward']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['engineer'], 'coach')
        self.assertTrue(live.context()['controls']['ready'])


class AcknowledgementTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        env = patch.dict('os.environ', {'IRACING_RADIO_AUDIO_DIR': ''})
        env.start(); self.addCleanup(env.stop)
        self.speech = FakeSpeech()
        self.coach = VoiceCoach(LiveTelemetry(), VoiceNoteStore(Path(self.work.name) / 'notes.sqlite3'), speech=self.speech)

    def test_a_memo_is_acknowledged_with_a_short_fixed_word(self):
        seen = set()
        for index in range(30):
            result = asyncio.run(self.coach.utterance(Utterance(text=f'앞이 밀려 {index}', reply=False)))
            self.assertIn(result['ack'], radio_phrases.ACKS)
            self.assertTrue(result['audio_base64'])
            seen.add(result['ack'])
        self.assertGreater(len(seen), 3)  # varied
        self.assertEqual(len(self.speech.calls), len(seen))  # each word is synthesized once, then cached
        self.assertTrue(all(len(text) <= 14 for _, text, _ in self.speech.calls))

    def test_recorded_feeling_keeps_its_text_reply_but_only_the_acknowledgement_is_spoken(self):
        live = self.coach.live
        live.ingest({'car_id': '1'}, {'session_time': 1., 'session_num': 0, 'lap_number': 1, 'distance_pct': .2, 'on_track': True, 'is_replay': False})
        live.clock = lambda: live.samples[-1]['wall_time']
        result = asyncio.run(self.coach.utterance(Utterance(text='이 코너가 어려워')))
        self.assertEqual(result['note']['reply'], FEELING_SAVED)
        self.assertIn(result['ack'], radio_phrases.ACKS)
        self.assertEqual([text for _, text, _ in self.speech.calls], [result['ack']])

    def test_silent_mode_and_real_answers_are_not_acknowledged(self):
        result = asyncio.run(self.coach.utterance(Utterance(text='앞이 밀려', reply=False, speak=False)))
        self.assertIsNone(result['ack'])
        self.assertEqual(self.speech.calls, [])
        result = asyncio.run(self.coach.utterance(Utterance(text='연료 몇 랩 남았어?')))
        self.assertIsNone(result['ack'])


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        env = patch.dict('os.environ', {'IRACING_RADIO_AUDIO_DIR': ''})
        env.start(); self.addCleanup(env.stop)
        # The real catalog has thousands of sentences; the job logic is the same on a small one.
        small = patch.multiple(radio_phrases, RANGES={**radio_phrases.RANGES, 'lap': radio_phrases.tenths(90, 91), 'gap': radio_phrases.tenths(.1, .5),
                                                      'pos': range(1, 4), 'points': range(1, 3), 'left': range(1, 3), 'deg': range(4, 6)},
                               PREPARED={'pit_fuel_low': None})
        small.start(); self.addCleanup(small.stop)
        self.speech = FakeSpeech()
        self.coach = VoiceCoach(LiveTelemetry(), VoiceNoteStore(Path(self.work.name) / 'notes.sqlite3'), speech=self.speech)
        self.job = self.coach.audio_preparation

    def finish(self):
        self.job.join(timeout=60)
        self.assertFalse(self.job.running())

    def test_catalog_holds_whole_sentences_for_the_prepared_ranges(self):
        sentences = set(radio_phrases.catalog())
        self.assertIn(('race', '앞차 0.3초. 줄고 있습니다.'), sentences)
        self.assertIn(('race', '클래스 2위. 선두와 0.5초 차.'), sentences)
        self.assertIn(('spotter', '1분 30초 7입니다.'), sentences)
        self.assertNotIn(('race', '앞차 0.6초. 줄고 있습니다.'), sentences)  # outside the prepared range: first use
        self.assertFalse([text for _, text in sentences if '급유 설정' in text])  # two free numbers: first use only
        fixed = set(fixed_catalog())
        self.assertTrue({(CALLS[key][0], text) for key in CALLS for text in texts(key)} <= fixed)
        self.assertTrue({(role, ack) for role in ('coach', 'race', 'strategy') for ack in radio_phrases.ACKS} <= fixed)

    def test_real_catalog_matches_the_agreed_gt3_lap_time_windows(self):
        self.addCleanup(patch.stopall)
        patch.stopall()
        laps = radio_phrases.RANGES['lap']
        self.assertEqual((laps[0], laps[-1], len(laps)), (40.0, 570.0, 1701 + 401 + 1801))
        self.assertNotIn(220.0, laps)  # between ordinary circuits and Le Mans
        self.assertIn(238.6, laps)     # Le Mans GT3
        self.assertIn(479.5, laps)     # Nordschleife VLN GT3
        self.assertGreater(len(radio_phrases.catalog()), 15000)

    def test_everything_is_fetched_once_and_then_driving_needs_no_synthesis(self):
        before = self.job.status()
        self.assertEqual((before['state'], before['ready']), ('IDLE', 0))
        self.assertEqual(before['characters_missing'], sum(len(text) for _, text in self.job.items()))
        self.job.start()
        self.finish()
        self.job._ready = (0., None)
        done = self.job.status()
        self.assertEqual((done['state'], done['missing'], done['failed']), ('COMPLETE', 0, 0))
        self.assertEqual(len(self.speech.calls), done['total'])
        calls = len(self.speech.calls)
        self.job.start()
        self.finish()
        self.coach.fixed_audio.phrase('race', radio_phrases.build('gap_ahead_closing', 2, gap=.3)['text'])
        self.coach.fixed_audio.clip('fuel_short#2')
        restarted = RadioAudioCache(FakeSpeech(), self.coach.fixed_audio.directory)
        restarted.phrase('spotter', radio_phrases.build('lap_time_read', 1, lap=90.4)['text'])
        self.assertEqual(len(self.speech.calls), calls)
        self.assertEqual(restarted.speech.calls, [])

    def test_service_failure_stops_early_and_a_retry_fetches_only_what_is_missing(self):
        good = self.speech.synthesize
        self.job.WORKERS = 1
        seen = []

        def flaky(text, role='coach', encoding='mp3'):
            seen.append(text)
            if len(seen) > 5:
                raise ValueError('한국어 음성 합성 연결 필요 · 텍스트 답변은 저장되었습니다.')
            return good(text, role=role, encoding=encoding)
        self.speech.synthesize = flaky
        self.job.start()
        self.finish()
        self.job._ready = (0., None)
        failed = self.job.status()
        self.assertEqual((failed['state'], failed['ready'], failed['failed']), ('FAILED', 5, 3))
        self.assertIn('음성 합성 연결', failed['message'])
        self.assertEqual(len(seen), 8)  # stopped after three failures in a row
        self.speech.synthesize = good
        self.job.start()
        self.finish()
        self.job._ready = (0., None)
        self.assertEqual(self.job.status()['state'], 'COMPLETE')
        self.assertEqual(len(self.speech.calls), failed['total'])

    def test_quota_errors_are_waited_out_instead_of_failing_the_batch(self):
        good = self.speech.synthesize
        self.job.WORKERS = 1
        state = {'raised': 0}

        class ResourceExhausted(Exception):
            pass

        def limited(text, role='coach', encoding='mp3'):
            if state['raised'] < 2:
                state['raised'] += 1
                raise ResourceExhausted('quota')
            return good(text, role=role, encoding=encoding)
        self.speech.synthesize = limited
        with patch.object(self.job.stop_event, 'wait', return_value=False) as wait:
            self.job.start()
            self.finish()
        self.assertEqual(wait.call_count, 2)
        self.job._ready = (0., None)
        self.assertEqual((self.job.status()['state'], self.job.status()['failed']), ('COMPLETE', 0))

    def test_stop_keeps_what_was_fetched_and_the_api_reports_progress(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from voice_coach import register_voice_routes
        app = FastAPI()
        register_voice_routes(app, self.coach, lambda name: None, None, None, None)
        good = self.speech.synthesize
        self.job.WORKERS = 1

        def stop_after_ten(text, role='coach', encoding='mp3'):
            if len(self.speech.calls) == 9:
                self.job.stop()
            return good(text, role=role, encoding=encoding)
        self.speech.synthesize = stop_after_ten
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/live-coach/radio-audio').json()['state'], 'IDLE')
            client.post('/api/live-coach/radio-audio/prepare')
            self.finish()
            self.job._ready = (0., None)
            status = client.post('/api/live-coach/radio-audio/stop').json()
        self.assertEqual((status['state'], status['ready']), ('STOPPED', 10))


if __name__ == '__main__':
    unittest.main()
