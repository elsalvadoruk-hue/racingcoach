"""The extra parts of a session log: nearby cars (races) and the driver's own measured drive.

Kept apart from backend_api so the rules can be tested without the app: what the app knows
(where the race logs are, how to find and analyse a recording, whether the driver is on track)
is passed in. Both parts are made only from files already on this computer; nothing is
recorded for them, and they follow session_log's size rule (session_log.EXTRA_ORDER).
"""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
import struct
import time

import drive_summary
import log_pack
from session_log import Deferred, Extra, scrub

MAX_RECORDINGS = 3
MAX_RECORDING_SECONDS = 7 * 86400
OVAL_WINDOW_ROWS, OVAL_WINDOWS, OVAL_PACKED_LIMIT = 200, 6, 160_000


def _oval_reduction(path, run_id, radio=()):
    """Lossless rows in bounded windows, explicitly separated by omitted ranges.

    Runs only in the exporter when the full ZIP is too large. No new collection,
    interpolation or SDK work; the original gzip remains unchanged.
    """
    header, rows = log_pack.read_lines(path)
    if header.get('run_id') != run_id or header.get('spotter_profile') != 'ASPHALT_OVAL' or not rows:
        return None
    from oval_review import select_windows
    width = min(OVAL_WINDOW_ROWS, len(rows))
    while width:
        selected, selection = select_windows(header, rows, radio, width, OVAL_WINDOWS)
        packed = log_pack.pack(scrub({**header, 'export_selection': selection}), selected)
        if len(packed) <= OVAL_PACKED_LIMIT:
            return Extra('nearby_cars', {'traffic.packed.xz': packed},
                         {'coverage': selection['mode'], 'traffic': selection,
                          'timing': {'included': False, 'reason': 'size_limit'}})
        width //= 2
    return None


def _header(path):
    """First line of a gzip'd JSON-lines log, or None."""
    import gzip
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as handle:
            return json.loads(handle.readline() or 'null')
    except (OSError, ValueError, EOFError):
        return None


def find_log(folder, pattern, run_id, started_at):
    """The race log of one run in a folder (matched by the run id in its header)."""
    if folder is None:
        return None
    folder = Path(folder)
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            if path.stat().st_mtime < started_at - 60:
                break
        except OSError:
            continue
        header = _header(path)
        if isinstance(header, dict) and header.get('run_id') == run_id:
            return path
    return None


def is_race(run):
    return str(run.get('session_type') or '').lower() == 'race'


def nearby_cars(traffic_folder, timing_folder, *, prepare=None):
    """Extra provider: the run's traffic and timing logs, packed without loss."""
    def provider(run):
        from oval_spotter import is_oval
        if not is_race(run) and not (str(run.get('session_type') or '').strip().lower() == 'practice'
                                     and is_oval(run.get('identity') or {})):
            return Extra('nearby_cars', info={'reason': 'not_race'})
        if prepare is not None:
            if prepare(run['run_id']) is False:
                return Extra('nearby_cars', info={'reason': 'write_pending'})
        files, info, fallback = {}, {}, None
        for key, folder, pattern in (('traffic', traffic_folder, 'traffic-*.jsonl.gz'),
                                     ('timing', timing_folder, 'timing-*.jsonl.gz')):
            path = find_log(folder, pattern, run['run_id'], run['started_at'])
            if path is None:
                continue
            header, rows = log_pack.read_lines(path)
            # The logs hold no names; scrub anyway in case a header ever gains a path.
            files[f'{key}.packed.xz'] = log_pack.pack(scrub(header), rows)
            if key == 'traffic' and is_oval(run.get('identity') or {}):
                fallback = lambda path=path: _oval_reduction(path, run['run_id'], run.get('radio_selection', ()))
            info[key] = {'rows': len(rows), 'seconds': round(rows[-1]['t'] - rows[0]['t'], 1) if len(rows) > 1 else 0.,
                         'raw_bytes': path.stat().st_size}
        if not files:
            return Extra('nearby_cars', info={'reason': 'no_log'})
        return Extra('nearby_cars', files, info, fallback=fallback)
    return provider


