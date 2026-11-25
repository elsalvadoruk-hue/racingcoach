"""Browse installed whole recordings by driving situation, preserving their playback IDs."""
from collections import Counter
from fnmatch import fnmatchcase
from functools import lru_cache
import hashlib
import os
import re
import threading
from typing import NamedTuple

import button_fuel_audio as fuel_audio
import race_strategy_clips as strategy_audio
from radio_audio import fixed_catalog_entries
from radio_phrases import PHRASES, catalog_entries
from race_call_audio import BY_WORDING, CALLS as RACE_CALLS, EXTRA_SHORTS, RACE_AUDIO_DIR, prepared_file
from voice_alerts import CATALOG, category


LABELS = {f"{scope}.{item['id']}": (group['title'], item['label'])
          for scope, groups in CATALOG.items() for group in groups for item in group['items']}
ROLES = {'coach': '주행 코치', 'race': '레이스 엔지니어', 'strategy': '전략 엔지니어', 'spotter': '스포터'}
# Recording keys can be more specific than the preference controlling their live event.
LIBRARY_KINDS = (
    ('코너 코칭', '제동', 'corner*brake* learned_brake* learned_release* coach_braking'),
    ('코너 코칭', '가속', 'corner*throttle* learned_throttle*'),
    ('코너 코칭', '최저속도', 'corner*min_speed'),
    ('코너 코칭', '시간 손실', 'corner*time brief_corner_loss brief_corner_also'),
    ('코너 코칭', '구간 기록 비교', 'brief_best_corner brief_gain delta_*'),
    ('코너 코칭', '언더스티어·오버스티어', 'coach_understeer* coach_oversteer*'),
    ('코너 코칭', '연속 코너·연석', 'learned_chain* learned_impact*'),
    ('코너 코칭', '구간 연습', 'drill_*'),
    ('경쟁 상황·코칭', '앞차 간격·추격', 'gap_ahead* brief_gap_ahead ahead_far catch_rate catch_in catch_need rival_ahead*'),
    ('경쟁 상황·코칭', '뒤차 간격·방어', 'gap_behind* brief_gap_behind lose_rate caught_in rival_behind*'),
    ('경쟁 상황·코칭', '페이스 비교', 'by_faster by_slower pace_*'),
    ('타이어 상태', '공기압', 'tire_pressure* tire_advice* tire_track_temp*'),
    ('타이어 상태', '온도·마모', 'tire_temperature* tire_trend* tire_input* tire_tires_ok'),
    ('무전 확인', '수신 확인', 'ack'),
    ('무전 확인', '음성 테스트', 'preview_* test'),
)
ALIASES = {
    'brief:stint_ahead': 'stint_* stops_*', 'brief:rejoin': 'rejoin_* pit_rejoin',
    'lap_time': 'lap_time_read', 'personal_best': 'personal_best_read', 'position': 'position_*',
    'briefing': 'form_*', 'brief:theoretical': 'theoretical_gain label_*', 'class_traffic': 'class_traffic_*',
    'last_lap': 'last_lap_*', 'damage_check': 'damage_* repair_*', 'tow': 'tow_time',
    'fuel_feedback': 'fuel_save_* fuel_lap', 'fuel_short': 'fuel_short_by', 'pit_fuel_plan': 'pit_fuel_*',
    'pit_window': 'box_now box_end_of_lap pit_window_* pit_run_dry', 'tyre_plan': 'tyre_*',
    'finish_laps': 'finish_laps_*', 've_save': 've_target',
    'hold': 'oval_hold_*', 'clear_left': 'oval_clear_*', 'left': 'oval_*',
}
CORNER = re.compile(r'(\d+)번\s*코너')
# Older buttons stored recordings. The editor converts these to questions,
# including retired question IDs so their old shortcuts can be cleared.
QUESTIONS = {
    'brief_corner_loss': 'corner_loss', 'brief_corner_also': 'corner_loss',
    'brief_gap_ahead': 'ahead', 'ahead_far': 'ahead', 'brief_gap_behind': 'behind',
    'lap_time_read': 'last', 'personal_best_read': 'best', 'position_now': 'position',
    'laps_to_go': 'laps', 'laps_left': 'laps', 'time_left': 'time', 'form_fuel': 'fuel',
    'pit_rejoin': 'pit_rejoin', 'stint_ahead': 'stint_ahead', 'stint_behind': 'stint_behind',
    'stops_none_ahead': 'stops_ahead', 'stops_ahead': 'stops_ahead',
    'stops_none_behind': 'stops_behind', 'stops_behind': 'stops_behind',
    'brief_best_corner': 'corner_gain', 'form_pit_loss': 'pit_transit', 'lose_rate': 'closing_behind',
    'control_tc_more': 'tc', 'control_abs_more': 'abs', 'control_abs_wet_map': 'abs',
    'control_bias_forward': 'bias',
}


