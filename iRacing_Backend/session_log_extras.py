"""The extra parts of a session log: nearby cars (races) and the driver's own measured drive.

Kept apart from backend_api so the rules can be tested without the app: what the app knows
(where the race logs are, how to find and analyse a recording, whether the driver is on track)
is passed in. Both parts are made only from files already on this computer; nothing is
recorded for them, and they follow session_log's size rule (session_log.EXTRA_ORDER).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import time

import drive_summary
import log_pack
from session_log import Deferred, Extra, scrub

MAX_RECORDINGS = 3
MAX_RECORDING_SECONDS = 7 * 86400


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
        if not is_race(run):
            return Extra('nearby_cars', info={'reason': 'not_race'})
        if prepare is not None:
            prepare(run['run_id'])
        files, info = {}, {}
        for key, folder, pattern in (('traffic', traffic_folder, 'traffic-*.jsonl.gz'),
                                     ('timing', timing_folder, 'timing-*.jsonl.gz')):
            path = find_log(folder, pattern, run['run_id'], run['started_at'])
            if path is None:
                continue
            header, rows = log_pack.read_lines(path)
            # The logs hold no names; scrub anyway in case a header ever gains a path.
            files[f'{key}.packed.xz'] = log_pack.pack(scrub(header), rows)
            info[key] = {'rows': len(rows), 'seconds': round(rows[-1]['t'] - rows[0]['t'], 1) if len(rows) > 1 else 0.,
                         'raw_bytes': path.stat().st_size}
        if not files:
            return Extra('nearby_cars', info={'reason': 'no_log'})
        return Extra('nearby_cars', files, info)
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


def recordings_for(run, candidates, metadata_of):
    """Same car/track/session kind and recorded time overlapping this run's calls.

    A recording may begin before the first call, finish after the last, or be one
    of several files from the run. Its save time cannot attach an adjacent run.
    """
    found = []
    for path in candidates:
        window = recording_window(path)
        if window is None or window[0] > run['ended_at'] or window[1] < run['started_at']:
            continue
        try:
            metadata = metadata_of(path) or {}
        except (OSError, ValueError):
            continue
        if not matches(run.get('identity'), metadata):
            continue
        want, have = _session_kind(run.get('session_type')), _session_kind(metadata.get('session_type'))
        if want and have and want != have:
            continue
        found.append((window[0], Path(path)))
    found.sort(key=lambda item: item[0])
    return [path for _, path in found[-MAX_RECORDINGS:]]


def own_drive(candidates, metadata_of, analyze, driving=lambda: False, defer=True, app_version='', traces=None):
    """Extra provider: the driver's own recordings of the run as measured values.

    ``analyze(path)`` returns (analysis result, metadata, handling with observations). It is
    heavy, so with ``defer`` it waits (Deferred) while the driver is on track.
    """
    def provider(run):
        paths = recordings_for(run, candidates(), metadata_of)
        if not paths:
            return Extra('drive_summary', info={'reason': 'no_recording'})
        if defer and driving():
            raise Deferred('driver on track')
        recordings, sources, laps, started = [], [], 0, time.time()
        for path in paths:
            if defer and driving():
                raise Deferred('driver on track')
            stamp = (path.stat().st_size, path.stat().st_mtime_ns)
            result, metadata, handling = analyze(path)
            summary = drive_summary.compact(result, metadata, handling)
            recordings.append(scrub(summary))
            sources.append((path, recordings[-1], stamp))
            laps += len(summary['laps'])
        if not recordings:
            return Extra('drive_summary', info={'reason': 'no_recording'})
        data = drive_summary.pack(recordings, app_version)
        def supplement():
            import handling_traces
            ordinal = 0
            for recording_index, (path, summary, stamp) in enumerate(sources):
                if defer and driving():
                    raise Deferred('driver on track')
                if (path.stat().st_size, path.stat().st_mtime_ns) != stamp:
                    continue
                for document in traces(path, summary, run['run_id']):
                    if defer and driving():
                        raise Deferred('driver on track')
                    document = scrub({**document, 'recording_index': recording_index})
                    yield Extra('handling_traces',
                                {f'handling-traces/{ordinal:03}.json.xz': handling_traces.pack(document)},
                                {'passes': len(document['passes']),
                                 'samples': sum(p['samples'] for p in document['passes'])})
                    ordinal += 1

        return Extra('drive_summary', {'drive_summary.json.xz': data},
                     {'recordings': len(recordings), 'laps': laps, 'seconds_to_make': round(time.time() - started, 1)},
                     supplement=supplement if traces is not None else None)
    return provider