def matches(identity, metadata):
    """A known run needs the same recorded car and track; missing identity is not a match."""
    for key in ('car_path', 'track_path'):
        want, have = (identity or {}).get(key), (metadata or {}).get(key)
        if want and (not have or str(want).casefold() != str(have).casefold()):
            return False
    want, have = (identity or {}).get('game'), (metadata or {}).get('game')
    if want and have and str(want).casefold() != str(have).casefold():
        return False
    return True


def recording_window(path):
    """Native recording times, independent of a renamed/copied file's name and mtime.

    IBT's disk subheader holds UTC start date and elapsed session times. LMU holds
    UTC RecordingTime plus the GPS Time channel. Read no other sample channels.
    Missing or corrupt clocks never fall back to the file's modification time.
    """
    path = Path(path)
    try:
        if path.suffix.casefold() == '.ibt':
            with path.open('rb') as handle:
                header = handle.read(144)
            if len(header) != 144:
                return None
            version, _, tick_rate, _, _, _, num_vars, _, _, buffer_len = struct.unpack_from('<10i', header)
            if min(version, tick_rate, num_vars, buffer_len) <= 0:
                return None
            started, first, last, _, count = struct.unpack_from('<Qddii', header, 112)
            if count <= 0:
                return None
        elif path.suffix.casefold() == '.duckdb':
            from lmu_lap_verdicts import _recording_start
            from lmu_telemetry import LmuTelemetryReader, _quote
            reader = LmuTelemetryReader(path)
            with reader._connection() as connection:
                tables = reader._tables(connection)
                started = _recording_start(reader._metadata(connection, tables).get('recording_time'))
                table = reader._table(tables, 'GPS Time')
                if started is None or table is None or 'value' not in {c.casefold() for c in tables[table]}:
                    return None
                first = connection.execute(f'SELECT value FROM {_quote(table)} ORDER BY rowid LIMIT 1').fetchone()
                last = connection.execute(f'SELECT value FROM {_quote(table)} ORDER BY rowid DESC LIMIT 1').fetchone()
                if not first or not last:
                    return None
                first, last = float(first[0]), float(last[0])
        else:
            return None
        if (not all(math.isfinite(v) for v in (started, first, last)) or started <= 0
                or first < 0 or not 0 < last - first <= MAX_RECORDING_SECONDS):
            return None
        return started, started + last - first
    except (OSError, ValueError, TypeError, struct.error, ImportError, RuntimeError):
        return None


def _session_kind(value):
    kind = str(value or '').strip().casefold()
    return 'qualifying' if kind in ('qualify', 'qualifying', 'open qualify', 'lone qualify') else kind


def recordings_for(run, candidates, metadata_of, diagnostics=None):
    """Same car/track/session kind and recorded time overlapping this run's calls.

    A recording may begin before the first call, finish after the last, or be one
    of several files from the run. Its save time cannot attach an adjacent run.
    """
    counts = diagnostics if diagnostics is not None else {}
    def count(key):
        counts[key] = counts.get(key, 0) + 1
    found = []
    for path in candidates:
        count('candidates')
        window = recording_window(path)
        if window is None:
            count('unreadable_window')
            continue
        if window[0] > run['ended_at'] or window[1] < run['started_at']:
            count('outside_run')
            continue
        try:
            metadata = metadata_of(path) or {}
        except (OSError, ValueError):
            count('metadata_unavailable')
            continue
        if not matches(run.get('identity'), metadata):
            count('identity_mismatch')
            continue
        want, have = _session_kind(run.get('session_type')), _session_kind(metadata.get('session_type'))
        if want and have and want != have:
            count('session_kind_mismatch')
            continue
        found.append((window[0], Path(path)))
    found.sort(key=lambda item: item[0])
    counts.update(matched=len(found), selected=min(MAX_RECORDINGS, len(found)),
                  omitted_by_limit=max(0, len(found) - MAX_RECORDINGS))
    return [path for _, path in found[-MAX_RECORDINGS:]]