def recording_question(event, source):
    if event == 'control_bias_step' and '바이어스' in source:
        return 'bias'
    return QUESTIONS.get(event)
RACE_WORDINGS = {f'{key}-{style}.ogg': words for key, (_, _, standard, short) in RACE_CALLS.items()
                 for style, words in (('default', standard), ('short', short))}
RACE_WORDINGS.update({f'{key}-short.ogg': short for key, (_, short) in EXTRA_SHORTS.items()})


@lru_cache(maxsize=1024)
def classification(key, role):
    for group, kind, patterns in LIBRARY_KINDS:
        if any(fnmatchcase(key, pattern) for pattern in patterns.split()):
            return group, kind
    mapped = category(key)
    if mapped is None:
        mapped = next((category(alias) for alias, patterns in ALIASES.items()
                       if any(fnmatchcase(key, p) for p in patterns.split())), None)
    return LABELS.get(mapped, ('기타 음성', ROLES.get(role, role)))


def clip_id(role, text, pack=None):
    identity = f'{role}\n{text}' if pack is None else f'pack:{pack}\n{role}\n{text}'
    return 'clip:' + hashlib.sha256(identity.encode()).hexdigest()


class Recording(NamedTuple):
    role: str
    source: str
    name: str
    group: str
    kind: str
    question: str | None


@lru_cache(maxsize=1)
def fuel_recordings():
    rows = {}
    for text, (topic, _, style) in fuel_audio.by_wording().items():
        mode = '짧은 무전' if style == 'short' else '기본 무전'
        rows[clip_id('strategy', text)] = Recording('strategy', text, text, fuel_audio.GROUP,
                                                   f'{fuel_audio.KINDS[topic]} · {mode}', None)
    return rows


@lru_cache(maxsize=1)
def strategy_recordings():
    rows={}
    for key,role,pair in strategy_audio.catalog():
        group,kind=classification(key,role)
        for mode,text in zip(('기본 무전','짧은 무전'),pair):
            rows[clip_id(role,text)]=Recording(role,text,text,group,f'{kind} · {mode}',None)
    return rows


@lru_cache(maxsize=1)
def defaults():
    from number_splice import LAP_KEYS
    from spotter import OPTIONAL_CLIPS, VARIANTS
    # Lap times and SOFs are assembled during playback. They have no individual recording.
    composed = {*LAP_KEYS, 'form_sof', 'form_sof_class'}
    entries = dict(((role, text), key) for key, role, text in fixed_catalog_entries())
    entries.update({(role, text): key for key, (role, variants) in PHRASES.items() for text in variants if '{' not in text})
    entries.update({(role, text): key for key, role, text in catalog_entries(k for k in PHRASES if k not in composed)})
    entries.update({binding: (standard or short).split('-')[0] for binding, (standard, short) in BY_WORDING.items()})
    entries.update({('spotter', text): key for key, choices in VARIANTS.items() for text in choices})
    entries.update({('spotter', text): key for key, text in OPTIONAL_CLIPS.items()})
    return {clip_id(role, text): Recording(role, text, text, *classification(key, role), recording_question(key, text))
            for (role, text), key in entries.items()}


def directory_stamp(directory):
    try:
        return directory.stat().st_mtime_ns
    except OSError:
        return None


def audio_files(directory):
    try:
        with os.scandir(directory) as files:
            return {entry.name for entry in files if entry.is_file(follow_symlinks=False) and entry.stat().st_size >= 100}
    except FileNotFoundError:
        return set()


