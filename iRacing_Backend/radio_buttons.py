"""On-demand radio. Every spoken item comes from the prepared voice library."""
import asyncio
from functools import cache, cached_property
from itertools import product
import json
import os
from pathlib import Path
import re
import secrets
import time

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from application_runtime import DEFAULT_CORS_ORIGINS

import button_fuel_audio as fuel_audio
from instant_answers import gap_sentences, instant_answer, number
from radio_button_library import VoiceLibrary, audio_files, clip_id
from radio_phrases import PHRASES, PLACEHOLDER, PREPARED, RANGES, build


TOPICS = {
    'last': ('랩·세션', '방금 랩타임'), 'best': ('랩·세션', '개인 최고 랩'),
    'position': ('랩·세션', '현재 클래스 순위'), 'laps': ('랩·세션', '남은 랩'),
    'theoretical': ('랩·세션', '구간 최고를 합친 이론상 베스트 랩'),
    'sector_delta': ('랩·세션', '최근 구간선에서 베스트 랩과의 차이'),
    'ahead': ('경쟁차', '앞차 간격'), 'behind': ('경쟁차', '뒤차 간격'),
    'catch_ahead': ('경쟁차', '앞차를 따라잡을 수 있어?'),
    'closing_behind': ('경쟁차', '뒤차가 랩당 몇 초씩 좁히고 있어?'),
    'ahead_lap': ('경쟁차', '앞차의 최근 랩타임'), 'leader_lap': ('경쟁차', '클래스 선두의 최근 랩타임'),
    'corner_loss': ('코너 코칭', '지난 랩 어디서 가장 많이 잃었어?'),
    'corner_gain': ('코너 코칭', '지난 랩 어디서 가장 많이 줄였어?'),
    'pit_rejoin': ('피트', '지금 피트 시 예상 복귀 순위'), 'stint_ahead': ('피트', '앞차 스틴트 길이'),
    'stint_behind': ('피트', '뒤차 스틴트 길이'),
    'stops_ahead': ('피트', '앞차 피트 진입 횟수'), 'stops_behind': ('피트', '뒤차 피트 진입 횟수'),
    'pit_transit': ('피트', '피트를 통과하면 몇 초 손해 봐?'),
    'fuel': ('연료', '현재 연료'),
    **{key: ('연료', label) for key, label in fuel_audio.NAMES.items()},
}
RETIRED = {'tc', 'abs', 'bias', 'time', 'fuel_laps', 'consumption', 'energy'}
# A button needs the normal recorded range of its answer, including both directions
# of a comparison. Field-dependent follow-up sentences can also be used in a race.
ANSWER_PHRASES = {
    'last': ('lap_time_read',), 'best': ('personal_best_read',), 'position': ('position_now',),
    'laps': ('laps_to_go',), 'theoretical': ('label_theoretical', 'lap_time_read'),
    'sector_delta': ('delta_ahead', 'delta_behind'), 'ahead': ('brief_gap_ahead', 'ahead_far'),
    'behind': ('brief_gap_behind',), 'catch_ahead': ('brief_gap_ahead', 'ahead_far', 'catch_rate', 'catch_need', 'catch_in'),
    'closing_behind': ('lose_rate',), 'ahead_lap': ('label_ahead_lap', 'lap_time_read'),
    'leader_lap': ('label_leader_lap', 'lap_time_read'), 'corner_loss': ('brief_corner_loss',),
    'corner_gain': ('brief_best_corner', 'brief_gain'), 'pit_rejoin': ('pit_rejoin',),
    'stint_ahead': ('stint_ahead',), 'stint_behind': ('stint_behind',),
    'stops_ahead': ('stops_none_ahead', 'stops_ahead'), 'stops_behind': ('stops_none_behind', 'stops_behind'),
    'pit_transit': ('form_pit_loss',), 'fuel': ('form_fuel',),
}
EXAMPLE = {'snapshot': {'lap_number': 7, 'lap_last_s': 102.3, 'lap_best_s': 101.6, 'position_confirmed': 9, 'session_laps_remaining': 4,
                        'fuel_l': 24}, 'fuel_avg_l_per_lap': 3.1,
           'pit_wall': {'active': True, 'fuel_laps': 7.7, 'energy_pct': 62, 'finish_laps_to_go': 4, 'class_position': 9,
                        'pit_transit_loss_s': 22, 'rejoin_class_position_estimate': 14,
                        'class_cars': [{'class_position': 1, 'last_lap_s': 99.4}],
                        'ahead': {'gap_estimate_s': 1.8, 'stint_laps': 7, 'stops': 0, 'last_lap_s': 101.2},
                        'behind': {'gap_estimate_s': 2.4, 'stint_laps': 5, 'stops': 1, 'gap_trend': 'closing',
                                   'gap_change_s_per_lap': -.4}, 'remaining_laps': 4}, 'game': 'lmu',
           'theoretical': {'lap_s': 100.2}, 'sector_delta': {'lap': 7, 'delta_s': -.4},
           'corner_coach': {'last_lap': {'lap': 6, 'losses': [{'call': '13번 코너', 'delta_s': .9}],
                                         'best_gain': {'call': '7번 코너', 'delta_s': -.3}}}}
