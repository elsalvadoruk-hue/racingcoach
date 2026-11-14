"""Locally reviewed whole-sentence takes; no synthesis or cloud fallback."""
import base64
import threading
import re
from pathlib import Path

from custom_voice_pack_format import inspect_pack, check_audio, audio_mime


class ReviewedRadio:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.loaded = False
        self.bindings, self.groups, self.cursors = {}, {}, {}

    def _load(self):
        if self.loaded:
            return
        if not (self.directory / 'manifest.json').is_file():
            self.loaded = True
            return
        if self.directory.is_symlink() or self.directory.is_junction():
            raise ValueError('검수 음성 폴더의 외부 연결은 사용할 수 없습니다.')
        manifest = inspect_pack(self.directory)
        groups, bindings = {}, {}
        for row in manifest['entries']:
            if (row.get('reviewedVoice') not in ('Fenrir', 'Charon')
                    or not isinstance(row.get('eventKey'), str) or not re.fullmatch('[a-z_]{1,64}', row['eventKey'])
                    or not isinstance(row.get('sourceTexts'), list) or not 1 <= len(row['sourceTexts']) <= 128
                    or any(not isinstance(t, str) or not 1 <= len(t) <= 2000 for t in row['sourceTexts'])):
                raise ValueError('검수 음성의 연결 정보를 확인하세요.')
            group = (row['role'], row['reviewedVoice'], row['eventKey'])
            groups.setdefault(group, []).append(row)
            for text in row['sourceTexts']:
                key = (row['role'], row['reviewedVoice'], text)
                if key in bindings and bindings[key] != group:
                    raise ValueError('검수 음성의 문장 연결이 중복됩니다.')
                bindings[key] = group
        self.groups, self.bindings, self.loaded = groups, bindings, True

    def _group(self, role, voice, text=None, event=None):
        self._load()
        voice = voice.rsplit('-', 1)[-1]
        return (role, voice, event) if event else self.bindings.get((role, voice, text))

    def has(self, role, voice, text):
        with self.lock:
            return self._group(role, voice, text) in self.groups

    def _audio(self, row):
        path = self.directory / row['audio']
        if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
            raise ValueError('검수 음성 폴더 밖의 파일은 사용할 수 없습니다.')
        data = path.read_bytes()
        check_audio(row, data)
        return 'data:' + audio_mime(row) + ';base64,' + base64.b64encode(data).decode('ascii')

    def audio(self, role, voice, text):
        with self.lock:
            group = self._group(role, voice, text)
            choices = self.groups[group]
            index = self.cursors.get(group, 0) % len(choices)
            audio = self._audio(choices[index])
            self.cursors[group] = index + 1
            return audio

    def choices(self, role, voice, event):
        with self.lock:
            group = self._group(role, voice, event=event)
            rows = self.groups.get(group, [])
            return [{'text': row['text'], 'audio_base64': self._audio(row), 'reviewed': True} for row in rows]
