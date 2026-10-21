"""Fetch a compatible, precompiled LMU reader; never run a compiler or open LMU.

Assets come only from the application's own GitHub release. SDK/source/DLL
hashes and the x64 PE header are checked before an immutable cache is installed.
The live telemetry thread only starts a worker and never waits for the network.
"""
from __future__ import annotations

import io
import json
import logging
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile

import lmu_shared_memory as sm

DOWNLOAD_BASE = 'https://github.com/elsalvadoruk-hue/racingcoach/releases/download/lmu-bridges'
MAX_BYTES = 4 << 20
RETRY_SECONDS = 300


def asset_name(sdk_dir=None):
    return sm.build_artifact_dir(sdk_dir).name + '-win-x64.zip'


def fetch_asset(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'RacingCoach-LMU', 'Accept': 'application/octet-stream'})
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('LMU 연결 파일이 허용 크기를 넘었습니다.')
    return data


def install_asset(data, sdk_dir):
    if len(data) > MAX_BYTES:
        raise ValueError('LMU 연결 파일이 허용 크기를 넘었습니다.')
    identity = sm.build_artifact_dir(sdk_dir).name
    destination = sm.downloaded_bridge_dir(sdk_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / ('.download-' + uuid.uuid4().hex)
    artifact = staging / identity
    artifact.mkdir(parents=True)
    # No arbitrary archive paths, duplicate members, links or extra payloads.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if (len(members) != 2 or {m.filename for m in members} != {'manifest.json', 'lmu_native_bridge.dll'} or
                sum(m.file_size for m in members) > MAX_BYTES or
                any((m.external_attr >> 16) & 0o170000 == 0o120000 for m in members)):
            raise ValueError('LMU 연결 파일 구성이 올바르지 않습니다.')
        for member in members:
            (artifact / member.filename).write_bytes(archive.read(member))
    sm.validate_bundled_bridges(staging)
    manifest = json.loads((artifact / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['header_fingerprint'] != sm.header_fingerprint(sdk_dir):
        raise sm._unavailable('ABI_MISMATCH')
    # A requested identity includes both the installed SDK and current reader
    # source. Recheck in case Steam updated the SDK during the download.
    if sm.build_artifact_dir(sdk_dir).name != identity:
        raise sm._unavailable('ABI_MISMATCH')
    if destination.exists():
        # Another process may have completed the same immutable download.
        identical = all((destination / name).is_file() and
                        (destination / name).read_bytes() == (artifact / name).read_bytes()
                        for name in ('manifest.json', 'lmu_native_bridge.dll'))
        if not identical:
            # Preserve a broken cache instead of editing a DLL that another
            # process may have loaded. Both paths stay inside this exact cache.
            preserved = destination.with_name(destination.name + '.invalid-' + uuid.uuid4().hex)
            if destination.resolve().parent != destination.parent.resolve() or preserved.resolve().parent != destination.parent.resolve():
                raise ValueError('LMU 연결 파일 저장 경로를 확인할 수 없습니다.')
            destination.rename(preserved)
            artifact.rename(destination)
    else:
        try:
            artifact.rename(destination)
        except FileExistsError:
            for name in ('manifest.json', 'lmu_native_bridge.dll'):
                if (destination / name).read_bytes() != (artifact / name).read_bytes():
                    raise ValueError('저장된 LMU 연결 파일을 확인할 수 없습니다.')
    return destination


class BridgeDownloads:
    def __init__(self, fetch=fetch_asset, clock=time.monotonic):
        self.fetch, self.clock = fetch, clock
        self._lock = threading.Lock()
        self._states = {}

    def status(self, sdk_dir):
        identity = sm.build_artifact_dir(sdk_dir).name
        with self._lock:
            return dict(self._states.get(identity, {'state': 'CONNECTION_NEEDED'}))

    def request(self, sdk_dir, *, retry=False):
        sdk_dir = sm.resolve_sdk_dir(sdk_dir)
        identity = sm.build_artifact_dir(sdk_dir).name
        with self._lock:
            current = self._states.get(identity)
            if current and (current['state'] == 'CONNECTION_PREPARING' or current['state'] == 'READY' and not retry or
                            not retry and self.clock() - current['checked_at'] < RETRY_SECONDS):
                return dict(current)
            self._states[identity] = {'state': 'CONNECTION_PREPARING', 'checked_at': self.clock()}
        threading.Thread(target=self._run, args=(identity, sdk_dir), name='LMU-connection-download', daemon=True).start()
        return self.status(sdk_dir)

    def _run(self, identity, sdk_dir):
        try:
            data = self.fetch(DOWNLOAD_BASE + '/' + identity + '-win-x64.zip')
            install_asset(data, sdk_dir)
            state = 'READY'
        except urllib.error.HTTPError as exc:
            state = 'CONNECTION_UPDATE_PENDING' if exc.code == 404 else 'CONNECTION_DOWNLOAD_FAILED'
        except Exception as exc:
            logging.getLogger('iRacingLive').warning('LMU connection download failed: %s', type(exc).__name__)
            state = 'CONNECTION_DOWNLOAD_FAILED'
        with self._lock:
            self._states[identity] = {'state': state, 'checked_at': self.clock()}


downloads = BridgeDownloads()