KEY = re.compile(r'key:(\d{1,3}):(\d{1,2})\Z')
HID = re.compile(r'hid:[a-f0-9]{24}:(?:b\d{1,5}|h\d{1,2})\Z')
FIXED = re.compile(r'clip:[a-f0-9]{64}\Z')
KINDS = ('both', 'iracing', 'lmu')


def wordings(item, context, style='default'):
    s, v = context.get('snapshot') or {}, context.get('pit_wall') or {}
    if item in fuel_audio.LIMITS:
        value = fuel_audio.measurement(item, context)
        return [('strategy', fuel_audio.wording(item, value, style))] if value is not None else []
    queries = {'last': '랩타임', 'best': '베스트 랩타임', 'position': '순위',
               'pit_rejoin': '피트하면', 'stint_ahead': '앞차 스틴트', 'stint_behind': '뒤차 스틴트',
               'stops_ahead': '앞차 피트 몇 번', 'stops_behind': '뒤차 피트 몇 번',
               'catch_ahead': '앞차를 따라잡을 수 있어?'}
    if item in queries:
        result = instant_answer(queries[item], context)
        return [(result[0], text) for text in result[1]] if result else []
    if item in ('corner_loss', 'corner_gain'):
        review = (context.get('corner_coach') or {}).get('last_lap') or {}
        lap = number(review.get('lap'))
        if lap is None or context.get('review_lap_number', s.get('lap_number')) != lap + 1:
            return []
        if item == 'corner_gain':
            gain = review.get('best_gain') or {}
            delta = number(gain.get('delta_s'))
            if gain.get('call') and delta is not None and -10 < delta <= -.1:
                return [('coach', build('brief_best_corner', turn=gain['call'])['text']),
                        ('coach', build('brief_gain', loss=-delta)['text'])]
            return []
        # The review ranks measured losses against the driver's own clean passes.
        losses = review.get('losses') or []
        worst = losses[0] if losses else {}
        loss = number(worst.get('delta_s'))
        if worst.get('call') and loss is not None and .1 <= loss < 10:
            return [('coach', build('brief_corner_loss', turn=worst['call'], loss=loss)['text'])]
        return []
    if item == 'laps':
        value = number(v.get('finish_laps_to_go')) or number(s.get('session_laps_remaining'))
        if value is not None and value == int(value) and 1 <= value <= 200:
            return [('strategy', build('laps_to_go', laps=value)['text'])]
    if item in ('ahead', 'behind') and v.get('active'):
        car = v.get(item) or {}
        if (number(car.get('gap_estimate_s')) or 0) > 0:
            return [('race', text) for text in gap_sentences(v, [item])]
    if item == 'fuel' and number(s.get('fuel_l')) is not None and s['fuel_l'] >= 0:
        return [('strategy', build('form_fuel', 2, liters=s['fuel_l'])['text'])]
    if item in ('theoretical', 'ahead_lap', 'leader_lap'):
        if item == 'theoretical':
            value = (context.get(item) or {}).get('lap_s')
        elif not v.get('active'):
            return []
        elif item == 'ahead_lap':
            value = (v.get('ahead') or {}).get('last_lap_s')
        else:
            leader = next((c for c in v.get('class_cars') or [] if c.get('class_position') == 1), {})
            value = leader.get('last_lap_s')
        if number(value) is not None and 20 < value < 1800:
            return [('spotter', build('label_' + item)['text']), ('spotter', build('lap_time_read', lap=value)['text'])]
    if item == 'sector_delta':
        split = context.get(item) or {}
        delta = number(split.get('delta_s'))
        if split.get('lap') is not None and split['lap'] == s.get('lap_number') and delta is not None and .1 <= abs(delta) < 10:
            return [('coach', build('delta_ahead' if delta < 0 else 'delta_behind', loss=abs(delta))['text'])]
    if item == 'closing_behind' and v.get('active'):
        car = v.get('behind') or {}
        delta = number(car.get('gap_change_s_per_lap'))
        if car.get('gap_trend') == 'closing' and not car.get('on_pit_road') and delta is not None and delta < 0:
            return [('race', build('lose_rate', loss=-delta)['text'])]
    if item == 'pit_transit':
        loss = number(v.get('pit_transit_loss_s'))
        if loss is not None and loss > 0:
            return [('strategy', build('form_pit_loss', sec=loss)['text'])]
    return []