class VoiceLibrary:
    def __init__(self, audio, packs, spotter):
        self.audio, self.packs, self.spotter = audio, packs, spotter
        self.lock = threading.RLock()
        self.selection = self.loaded = self.stamp = None
        self.packed, self.files, self.rows = {}, {}, []

    def _update_packs(self):
        with self.packs.lock:
            selection = tuple(sorted(self.packs.selections.items()))
            if selection == self.selection and self.packs.loaded is self.loaded:
                return
            loaded = self.packs.loaded
        packed, files = {}, {}
        for role, key in selection:
            folder = self.packs._folder(key)
            for (owner, source), row in loaded[key][1].items():
                if owner != role:
                    continue
                identifier = clip_id(role, source, key)
                event = row.get('eventKey')
                event = event if isinstance(event, str) else ''
                group, kind = classification(event, role)
                question = recording_question(event, source)
                if group == '기타 음성':
                    default = defaults().get(clip_id(role, source))
                    if default:
                        group, kind = default.group, default.kind
                        question = default.question
                packed[identifier] = Recording(role, source, row.get('text') or source, group, kind, question)
                if row.get('audio'):
                    files[identifier] = folder / row['audio']
        self.packed, self.selection, self.loaded = packed, selection, loaded
        self.files = files
        self.pack_dirs = tuple({p.parent for p in files.values()})
        self.stamp = None

    def resolve(self, ids):
        with self.lock:
            self._update_packs()
            result = {}
            for key in ids:
                item = self.packed.get(key) or fuel_recordings().get(key) or strategy_recordings().get(key) or defaults().get(key)
                if item is None:
                    continue
                if key not in self.packed and (item.role, item.source) in BY_WORDING:
                    path = prepared_file(item.role, item.source, self.packs, self.audio.directory)
                    if path:
                        item = item._replace(name=RACE_WORDINGS[path.name])
                result[key] = item
            return result

    def _installed(self):
        from race_engineer import role_voice
        self._update_packs()
        radio = self.audio.directory
        spot = self.spotter.directory if self.spotter else None
        fuel_dirs = {(topic,style): fuel_audio.directory(radio,topic,style)
                     for topic in fuel_audio.LIMITS for style in fuel_audio.STYLES}
        strategy_dir=radio/'pieces'/'race-strategy'
        roots = [radio, radio / RACE_AUDIO_DIR, strategy_dir, *fuel_dirs.values(), *self.pack_dirs] + ([spot] if spot else [])
        stamp = (tuple(directory_stamp(p) for p in roots), tuple(role_voice(r) for r in ROLES),
                 self.spotter.voice() if spot else None)
        if stamp == self.stamp:
            return self.rows
        radio_files, spot_files = audio_files(radio), audio_files(spot) if spot else set()
        installed = {directory: audio_files(directory) for directory in self.pack_dirs}
        rows, aliases, replaced = [], {}, set()
        for key, item in self.packed.items():
            path = self.files.get(key)
            if not path or path.name not in installed[path.parent]:
                continue
            role, source, name, group, kind, _ = item
            replaced.add((role, source))
            signature = (path, role, group, kind, name)
            if signature in aliases:
                aliases[signature].append(source)
            else:
                sources = [source]
                aliases[signature] = sources
                rows.append((key, item, sources))
        recorded = fuel_recordings()
        for (topic,style), folder in fuel_dirs.items():
            available = audio_files(folder)
            for n in sorted(int(name[:-4]) for name in available & fuel_audio.filenames(topic)):
                words = fuel_audio.wording(topic,n,style)
                identifier = clip_id('strategy',words)
                if ('strategy',words) not in replaced:
                    rows.append((identifier,recorded[identifier],[words]))
        strategy_files=audio_files(strategy_dir)
        if strategy_files:
            for identifier,item in strategy_recordings().items():
                if (item.role,item.source) in replaced:
                    continue
                paths=strategy_audio.files(radio,item.role,item.source,self.packs)
                if paths and paths[0].name in strategy_files:
                    rows.append((identifier,item,[item.source]))
        for key, item in defaults().items():
            role, source = item.role, item.source
            if (role, source) in replaced:
                continue
            if (role, source) in BY_WORDING:
                path = prepared_file(role, source, self.packs, radio)
                if path:
                    if path not in aliases:
                        aliases[path] = []
                        rows.append((key, item._replace(name=RACE_WORDINGS[path.name]), aliases[path]))
                    aliases[path].append(source)
                continue
            ready = radio_files and self.audio.path(role, source).name in radio_files
            if not ready and role == 'spotter' and spot_files:
                ready = self.spotter.path(source).name in spot_files
            if ready:
                rows.append((key, item, [source]))
        self.rows = [(key, item, int(turn[1]) if (turn := CORNER.search(item.name) or CORNER.search(item.source)) else 0,
                      '\n'.join([item.name, *sources]).casefold()) for key, item, sources in rows]
        self.stamp = stamp
        return self.rows

    def browse(self, group='', kind='', corner=0, q='', offset=0, flat=False):
        with self.lock:
            rows = self._installed()
        groups, kinds, corners = Counter(), Counter(), Counter()
        clips, total = [], 0
        query = q.strip().casefold()
        offset = max(0, offset)
        for key, item, turn, search in rows:
            if query and query not in search and query not in (item.group + ' ' + item.kind).casefold():
                continue
            groups[item.group] += 1
            if group and item.group != group:
                continue
            kinds[item.kind] += 1
            if kind and item.kind != kind:
                continue
            if turn:
                corners[turn] += 1
            if corner and turn != corner:
                continue
            if (kind or query or flat) and offset <= total < offset + 40:
                clips.append(dict(id=key, name=item.name, group=item.group, kind=item.kind, ready=True))
            total += 1
        return dict(groups=dict(sorted(groups.items())), kinds=dict(sorted(kinds.items())), corners=sorted(corners),
                    clips=clips, total=total, more=bool(clips) and total > offset + len(clips))
