"""Select existing short-radio files without changing audio or default voice caches."""
from collections import defaultdict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from custom_voice_pack_format import check_audio, validate_manifest

CATALOG_FORMAT = 'racingcoach-short-radio-review-catalog'
SELECTION_FORMAT = 'racingcoach-short-radio-selection'
FRONT_IDS = {f'class_traffic_ahead-take{number}' for number in (2, 4, 5)}


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ShortRadioSelection:
    def __init__(self, directory):
        raw = Path(directory).absolute()
        if any(path.is_symlink() or path.is_junction() for path in (raw, *raw.parents)):
            raise ValueError('검수 폴더의 외부 연결은 사용할 수 없습니다.')
        self.directory = raw.resolve()
        self.lock = threading.RLock()

    def _path(self, name):
        path = self.directory / name
        if not path.is_relative_to(self.directory):
            raise ValueError('검수 폴더 밖의 파일은 사용할 수 없습니다.')
        chain = [path, *path.parents[:len(path.parents) - len(self.directory.parents)]]
        if any(part.is_symlink() or part.is_junction() for part in chain):
            raise ValueError('검수 폴더의 외부 연결은 사용할 수 없습니다.')
        if not path.resolve().is_relative_to(self.directory):
            raise ValueError('검수 폴더 밖의 파일은 사용할 수 없습니다.')
        return path

    def catalog(self):
        path = self._path('review-catalog.json')
        if not path.exists():
            path = self._path('review-catalog.default.json')
        catalog = json.loads(path.read_text(encoding='utf-8'))
        if catalog.get('format') != CATALOG_FORMAT or catalog.get('schemaVersion') != 1:
            raise ValueError('지원하지 않는 검수 목록입니다.')
        signature = sha(json_bytes({key: value for key, value in catalog.items() if key != 'sha256'}))
        if signature != catalog.get('sha256'):
            raise ValueError('검수 목록 확인값이 다릅니다.')
        validate_manifest(catalog['manifest'])
        metadata = catalog.get('metadata', {})
        if not isinstance(metadata, dict) or set(metadata) != {row['id'] for row in catalog['manifest']['entries']}:
            raise ValueError('음원 출처와 검수 목록이 일치하지 않습니다.')
        groups = defaultdict(set)
        for row in catalog['manifest']['entries']:
            meta = metadata[row['id']]
            if (not isinstance(meta, dict) or type(meta.get('priorApproved')) is not bool
                    or meta.get('kind') not in ('natural', 'tempo')
                    or not isinstance(meta.get('rootId'), str)
                    or not isinstance(meta.get('rootPcmSha256'), str)):
                raise ValueError('음원의 승인 이력과 출처 정보를 확인하세요.')
            if (row.get('role'), row.get('reviewedVoice')) not in {('spotter', 'Fenrir'), ('race', 'Charon')}:
                raise ValueError('이 검수실은 Fenrir·Charon 짧은 무전만 적용합니다.')
            if (row['text'] != row['spoken'] or any(word in row['spoken'] for word in ('습니다', '입니다', '세요', '십시오'))
                    or any(character.isdigit() for character in row['spoken'])):
                raise ValueError('숫자 또는 긴 문구는 짧은 무전 검수 대상에 추가할 수 없습니다.')
            groups[(row['role'], row['reviewedVoice'], row['eventKey'])].add(row['spoken'])
        if any(len(texts) != 1 for texts in groups.values()):
            raise ValueError('같은 안내에 서로 다른 문구를 넣을 수 없습니다.')
        return catalog

    def state(self):
        with self.lock:
            catalog = self.catalog()
            current_bytes = self._path('manifest.json').read_bytes()
            current = validate_manifest(json.loads(current_bytes))
            allowed = {row['id']: row for row in catalog['manifest']['entries']}
            if any(row['id'] not in allowed or row != allowed[row['id']] for row in current['entries']):
                raise ValueError('실사용 목록이 검수 목록과 달라졌습니다. 새 목록부터 확인하세요.')
            previous = None
            path = self._path('review-selection.json')
            if not path.exists():
                path = self._path('review-selection.default.json')
            if path.exists():
                previous = json.loads(path.read_text(encoding='utf-8'))
                if (previous.get('catalogSha256') != catalog['sha256']
                        or previous.get('activeManifestSha256') != sha(current_bytes)):
                    previous = None
            return {'catalog': catalog, 'activeIds': [row['id'] for row in current['entries']],
                    'activeManifestSha256': sha(current_bytes), 'previousSelection': previous}

    def validate(self, selection):
        with self.lock:
            state = self.state()
            catalog = state['catalog']
            if not isinstance(selection, dict) or selection.get('format') != SELECTION_FORMAT or selection.get('schemaVersion') != 1:
                raise ValueError('이 검수실에서 내보낸 테이크 선택 JSON을 사용하세요.')
            if selection.get('catalogSha256') != catalog['sha256']:
                raise ValueError('다른 검수 목록의 선택 파일입니다.')
            if selection.get('activeManifestSha256') != state['activeManifestSha256']:
                raise ValueError('실사용 목록이 이미 바뀌었습니다. 현재 목록을 새로 불러오세요.')
            rows = {row['id']: row for row in catalog['manifest']['entries']}
            sets = []
            for key in ('selectedIds', 'rejectedIds'):
                values = selection.get(key, [])
                if (not isinstance(values, list) or any(not isinstance(value, str) for value in values)
                        or len(values) != len(set(values)) or set(values) - rows.keys()):
                    raise ValueError('검수 목록에 없는 테이크 또는 중복 선택이 있습니다.')
                sets.append(set(values))
            selected, rejected = sets
            if selected & rejected:
                raise ValueError('같은 테이크를 사용과 제외에 함께 넣을 수 없습니다.')
            front = {row['id'] for row in rows.values() if row['eventKey'] == 'class_traffic_ahead'}
            if front and (front != FRONT_IDS or not FRONT_IDS <= selected):
                raise ValueError('앞 클래스는 원래 테이크 2·4·5를 모두 유지해야 합니다.')
            groups = defaultdict(list)
            for row in rows.values():
                groups[row['eventKey']].append(row)
            missing = [items[0]['spoken'] for items in groups.values() if not any(row['id'] in selected for row in items)]
            if missing:
                raise ValueError('다음 안내마다 사용할 테이크를 하나 이상 골라주세요: ' + ' / '.join(missing))
            excessive = [items[0]['spoken'] for items in groups.values()
                         if sum(row['id'] in selected for row in items) > 5]
            if excessive:
                raise ValueError('안내별로 최대 5개만 선택할 수 있습니다: ' + ' / '.join(excessive))
            notes = selection.get('notes', {})
            if (not isinstance(notes, dict) or set(notes) - rows.keys()
                    or any(not isinstance(note, str) or len(note) > 2000 for note in notes.values())):
                raise ValueError('메모는 테이크별로 2,000자까지 저장할 수 있습니다.')
            manifest = deepcopy(catalog['manifest'])
            manifest['entries'] = [row for row in manifest['entries'] if row['id'] in selected]
            validate_manifest(manifest)
            seen = set()
            for row in manifest['entries']:
                if row['sha256'] in seen:
                    raise ValueError('동일한 음원 파일을 중복 선택했습니다.')
                seen.add(row['sha256'])
                check_audio(row, self._path(row['audio']).read_bytes())
            return manifest, state

    def _atomic(self, name, data):
        path = self._path(name)
        temporary = self._path('.review-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def apply(self, selection):
        with self.lock:
            manifest, state = self.validate(selection)
            old = self._path('manifest.json').read_bytes()
            if sha(old) != state['activeManifestSha256']:
                raise ValueError('저장 중 실사용 목록이 바뀌었습니다. 다시 불러오세요.')
            manifest['version'] = manifest['version'].split('.selection', 1)[0] + '.selection' + str(time.time_ns())
            self._atomic('manifest.previous.json', old)
            new_bytes = json_bytes(manifest)
            saved = deepcopy(selection)
            # Listening approval comes from the existing local record, not an import.
            previous = state['previousSelection'] or {}
            selected = set(saved['selectedIds'])
            approved = previous.get('userApprovedIds', [])
            saved['userApprovedIds'] = [name for name in approved if name in selected]
            saved.pop('singleReplacement', None)
            replacement = previous.get('singleReplacement')
            if isinstance(replacement, dict) and replacement.get('newId') in selected:
                saved['singleReplacement'] = deepcopy(replacement)
            saved['activeManifestSha256'] = sha(new_bytes)
            self._atomic('review-selection.json', json_bytes(saved))
            self._atomic('manifest.json', new_bytes)
            return {'selectedFiles': len(manifest['entries']), 'groups': len({row['eventKey'] for row in manifest['entries']}),
                    'message': '선택 목록을 저장했습니다. 앱과 검은 실행창을 닫고 실행.bat으로 다시 열면 반영됩니다.'}