def keyboard(key):
    match = KEY.fullmatch(key)
    if match:
        vk, mods = map(int, match.groups())
        if 8 <= vk <= 254 and vk not in (16, 17, 18, 91, 92) and 0 <= mods <= 15:
            return vk, mods
    return None


def answer_recordings(phrase):
    role, variants = PHRASES[phrase]
    seed = 2 if phrase == 'form_fuel' else 0
    names = PLACEHOLDER.findall(variants[seed])
    ranges = PREPARED.get(phrase, RANGES)
    for values in product(*(ranges[name] for name in names)):
        yield role, build(phrase, seed, **dict(zip(names, values)))['text']


@cache
def lap_piece_keys(phrase):
    from number_splice import plan
    return tuple(dict.fromkeys(piece.key for _, text in answer_recordings(phrase) for piece in plan(text)[0]))


def validate(rows):
    if not isinstance(rows, list):
        raise ValueError('버튼 목록을 확인하세요.')
    ids, shortcuts = set(), set()
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('버튼 설정을 확인하세요.')
        identifier, name, scope = row.get('id'), row.get('name'), row.get('scope')
        if (not isinstance(identifier, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', identifier)
                or identifier in ids or not isinstance(name, str) or not 1 <= len(name.strip()) <= 80
                or type(row.get('enabled')) is not bool or scope not in KINDS):
            raise ValueError('버튼 이름과 적용 게임을 확인하세요.')
        ids.add(identifier)
        topics, keys = row.get('items'), row.get('keys')
        if (not isinstance(topics, list) or not all(isinstance(i, str) and
                (i in TOPICS or i in RETIRED or FIXED.fullmatch(i)) for i in topics) or not isinstance(keys, list)):
            raise ValueError('읽을 항목과 연결할 키를 선택하세요.')
        topics = [i for i in topics if i not in RETIRED]
        if scope == 'iracing' and 'energy_remaining' in topics:
            raise ValueError('가상 에너지는 LMU 또는 두 게임 공통 버튼에서 선택하세요.')
        normalized.append({**row, 'items': topics})
        for binding in keys:
            key = binding.get('code') if isinstance(binding, dict) else None
            if (not isinstance(key, str) or not (keyboard(key) or HID.fullmatch(key)) or
                    not isinstance(binding.get('label'), str) or not 1 <= len(binding['label']) <= 100):
                raise ValueError('지원하는 키나 휠 버튼을 눌러 주세요.')
            if row['enabled'] and topics:
                if (scope, key) in shortcuts:
                    raise ValueError('같은 게임에서 이미 사용 중인 키입니다.')
                shortcuts.add((scope, key))
    return normalized


class ButtonRadio:
    def __init__(self, coach, spotter_audio=None):
        self.coach = coach
        self.spotter_audio = spotter_audio
        store_path = getattr(getattr(coach, 'store', None), 'path', None)
        self.path = Path(store_path).with_name('radio-buttons.json') if isinstance(store_path, (str, Path)) else None
        self.rows, self.revision = [], 0
        self.load_error = ''
        if self.path is not None and self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding='utf-8'))
                self.rows, _ = self.settings_rows(validate(saved['rows']))
                self.revision = int(saved['revision'])
            except (ValueError, KeyError, TypeError, OSError):
                self.load_error = '저장한 무전 버튼 설정 파일을 읽을 수 없습니다.'
        self.clients, self.task = {}, None
        self.pending = None
        self.reindex()

    def reindex(self):
        self.maps = {}
        for game in KINDS[1:]:
            mapping = {}
            for scope in ('both', game):
                for row in self.rows:
                    if row['enabled'] and row['scope'] == scope:
                        items = [item for item in row['items'] if item != 'energy_remaining' or game == 'lmu']
                        if items:
                            mapping.update({k['code']: {**row, 'items': items} for k in row['keys']})
            self.maps[game] = mapping
        self.shortcuts = {g: [(key, *keyboard(key)) for key in m if keyboard(key)] for g, m in self.maps.items()}

    def owns_f8(self, game, key_state):
        if any(self.clients.copy().values()):
            return True
        f8_masks = {mask for _, vk, mask in self.shortcuts.get(game, ()) if vk == 0x77}
        if not f8_masks:
            return False
        from radio_button_input import modifier_mask
        return modifier_mask(key_state) in f8_masks

    @cached_property
    def library(self):
        return VoiceLibrary(self.coach.fixed_audio, self.coach.custom_packs, self.spotter_audio)

    def read_clip(self, item, role, text):
        if item.startswith('clip:') and item != clip_id(role, text):
            packs = self.coach.custom_packs
            with packs.lock:
                key = packs.selections.get(role)
                if not key or item != clip_id(role, text, key):
                    raise FileNotFoundError('선택한 음성 팩을 다시 선택하거나 교체하세요.')
                return packs.audio(role, text, exact=True)
        if item in fuel_audio.LIMITS or (item.startswith('clip:') and role == 'strategy'):
            take = fuel_audio.by_wording().get(text)
            if take is not None:
                packs = getattr(self.coach, 'custom_packs', None)
                if item in fuel_audio.LIMITS and packs and packs.has(role, text):
                    return packs.audio(role, text)
                return fuel_audio.read(self.coach.fixed_audio.directory, *take)
        try:
            return self.coach.fixed_audio.read_prepared(role, text)
        except (OSError, ValueError):
            if role != 'spotter' or self.spotter_audio is None:
                raise
            return self.spotter_audio.read_prepared(text)

    def settings_rows(self, rows):
        ids = {key for row in rows for key in row['items'] if key.startswith('clip:')}
        saved = self.library.resolve(ids) if ids else {}
        def question(key):
            return (saved[key].question or key) if key in saved else key
        rows = [{**row, 'items': [key for key in map(question, row['items']) if key not in RETIRED]} for row in rows]
        names = {key: TOPICS[key][1] for row in rows for key in row['items'] if key in TOPICS}
        names.update({key: '이전 고정 음성 · ' + recording.name for key, recording in saved.items() if not recording.question})
        return rows, names

    def prepared(self, role, text):
        if self.coach.fixed_audio.cached(role, text):
            return True
        if role == 'spotter' and self.spotter_audio is not None:
            try:
                return self.spotter_audio.path(text).stat().st_size >= 100
            except OSError:
                pass
        return False

    def topic_catalog(self, keys=None):
        @cache
        def phrase_ready(phrase):
            if phrase in ('lap_time_read', 'personal_best_read'):
                pieces = self.coach.fixed_audio.pieces()
                if all(pieces.stored(pieces.path('spotter', key)) for key in lap_piece_keys(phrase)):
                    return True
            return all(self.prepared(role, text) for role, text in answer_recordings(phrase))

        style = fuel_audio.style_for(getattr(self.coach, 'custom_packs', None))
        def ready(key):
            if key in fuel_audio.LIMITS:
                folder = fuel_audio.directory(self.coach.fixed_audio.directory, key, style)
                return fuel_audio.filenames(key).issubset(audio_files(folder))
            return all(phrase_ready(phrase) for phrase in ANSWER_PHRASES[key])

        return [{'id': key, 'name': TOPICS[key][1], 'group': TOPICS[key][0], 'ready': True,
                 **({'game': 'lmu'} if key == 'energy_remaining' else {})}
                for key in (TOPICS if keys is None else keys) if ready(key)]

    def save(self, data, rows=None):
        if self.path is None:
            raise HTTPException(503, '무전 버튼 설정 저장소를 사용할 수 없습니다.')
        if self.load_error:
            raise HTTPException(409, self.load_error)
        if data.get('revision') != self.revision:
            raise HTTPException(409, '다른 창에서 저장했습니다. 창을 다시 열어 최신 설정을 확인하세요.')
        if rows is None:
            rows, _ = self.settings_rows(validate(data.get('rows')))
        if getattr(self, 'speech_inputs', None):
            self.speech_inputs.check_radio(rows)
        value = {'revision': self.revision + 1, 'rows': rows}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(self.path)
        self.rows, self.revision = rows, value['revision']
        self.reindex()
        self.pending = None
        return value

    def render(self, items, context, *, driving=False):
        parts, spoken, missing = [], [], []
        ids = {i for i in items if i.startswith('clip:')}
        fixed = self.library.resolve(ids) if ids else {}
        style = fuel_audio.style_for(getattr(self.coach, 'custom_packs', None))
        for item in items:
            recording = fixed.get(item)
            if driving and recording and recording.question:
                item, recording = recording.question, None
            candidates = [(recording.role, recording.source)] if recording else wordings(item, context, style)
            clips = []
            for role, text in candidates:
                try:
                    clips.append(self.read_clip(item, role, text))
                except (OSError, ValueError, KeyError):
                    missing.append(text)
                    # A rejoin position or stint length stands on its own. A label
                    # without the lap time, or a gap without the catch rate, does not.
                    if item not in ('pit_rejoin', 'stint_ahead', 'stint_behind'):
                        clips.clear()
                    break
            parts.extend(clips)
            spoken.extend(recording.name if recording else text for role, text in candidates[:len(clips)])
            if not candidates:
                missing.append(TOPICS[item][1] if item in TOPICS else '선택한 녹음 · 팩 다시 선택 또는 교체')
        return {'parts': parts, 'spoken': spoken, 'missing': missing}

    async def pump(self):
        from radio_button_input import WindowsButtons
        native = None
        try:
            native = WindowsButtons()
            previous, previous_keys, last_game = {}, set(), None
            while self.clients:
                game = native.foreground_game()
                changes = native.changes()
                edges = []
                held_before = previous_keys.union(*previous.values())
                for group, values in changes:
                    if group == 'removed':
                        previous = {k: v for k, v in previous.items() if not k.startswith(values)}
                    else:
                        edges.extend(sorted(values - previous.get(group, set())))
                        previous[group] = values
                captures = [q for q, capture in self.clients.items() if capture]
                speech = getattr(self, 'speech_inputs', None)
                assigned = self.shortcuts.get(game, []) + (speech.shortcuts() if speech and game else [])
                now_keys = native.shortcuts_down(assigned)
                if speech:
                    speech.poll(now_keys.union(*previous.values()), game if game == self.coach.live.game else None, bool(captures))
                if captures:
                    if edges:
                        for queue in captures:
                            if queue.empty():
                                queue.put_nowait({'code': edges[0]})
                elif game and game == last_game:
                    edges.extend(now_keys - previous_keys)
                    mapping = self.maps[game]
                    seen = {mapping[key]['id'] for key in held_before if key in mapping}
                    for key in edges:
                        row = mapping.get(key)
                        if row and row['id'] not in seen:
                            seen.add(row['id'])
                            self.emit(row, game)
                previous_keys, last_game = now_keys, game
                await asyncio.sleep(.016)
        except (OSError, AttributeError) as exc:
            for q in self.clients:
                if q.empty():
                    q.put_nowait({'error': f'Windows 입력을 열 수 없습니다. {exc}'})
        finally:
            if getattr(self, 'speech_inputs', None):
                self.speech_inputs.release()
            if native:
                native.close()

    def emit(self, row, game):
        live = self.coach.live
        with live.lock:
            _, sample = live._current_snapshot()
            if not sample or not sample.get('run_id') or live.game != game:
                return
            ticket = secrets.token_hex(12)
            self.pending = dict(ticket=ticket, expires=time.monotonic() + 5, claimed=False,
                                run=sample['run_id'], last=sample.get('lap_last_s'), items=row['items'], game=game)
            if 'corner_loss' in row['items'] or 'corner_gain' in row['items'] or any(k.startswith('clip:') for k in row['items']):
                self.pending['lap'] = sample.get('lap_number')
                self.pending['review'] = getattr(getattr(live, 'corners', None), 'review', None)
            if 'theoretical' in row['items']:
                self.pending['theoretical'] = live.lap_sectors.theoretical()
            if 'sector_delta' in row['items']:
                self.pending['sector_delta'] = live.lap_sectors.delta()
        for queue, capture in self.clients.items():
            if not capture:
                if not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({'ticket': ticket, 'game': game, 'run': sample['run_id']})

    async def stream(self, ws, capture):
        origin = ws.headers.get('origin')
        allowed = os.getenv('CORS_ORIGINS', DEFAULT_CORS_ORIGINS).split(',')
        if origin and origin not in (address.strip() for address in allowed):
            await ws.close(code=1008)
            return
        await ws.accept()
        if os.name != 'nt':
            await ws.send_json({'error': 'Windows에서 휠 입력을 연결할 수 있습니다.'})
            await ws.close()
            return
        queue = asyncio.Queue(maxsize=1)
        self.clients[queue] = capture
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.pump())
        closed = asyncio.create_task(ws.receive())
        try:
            await ws.send_json({'ready': True})
            while not closed.done():
                event = asyncio.create_task(queue.get())
                try:
                    done, _ = await asyncio.wait((closed, event), return_when=asyncio.FIRST_COMPLETED)
                    if closed in done:
                        break
                    await ws.send_json(event.result())
                    if capture or 'error' in event.result():
                        break
                finally:
                    event.cancel()
        except (WebSocketDisconnect, RuntimeError, OSError):
            pass
        finally:
            closed.cancel()
            self.clients.pop(queue, None)
            # The shared pump exits on its next tick. A new tab may already be
            # acquiring the same HID usages; keep registration under one owner.


