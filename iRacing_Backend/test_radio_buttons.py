import asyncio
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from radio_button_input import WindowsButtons
from radio_buttons import ButtonRadio, EXAMPLE, clip_id, register, wordings


def row(name, scope='both'):
    return dict(id=name, name=name, scope=scope, enabled=True, items=['last', 'ahead'],
                keys=[dict(code='key:120:1', label='Ctrl+F9')])


class ButtonSettingsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.coach = SimpleNamespace(store=SimpleNamespace(path=Path(folder.name) / 'store.db'))
        self.service = ButtonRadio(self.coach)

    def test_retired_questions_are_removed_from_legacy_rows_without_losing_keys(self):
        old = [{**row('mixed'), 'items': ['tc', 'ahead', 'fuel_laps', 'bias', 'behind']},
               {**row('empty'), 'items': ['energy', 'time', 'abs', 'consumption']}]
        self.service.path.write_text(json.dumps({'rows': old, 'revision': 9}), encoding='utf-8')
        loaded = ButtonRadio(self.coach)
        expected = [{**old[0], 'items': ['ahead', 'behind']}, {**old[1], 'items': []}]
        self.assertEqual(loaded.rows, expected)
        self.assertEqual(loaded.maps['lmu']['key:120:1']['id'], 'mixed')
        self.assertEqual(loaded.save({'revision': 9, 'rows': old})['rows'], expected)

    def test_one_example_does_not_qualify_a_question(self):
        from radio_audio import RadioAudioCache
        self.coach.fixed_audio = RadioAudioCache(None, self.service.path.parent)
        examples = {line for key in ('last', 'fuel', 'ahead_lap', 'corner_gain')
                    for line in wordings(key, EXAMPLE)}
        with patch.object(self.service, 'prepared', side_effect=lambda role, text: (role, text) in examples):
            self.assertFalse(self.service.topic_catalog())

    def test_labeled_lap_never_plays_half_an_answer(self):
        def read(item, role, text):
            if '초' in text:
                raise FileNotFoundError
            return text
        with patch.object(self.service, 'read_clip', side_effect=read):
            answer = self.service.render(['ahead_lap', 'fuel'], EXAMPLE, driving=True)
        self.assertEqual(answer['spoken'], ['지금 연료는 24리터입니다.'])
        self.assertTrue(answer['missing'])

    def test_legacy_time_and_bias_recordings_are_only_available_for_listening(self):
        self.coach.fixed_audio = SimpleNamespace(read_prepared=lambda role, text: text)
        self.coach.custom_packs = SimpleNamespace(lock=threading.RLock(), selections={}, loaded={})
        clips = [clip_id('spotter', '5분 남았습니다.'), clip_id('coach', '바이어스 52.5퍼센트.')]
        saved = {**row('old'), 'items': clips}
        self.assertEqual(self.service.settings_rows([saved])[0], [{**saved, 'items': []}])
        self.assertFalse(self.service.render(clips, EXAMPLE, driving=True)['parts'])
        self.assertEqual(len(self.service.render(clips, EXAMPLE)['parts']), 2)

    def test_race_questions_require_the_corresponding_measurement(self):
        from copy import deepcopy
        ctx = deepcopy(EXAMPLE)
        for key in ('corner_gain', 'theoretical', 'sector_delta', 'ahead_lap', 'leader_lap',
                    'closing_behind', 'stops_ahead', 'stops_behind', 'pit_transit'):
            self.assertTrue(wordings(key, ctx), key)
            self.assertFalse(wordings(key, {}), key)
        wall = ctx['pit_wall']
        wall['behind'].update(gap_trend='opening', gap_change_s_per_lap=.4, stops=11)
        wall['ahead']['stops'] = None
        ctx['snapshot']['lap_number'] += 1
        for key in ('corner_gain', 'sector_delta', 'closing_behind', 'stops_ahead', 'stops_behind'):
            self.assertFalse(wordings(key, ctx), key)
        wall['active'] = False
        for key in ('ahead_lap', 'leader_lap'):
            self.assertFalse(wordings(key, ctx), key)

    def test_middle_missing_recording_excludes_new_registration_but_preserves_existing_row(self):
        self.coach.fixed_audio = SimpleNamespace(cached=lambda role, text: text != '앞차는 피트를 세 번 했습니다.')
        app, router = FastAPI(), APIRouter()
        service = register(router, self.coach)
        app.include_router(router)
        saved = {**row('original'), 'items': ['stops_ahead']}
        service.save({'revision': 0, 'rows': [saved]})
        with TestClient(app) as client:
            renamed = {**saved, 'name': '이름 변경'}
            self.assertEqual(client.post('/buttons', json={'revision': 1, 'rows': [renamed]}).status_code, 200)
            added = {**saved, 'id': 'added', 'keys': []}
            self.assertEqual(client.post('/buttons', json={'revision': 2, 'rows': [renamed, added]}).status_code, 422)
            self.assertEqual(client.get('/buttons').json()['rows'], [renamed])

    def test_many_buttons_and_repeated_items_round_trip_without_empty_rows_claiming_keys(self):
        rows = [{**row(f'button{i}'), 'keys': [], 'items': []} for i in range(12)]
        rows[3]['keys'] = row('wheel')['keys']
        rows[7].update(keys=row('wheel')['keys'], items=['fuel', 'last', 'fuel', 'ahead', 'fuel', 'behind'])
        self.service.save({'revision': 0, 'rows': rows})
        loaded = ButtonRadio(self.coach)
        self.assertEqual(loaded.rows, rows)
        self.assertEqual(loaded.maps['iracing']['key:120:1']['id'], 'button7')
        remaining = [r for r in rows if r['id'] != 'button7']
        loaded.save({'revision': 1, 'rows': remaining})
        self.assertEqual(ButtonRadio(self.coach).rows, remaining)
        self.assertFalse(any(loaded.maps.values()))

    def test_save_reload_conflict_and_per_game_override(self):
        s = self.service
        s.save({'revision': 0, 'rows': [row('race'), row('lmu', 'lmu')]})
        loaded = ButtonRadio(self.coach)
        self.assertEqual(loaded.maps['iracing']['key:120:1']['id'], 'race')
        self.assertEqual(loaded.maps['lmu']['key:120:1']['id'], 'lmu')
        with self.assertRaises(HTTPException):
            s.save({'revision': 0, 'rows': []})
        with self.assertRaises(ValueError):
            s.save({'revision': 1, 'rows': [row('a'), row('b')]})
        self.assertEqual(ButtonRadio(self.coach).rows, loaded.rows)

    def test_neutral_fuel_wording_and_missing_evidence(self):
        self.assertEqual(wordings('fuel', EXAMPLE), [('strategy', '지금 연료는 24리터입니다.')])
        self.assertEqual(wordings('laps', EXAMPLE), [('strategy', '4랩 남았습니다.')])
        for key in ('ahead', 'behind', 'last', 'energy', 'fuel', 'bias'):
            self.assertEqual(wordings(key, {}), [])
        self.assertEqual(wordings('laps', {'snapshot': {'session_time_remaining_s': 600}}), [])

    def test_measured_fuel_buttons_use_the_reported_tenth_including_zero(self):
        ctx = {'game': 'lmu', 'pit_wall': {'fuel_laps': .5, 'energy_pct': 0},
               'fuel_avg_l_per_lap': 3.15, 'fuel_l_per_lap': 4.1}
        self.assertEqual(wordings('fuel_range', ctx, 'short'), [('strategy','연료 약 0.5랩.')])
        self.assertEqual(wordings('fuel_average', ctx), [('strategy','최근 평균은 랩당 3.2리터 정도입니다.')])
        self.assertEqual(wordings('energy_remaining', ctx), [('strategy','가상 에너지 0퍼센트 남았습니다.')])
        self.assertFalse(wordings('energy_remaining', {**ctx, 'game': 'iracing'}))
        for bad in [None, float('nan'), True, -.1, 200.01]:
            self.assertFalse(wordings('fuel_range', {'pit_wall': {'fuel_laps': bad}}))
        self.assertFalse(wordings('fuel_average', {'fuel_l_per_lap': 3.1}))

    def test_lmu_energy_only_listens_for_its_shortcut_in_lmu(self):
        energy = {**row('energy-only'), 'items':['energy_remaining']}
        self.service.save({'revision':0, 'rows':[energy]})
        self.assertFalse(self.service.shortcuts['iracing'])
        self.assertEqual(self.service.maps['lmu']['key:120:1']['items'], ['energy_remaining'])
        mixed = {**energy, 'items':['fuel_average','energy_remaining','fuel_range']}
        self.service.save({'revision':1, 'rows':[mixed]})
        self.assertEqual(self.service.maps['iracing']['key:120:1']['items'], ['fuel_average','fuel_range'])
        with self.assertRaises(ValueError):
            self.service.save({'revision':2, 'rows':[{**mixed,'scope':'iracing'}]})

    def test_missing_whole_number_blocks_only_its_own_topic_and_style(self):
        from button_fuel_audio import directory
        root = self.service.path.parent
        packs = SimpleNamespace(lock=threading.RLock(), selections={}, loaded={},
                                is_builtin=lambda key: key=='short', has=lambda *_: False)
        self.coach.custom_packs = packs
        self.coach.fixed_audio = SimpleNamespace(directory=root)
        for style in ('default','short'):
            folder = directory(root,'fuel_average',style)
            folder.mkdir(parents=True)
            for n in range(301):
                (folder/f'{n}.ogg').write_bytes(b'OggS' + bytes(100) + style.encode())
        (directory(root,'fuel_average','short')/'31.ogg').unlink()
        self.assertTrue(self.service.topic_catalog(['fuel_average']))
        self.assertTrue(self.service.render(['fuel_average'],EXAMPLE)['parts'])
        packs.selections['strategy'] = 'short'
        self.assertFalse(self.service.topic_catalog(['fuel_average']))
        self.assertFalse(self.service.render(['fuel_average'],EXAMPLE)['parts'])
        heard = self.service.render(['fuel_average'],{**EXAMPLE,'fuel_avg_l_per_lap':17.9})
        self.assertEqual(heard['spoken'],['랩당 평균 17.9리터.'])
        self.assertTrue(heard['parts'][0].startswith('data:audio/ogg'))

    def test_corner_question_uses_last_completed_lap_and_never_a_saved_number(self):
        def context(current, lap, turn, loss):
            return {'snapshot': {'lap_number': current}, 'corner_coach': {'last_lap': {
                'lap': lap, 'losses': [{'call': turn, 'delta_s': loss}]}}}
        for turn, loss in [('13번 코너', .9), ('4번 코너', .2)]:
            parts = wordings('corner_loss', context(7, 6, turn, loss))
            self.assertIn(turn, parts[0][1])
            self.assertIn(f'{loss}초', parts[0][1])
        for ctx in ({}, context(8, 6, '13번 코너', .9), context(7, 6, '', .9), context(7, 6, '4번 코너', -.2)):
            self.assertFalse(wordings('corner_loss', ctx))

    def test_old_recorded_corner_is_a_live_question_in_existing_shortcut_and_editor(self):
        key, source = 'qa@1', '지난 랩은 13번 코너에서 0.9초 잃었습니다.'
        clip = clip_id('coach', source, key)
        packs = SimpleNamespace(lock=threading.RLock(), selections={'coach': key}, _folder=lambda _: self.service.path.parent,
                                loaded={key: ({}, {('coach', source): {'eventKey': 'brief_corner_loss', 'text': source}})})
        self.coach.custom_packs = packs
        self.coach.fixed_audio = SimpleNamespace(read_prepared=lambda role, text: text)
        sample = {'run_id': 'qa', 'lap_number': 8}
        review = {'lap': 7, 'losses': [{'call': '4번 코너', 'delta_s': .2}]}
        live = SimpleNamespace(game='lmu', lock=threading.RLock(), _current_snapshot=lambda: (0, sample),
                               corners=SimpleNamespace(review=review))
        live.context = lambda **_: dict(game='lmu', connected=True, snapshot=sample)
        self.coach.live = live
        app, router = FastAPI(), APIRouter()
        service = register(router, self.coach)
        app.include_router(router)
        old = {**row('saved'), 'items': ['ahead', clip, 'behind']}
        service.save({'revision': 0, 'rows': [old]})
        with TestClient(app) as client:
            config = client.get('/buttons').json()
            self.assertEqual(config['rows'], [{**old, 'items': ['ahead', 'corner_loss', 'behind']}])
            service.emit(old, 'lmu')
            sample['lap_number'] = 9
            live.corners.review = None
            output = client.post('/buttons/play', json={'ticket': service.pending['ticket']}).json()
            self.assertEqual(output['spoken'], ['지난 랩은 4번 코너에서 0.2초 잃었습니다.'])
            response = client.post('/buttons', json=config)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(ButtonRadio(self.coach).rows, config['rows'])

    def test_native_edges_do_not_repeat_while_held_or_without_game_focus(self):
        s = self.service
        r = row('test')
        key = 'hid:' + 'b' * 24 + ':b6'
        r['keys'].append(dict(code=key, label='wheel 6'))
        s.save({'revision': 0, 'rows': [r]})
        q = asyncio.Queue()
        s.clients[q] = False
        frames = iter([(None, False, True), ('iracing', False, True), ('iracing', False, False),
                       ('iracing', True, False), ('iracing', True, True), ('iracing', True, True),
                       ('iracing', False, False), ('iracing', False, True)])
        class Native:
            def foreground_game(self):
                frame = next(frames, None)
                if not frame:
                    s.clients.clear()
                self.game, self.hid, self.key = frame or (None, False, False)
                return self.game

            def changes(self):
                return [('buttons', {key} if self.hid else set())]

            def shortcuts_down(self, bindings):
                return {item[0] for item in bindings} if self.key else set()

            def close(self):
                pass
        with patch('radio_button_input.WindowsButtons', Native), patch.object(s, 'emit') as emit:
            asyncio.run(s.pump())
        self.assertEqual(emit.call_count, 2)

    def test_unreadable_config_preserved_and_does_not_prevent_coach_start(self):
        self.service.path.write_text('{')
        service = ButtonRadio(self.coach)
        self.assertFalse(service.rows)
        with self.assertRaises(HTTPException):
            service.save({'revision': 0, 'rows': [row('a')]})
        self.assertEqual(service.path.read_text(), '{')

    def test_chord_does_not_turn_into_a_second_shortcut_when_ctrl_is_released(self):
        native = WindowsButtons.__new__(WindowsButtons)
        native.held_shortcuts = {}
        down = {0x11, 120}
        native.keys = lambda vk: 0x8000 if vk in down else 0
        bindings = [('key:120:0', 120, 0), ('key:120:1', 120, 1)]
        self.assertEqual(native.shortcuts_down(bindings), {'key:120:1'})
        down.remove(0x11)
        self.assertEqual(native.shortcuts_down(bindings), {'key:120:1'})
        down.clear()
        self.assertFalse(native.shortcuts_down(bindings))
        down.add(120)
        self.assertEqual(native.shortcuts_down(bindings), {'key:120:0'})

    def test_mic_only_yields_to_matching_f8_chord_or_input_capture(self):
        r = row('radio', 'lmu')
        r['keys'] = [dict(code='key:119:1', label='Ctrl+F8')]
        self.service.save({'revision': 0, 'rows': [r]})
        ctrl = lambda vk: 0x8000 if vk == 0x11 else 0
        self.assertFalse(self.service.owns_f8('iracing', ctrl))
        self.assertFalse(self.service.owns_f8('lmu', lambda vk: 0))
        self.assertTrue(self.service.owns_f8('lmu', ctrl))
        self.service.clients[asyncio.Queue()] = True
        self.assertTrue(self.service.owns_f8('iracing', lambda vk: 0))

    def test_fixed_pack_binding_never_substitutes_another_recording_with_the_same_words(self):
        key, text = 'driver-pack@1', '연료 확인.'
        packs = SimpleNamespace(lock=threading.RLock(), selections={'coach': key},
                                loaded={key: ({}, {('coach', text): {}})}, _folder=lambda _: self.service.path.parent,
                                audio=lambda *args, exact=False: 'selected' if exact else 'rotating live call')
        self.coach.custom_packs = packs
        self.coach.fixed_audio = SimpleNamespace(read_prepared=lambda *args: 'default')
        items = [clip_id('coach', text, key)]
        self.assertEqual(self.service.render(items, {})['parts'], ['selected'])
        packs.selections.clear()
        self.assertEqual(self.service.render(items, {})['parts'], [])

    def test_library_groups_variants_before_paging_and_searches_source_and_pack_wording(self):
        from radio_button_library import VoiceLibrary
        with tempfile.TemporaryDirectory() as folder:
            from radio_audio import RadioAudioCache
            cache = RadioAudioCache(None, Path(folder) / 'radio')
            cache.directory = Path(folder) / 'radio'
            records = {}
            for corner in range(1, 31):
                for tenth in range(1, 6):
                    source = f'{corner}번 코너에서 0.{tenth}초 잃었습니다.'
                    records['coach', source] = dict(eventKey='brief_corner_loss', text=f'{corner}번 코너 손실 0.{tenth}초.', audio=f'{corner}-{tenth}.ogg')
            alias = '지난 랩 열세 번째 구간.'
            records['coach', alias] = records['coach', '13번 코너에서 0.1초 잃었습니다.']
            records['coach', '미분류 원문'] = dict(text='내 안내.', audio='custom.wav')
            for entry in records.values():
                (Path(folder) / entry['audio']).write_bytes(b'0' * 100)
            packs = SimpleNamespace(lock=threading.RLock(), selections={'coach': 'qa@1'}, loaded={'qa@1': ({}, records)},
                                    _folder=lambda _: Path(folder), is_builtin=lambda _: False)
            library = VoiceLibrary(cache, packs, None)
            root = library.browse()
            self.assertFalse(root['clips'])
            self.assertEqual(root['groups']['코너 코칭'], 150)
            group = library.browse(group='코너 코칭')
            self.assertEqual(group['kinds']['시간 손실'], 150)
            self.assertFalse(group['clips'])
            first = library.browse(group='코너 코칭', kind='시간 손실')
            second = library.browse(group='코너 코칭', kind='시간 손실', offset=40)
            self.assertTrue(first['more'])
            self.assertFalse({v['id'] for v in first['clips']} & {v['id'] for v in second['clips']})
            narrowed = library.browse(group='코너 코칭', kind='시간 손실', corner=13, q='잃었습니다')
            self.assertEqual(narrowed['total'], 5)
            self.assertEqual(library.browse(q='열세 번째')['clips'][0]['id'], narrowed['clips'][0]['id'])
            self.assertEqual(library.browse(q='손실 0.1초')['total'], 30)
            self.assertEqual(library.browse(q='내 안내')['total'], 1)

    def test_listening_catalog_shows_one_entry_per_race_recording_with_its_spoken_wording(self):
        from race_call_audio import CALLS, RACE_AUDIO_DIR
        from radio_button_library import VoiceLibrary
        base = self.service.path.parent
        race_dir = base / RACE_AUDIO_DIR
        race_dir.mkdir()
        (race_dir / 'finish_save_2-default.ogg').write_bytes(b'0' * 200)
        packs = SimpleNamespace(lock=threading.RLock(), selections={}, loaded={}, is_builtin=lambda _: False)
        library = VoiceLibrary(SimpleNamespace(directory=base), packs, None)
        _, role, standard, short = CALLS['finish_save_2']
        self.assertEqual(library.browse()['total'], 1)
        result = library.browse(q=short)['clips']
        self.assertEqual([r['name'] for r in result], [standard])
        old_id = clip_id(role, short)
        self.assertEqual(library.resolve([old_id])[old_id].name, standard)

    def test_isolated_gap_status_does_not_disable_other_voice_routes(self):
        service = ButtonRadio(SimpleNamespace())
        self.assertFalse(any(service.maps.values()))
        with self.assertRaises(HTTPException) as caught:
            service.save({'revision': 0, 'rows': []})
        self.assertEqual(caught.exception.status_code, 503)

    def test_only_latest_press_can_play_once_with_press_time_lap_and_current_gap(self):
        sample = {**EXAMPLE['snapshot'], 'run_id': 'test-run'}
        view = {**EXAMPLE['pit_wall']}
        live = SimpleNamespace(lock=threading.RLock(), game='iracing', _current_snapshot=lambda: (0, sample))
        live.context = lambda **kwargs: dict(connected=True, game=live.game, snapshot=sample, pit_wall=view)
        self.coach.live = live
        self.coach.fixed_audio = SimpleNamespace(read_prepared=lambda role, text: text)
        app, router = FastAPI(), APIRouter()
        radio = register(router, self.coach)
        app.include_router(router)
        radio.clients[queue := asyncio.Queue(maxsize=1)] = False
        def press():
            radio.emit(row('gap'), live.game)
            return queue.get_nowait()['ticket']
        with TestClient(app) as client:
            def play(ticket):
                return client.post('/buttons/play', json={'ticket': ticket})
            for game in ('iracing', 'lmu'):
                live.game = game
                sample.update(run_id=game, lap_last_s=102.3)
                old, latest = press(), press()
                self.assertEqual(play(old).status_code, 410)
                sample['lap_last_s'] = 120
                view['ahead'] = {'gap_estimate_s': 3.8}
                answer = play(latest)
                self.assertEqual(answer.status_code, 200)
                spoken = answer.json()['spoken']
                self.assertEqual(len(spoken), 2)
                self.assertIn('1분 42초 3', spoken[0])
                self.assertIn('3.8초', spoken[1])
                self.assertEqual(play(latest).status_code, 410)
                changed = press()
                sample['run_id'] = 'next'
                self.assertEqual(play(changed).status_code, 410)

    def test_answer_context_leaves_the_event_loop_available_for_spotter_frames(self):
        sample = {**EXAMPLE['snapshot'], 'run_id': 'test-run'}
        entered, heartbeat = threading.Event(), threading.Event()
        context_yielded = []
        live = SimpleNamespace(lock=threading.RLock(), game='iracing', _current_snapshot=lambda: (0, sample))
        def context(**kwargs):
            entered.set()
            context_yielded.append(heartbeat.wait(2))
            return dict(connected=True, game=live.game, snapshot=sample)
        live.context = context
        self.coach.live = live
        self.coach.fixed_audio = SimpleNamespace(read_prepared=lambda role, text: text)
        app, router = FastAPI(), APIRouter()
        radio = register(router, self.coach)
        app.include_router(router)
        @app.get('/heartbeat')
        async def publish_frame():
            heartbeat.set()
            return {'active': True}
        radio.clients[queue := asyncio.Queue(maxsize=1)] = False
        radio.emit({**row('fuel'), 'items': ['fuel']}, 'iracing')
        ticket = queue.get_nowait()['ticket']
        answers = []
        with TestClient(app) as client:
            request = threading.Thread(target=lambda: answers.append(client.post('/buttons/play', json={'ticket': ticket})))
            request.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(client.get('/heartbeat').status_code, 200)
            finally:
                heartbeat.set()
                request.join(4)
        self.assertEqual(context_yielded, [True], 'The button context blocked the spotter event loop')
        self.assertEqual(answers[0].status_code, 200)
