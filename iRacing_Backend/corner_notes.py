"""Private track-level notes with atomic, serialized writes."""
from __future__ import annotations
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import threading


def position_bucket(pct):
    value = float(pct)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError('코너 위치가 올바르지 않습니다.')
    return int(math.floor(value * 200 + .5))


class CornerNotesStore:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def _key(game, track_id, track_config, pct):
        return json.dumps([game, str(track_id), track_config or '', position_bucket(pct)], ensure_ascii=False)

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('notes'), dict):
            raise ValueError('메모 파일 형식을 확인해 주세요.')
        for key, value in data['notes'].items():
            identity = json.loads(key)
            if (not isinstance(identity, list) or len(identity) != 4
                    or identity[0] not in ('iracing', 'lmu') or not isinstance(identity[1], str)
                    or not isinstance(identity[2], str) or type(identity[3]) is not int or not 0 <= identity[3] <= 200
                    or not isinstance(value, dict) or not isinstance(value.get('text'), str)):
                raise ValueError('메모 파일 형식을 확인해 주세요.')
        return data['notes']

    def list(self, game, track_id, track_config):
        with self._lock:
            notes = self._load()
            scope = [game, str(track_id), track_config or '']
            return sorted([dict(value, pct=json.loads(key)[3] / 200)
                           for key, value in notes.items() if json.loads(key)[:3] == scope], key=lambda row: row['pct'])

    def put(self, game, track_id, track_config, pct, text, *, number=None, name=None):
        if game not in ('iracing', 'lmu') or not str(track_id or '').strip():
            raise ValueError('게임과 트랙 정보를 확인해 주세요.')
        if not isinstance(text, str) or len(text) > 300:
            raise ValueError('메모는 300자 이내로 입력해 주세요.')
        key = self._key(game, track_id, track_config, pct)
        row = {'text': text, 'updated_at': datetime.now(timezone.utc).isoformat(), 'number': number, 'name': name}
        with self._lock:
            notes = self._load()
            if text:
                notes[key] = row
            else:
                notes.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                 prefix=self.path.name + '.', suffix='.tmp', delete=False) as output:
                    temporary = Path(output.name)
                    json.dump({'version': 1, 'notes': notes}, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return dict(row, pct=position_bucket(pct) / 200)
