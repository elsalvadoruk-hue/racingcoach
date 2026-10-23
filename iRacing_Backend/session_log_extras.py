"""The extra parts of a session log: nearby cars (races) and the driver's own measured drive.

Kept apart from backend_api so the rules can be tested without the app: what the app knows
(where the race logs are, how to find and analyse a recording, whether the driver is on track)
is passed in. Both parts are made only from files already on this computer; nothing is
recorded for them, and they follow session_log's size rule (session_log.EXTRA_ORDER).
"""
from __future__ import annotations

import json
from pathlib import Path
import time

import drive_summary
import log_pack
from session_log import Deferred, Extra, scrub

MAX_RECORDINGS = 3
RECORDING_AFTER_S = 1800     # a recording is closed when the driver leaves the car, after the last call


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


def nearby_cars(traffic_folder, timing_folder):
    """Extra provider: the run's traffic and timing logs, packed without loss."""
    def provider(run):
        if not is_race(run):
            return Extra('nearby_cars', info={'reason': 'not_race'})
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
    """A recording belongs to the run when car and track agree wherever both are known."""
    for key in ('car_path', 'track_path'):
        want, have = (identity or {}).get(key), (metadata or {}).get(key)
        if want and have and str(want).casefold() != str(have).casefold():
            return False
    return True


def recordings_for(run, candidates, metadata_of):
    """Recordings written during the run: closed between its start and RECORDING_AFTER_S after it."""
    found = []
    for path in candidates:
        try:
            closed = Path(path).stat().st_mtime
        except OSError:
            continue
        if not run['started_at'] - 60 <= closed <= run['ended_at'] + RECORDING_AFTER_S:
            continue
        if matches(run.get('identity'), metadata_of(path)):
            found.append(Path(path))
    found.sort(key=lambda p: p.stat().st_mtime)
    return found[-MAX_RECORDINGS:]


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