def register(router, coach, spotter_audio=None):
    service = ButtonRadio(coach, spotter_audio)

    @router.get('/buttons')
    async def settings():
        if service.load_error:
            raise HTTPException(409, service.load_error)
        revision = service.revision
        rows = service.rows
        if any(k.startswith('clip:') for row in rows for k in row['items']):
            rows, names = await asyncio.to_thread(service.settings_rows, rows)
        else:
            names = {key: TOPICS[key][1] for row in rows for key in row['items'] if key in TOPICS}
        return {'revision': revision, 'rows': rows, 'names': names}

    @router.post('/buttons')
    async def save(body: dict):
        try:
            rows = validate(body.get('rows'))
            rows, _ = await asyncio.to_thread(service.settings_rows, rows)
            previous = service.rows
            if any(k.startswith('clip:') for r in previous for k in r['items']):
                previous, _ = await asyncio.to_thread(service.settings_rows, previous)
            existing = {r['id']: set(r['items']) for r in previous}
            saved_clips = {k for r in previous for k in r['items'] if k.startswith('clip:')}
            if any(k.startswith('clip:') and k not in saved_clips for r in rows for k in r['items']):
                raise ValueError('설치 음성은 ‘설치 음성 듣기’에서 들을 수 있습니다.')
            added = {k for r in rows for k in r['items'] if k in TOPICS and k not in existing.get(r['id'], ())}
            if added and len(await asyncio.to_thread(service.topic_catalog, added)) != len(added):
                raise ValueError('설치된 음성으로 답할 수 있는 질문을 선택하세요.')
            return service.save(body, rows)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/buttons/catalog')
    def catalog(q: str = '', offset: int = 0, group: str = '', kind: str = '', corner: int = 0, view: str = ''):
        if view == 'topics':
            return {'topics': service.topic_catalog()}
        result = service.library.browse(group, kind, corner, q, offset, flat=not view)
        if not view:
            result['topics'] = service.topic_catalog()
        return result

    @router.post('/buttons/preview')
    def preview(body: dict):
        items = body.get('items')
        if not isinstance(items, list) or not items or not all(isinstance(i, str) and (i in TOPICS or FIXED.fullmatch(i)) for i in items):
            raise HTTPException(422, '읽을 음성을 선택하세요.')
        game = body.get('game', EXAMPLE['game'])
        if game not in ('iracing', 'lmu'):
            raise HTTPException(422, '게임을 확인하세요.')
        return service.render([k for k in items if k != 'energy_remaining' or game == 'lmu'], {**EXAMPLE, 'game': game})

    @router.post('/buttons/play')
    async def answer(body: dict):
        receipt = service.pending
        if (not receipt or receipt['ticket'] != body.get('ticket') or receipt['claimed']
                or time.monotonic() >= receipt['expires']):
            raise HTTPException(410, '무전 요청 시간이 지났습니다.')
        # All tabs see the edge; only one can claim it before the first await.
        receipt['claimed'] = True
        budget_ms = int((receipt['expires'] - time.monotonic()) * 1000)
        run, game = receipt['run'], receipt['game']
        # The snapshot can wait on live measurements. Keep that wait off the
        # event loop shared by the spotter WebSocket and native button input.
        context = await asyncio.to_thread(coach.live.context, include_recent=False)
        if service.pending is not receipt or time.monotonic() >= receipt['expires']:
            raise HTTPException(410, '무전 요청 시간이 지났습니다.')
        snapshot = context.get('snapshot') or {}
        if not context['connected'] or snapshot.get('run_id') != run or context.get('game') != game:
            raise HTTPException(410, '주행이 바뀌었습니다.')
        context['snapshot'] = {**snapshot, 'lap_last_s': receipt['last']}
        context['theoretical'] = receipt.get('theoretical')
        context['sector_delta'] = receipt.get('sector_delta')
        if 'review' in receipt:
            context['corner_coach'] = {'last_lap': receipt['review']}
            context['review_lap_number'] = receipt['lap']
        result = await asyncio.to_thread(service.render, receipt['items'], context, driving=True)
        if not result['parts']:
            unavailable = '지금은 확인할 수 없어.'
            try:
                clip = await asyncio.to_thread(coach.fixed_audio.read_prepared, 'coach', unavailable)
            except (OSError, ValueError, KeyError):
                pass
            else:
                result['parts'].append(clip)
                result['spoken'].append(unavailable)
        if service.pending is not receipt or time.monotonic() >= receipt['expires']:
            raise HTTPException(410, '무전 요청 시간이 지났습니다.')
        with coach.live.lock:
            _, current = coach.live._current_snapshot()
            if not current or current.get('run_id') != run or coach.live.game != game:
                raise HTTPException(410, '주행이 바뀌었습니다.')
        return {**result, 'run': run, 'remaining_ms': budget_ms}

    @router.websocket('/buttons/input')
    async def input_events(socket: WebSocket, capture: bool = False):
        await service.stream(socket, capture)

    return service