def _stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_dev, stat.st_ino


def recording_ref(path, stamp, driving=lambda: False):
    """Content identity, only in the deferred export worker, with bounded memory.

    Paths, file names and filesystem identifiers never leave this function. A
    changed/replaced file cannot label an analysis made from its earlier bytes.
    """
    if _stamp(path) != stamp:
        return None
    digest = hashlib.sha256(b'racingcoach-recording-v1\0')
    with path.open('rb') as handle:
        while True:
            if driving():
                raise Deferred('driver on track')
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return 'sha256:' + digest.hexdigest() if _stamp(path) == stamp else None


def _coverage(run, window):
    origin, end = run['started_at'], run['ended_at']
    return {'time_origin': 'run_start', 'unit': 'seconds',
            'run_window': [0., end - origin],
            'recording_window': [window[0] - origin, window[1] - origin],
            'overlap_window': [max(0., window[0] - origin), min(end, window[1]) - origin],
            'analysis_scope': 'whole_selected_recording', 'full_recording_replay': False}


def own_drive(candidates, metadata_of, analyze, driving=lambda: False, defer=True, app_version='', traces=None):
    """Extra provider: the driver's own recordings of the run as measured values.

    ``analyze(path)`` returns (analysis result, metadata, handling with observations). It is
    heavy, so with ``defer`` it waits (Deferred) while the driver is on track.
    """
    def provider(run):
        selection = {}
        paths = recordings_for(run, candidates(), metadata_of, selection)
        if not paths:
            detail = ('no_candidates' if not selection.get('candidates') else
                      'unreadable_window' if selection.get('unreadable_window') == selection['candidates'] else
                      'outside_run' if selection.get('outside_run') == selection['candidates'] else 'no_matching_recording')
            return Extra('drive_summary', info={'reason': 'no_recording', 'reason_detail': detail, 'selection': selection})
        if defer and driving():
            raise Deferred('driver on track')
        recordings, sources, laps, started = [], [], 0, time.time()
        for path in paths:
            if defer and driving():
                raise Deferred('driver on track')
            stamp = _stamp(path)
            window = recording_window(path)
            result, metadata, handling = analyze(path)
            ref = recording_ref(path, stamp, driving if defer else lambda: False)
            if ref is None or window is None:
                selection['changed_during_export'] = selection.get('changed_during_export', 0) + 1
                continue
            summary = drive_summary.compact(result, metadata, handling)
            summary.update(source_ref=ref, source_coverage=_coverage(run, window))
            recordings.append(scrub(summary))
            sources.append((path, recordings[-1], stamp))
            laps += len(summary['laps'])
        if not recordings:
            return Extra('drive_summary', info={'reason': 'recording_changed', 'selection': selection})
        data = drive_summary.pack(recordings, app_version)
        def supplement():
            import handling_traces
            ordinal = 0
            for recording_index, (path, summary, stamp) in enumerate(sources):
                if defer and driving():
                    raise Deferred('driver on track')
                if _stamp(path) != stamp:
                    continue
                for document in traces(path, summary, run['run_id']):
                    if defer and driving():
                        raise Deferred('driver on track')
                    if _stamp(path) != stamp:
                        break
                    document = scrub({**document, 'recording_index': recording_index,
                                      'source_ref': summary['source_ref'], 'source_coverage': summary['source_coverage']})
                    yield Extra('handling_traces',
                                {f'handling-traces/{ordinal:03}.json.xz': handling_traces.pack(document)},
                                {'passes': len(document['passes']),
                                 'samples': sum(p['samples'] for p in document['passes'])})
                    ordinal += 1

        return Extra('drive_summary', {'drive_summary.json.xz': data},
                     {'recordings': len(recordings), 'laps': laps, 'seconds_to_make': round(time.time() - started, 1),
                      'selection': selection},
                     supplement=supplement if traces is not None else None)
    return provider
