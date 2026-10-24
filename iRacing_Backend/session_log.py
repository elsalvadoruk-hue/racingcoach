"""One driven session as a file a driver can send to whoever improves the app.

The file is a zip of plain JSON, readable without the app:

    manifest.json   what this is: app version, track, car, when, how many of what
    radio.jsonl     every call's life (started, completed, interrupted, expired, cancelled) with its reason
    reports.jsonl   the engineer's reports and the measured pit wall state behind each one

and, when they exist and the file stays small enough to send (EXTRA_ORDER, manifest "extras"):

    drive_summary.json.xz   the driver's own laps and corner passes as measured values (drive_summary)
    handling-traces/*.json.xz   selected corners across laps, only without nearby-car files and within spare space
    traffic.packed.xz       races: nearby cars ten times a second, positions only (traffic_log, log_pack)
    timing.packed.xz        races: every car's lap position and order once a second (timing_log, log_pack)

It answers "what was said, what was lost and why" for a whole session, which is what the
improvements so far were found from. It holds no audio, no telemetry file, no key or token.
Names of drivers are replaced by "Driver <number>". What the driver asked by microphone,
and the answers, are left out unless he chooses to include them.
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
import logging
from itertools import islice

logger = logging.getLogger(__name__)

FORMAT, FORMAT_VERSION = 'iracing-coach-log', 1
NAME_KEYS = {'name', 'user_name', 'driver_name', 'team_name', 'abbrev_name', 'initials'}
# Ratings, with the track and the time, could point at a named driver in public results.
PRIVATE_KEYS = {'session_id', 'subsession_id', 'recent', 'irating', 'license', 'user_id', 'cust_id', 'steam_id'}
PATH = re.compile(r'[A-Za-z]:[\\/]|\\\\|/Users/|/home/')


def scrub(value, idx=None):
    """A copy without names, session numbers and anything that looks like a path on this PC."""
    if isinstance(value, dict):
        number = value.get('idx') if isinstance(value.get('idx'), (int, float)) else idx
        result = {}
        for key, item in value.items():
            if key in PRIVATE_KEYS:
                continue
            if key in NAME_KEYS:
                result[key] = f'Driver {int(number)}' if number is not None else None
            else:
                result[key] = scrub(item, number)
        return result
    if isinstance(value, list):
        return [scrub(item, idx) for item in value]
    if isinstance(value, str) and PATH.search(value):
        return None
    return value


def note_run(context):
    snapshot = context.get('snapshot') if isinstance(context, dict) else None
    return snapshot.get('run_id') if isinstance(snapshot, dict) else None


def _first_session(db, run_id):
    """Game, track and car stored with the run's radio events (when the drive left no report)."""
    for (event_json,) in db.execute('SELECT event_json FROM radio_delivery WHERE run_id=? ORDER BY id', (run_id,)):
        session = json.loads(event_json or '{}').get('session')
        if isinstance(session, dict):
            return session
    return {}


def sessions(store, limit=20):
    """The most recent driven sessions that left a delivery log, newest first."""
    with store.connect() as db:
        runs = db.execute("SELECT run_id, MIN(created_at) AS started, MAX(created_at) AS ended, COUNT(*) AS events, "
                          "SUM(status='completed') AS completed FROM radio_delivery WHERE run_id != '' "
                          "GROUP BY run_id ORDER BY ended DESC LIMIT ?", (limit,)).fetchall()
        result = []
        for run in runs:
            notes = db.execute("SELECT context_json, source FROM voice_notes WHERE created_at BETWEEN ? AND ? ORDER BY created_at",
                               (run['started'] - 120, run['ended'] + 120)).fetchall()
            identity, session_type, reports, questions = {}, None, 0, 0
            for note in notes:
                context = json.loads(note['context_json'] or '{}')
                if note_run(context) != run['run_id']:
                    continue
                identity = context.get('identity') or identity
                session_type = (context.get('pit_wall') or {}).get('session_type') or session_type
                reports += note['source'] == 'AUTO_REPORT'
                questions += note['source'] != 'AUTO_REPORT'
            if not identity:
                identity = _first_session(db, run['run_id'])
                session_type = session_type or identity.get('session_type')
            result.append({'run_id': run['run_id'], 'started_at': run['started'], 'ended_at': run['ended'],
                           'minutes': round((run['ended'] - run['started']) / 60, 1), 'calls': run['events'],
                           'completed': run['completed'] or 0, 'reports': reports, 'questions': questions,
                           'track': identity.get('track_name'), 'track_path': identity.get('track_path'),
                           'car': identity.get('car_name'), 'session_type': session_type})
    return result


class Deferred(Exception):
    """An extra part cannot be made right now (the driver is on track); try the session later."""


@dataclass
class Extra:
    """Files added to a log. Empty files: not included, and why (``info['reason']``)."""
    group: str
    files: dict = field(default_factory=dict)
    info: dict = field(default_factory=dict)
    # Lazy, bounded corner bundles. Called only after the ordinary size rule is resolved.
    supplement: object = None


# Dropped first when the file would not fit: nearby cars, then the driver's own measured values.
EXTRA_ORDER = ('nearby_cars', 'drive_summary')
EXTRA_DESCRIPTIONS = {
    'drive_summary.json.xz': "xz JSON: own laps, every valid lap's corner passes and handling observations (measured values only)",
    'traffic.packed.xz': 'races: nearby cars (within 200 m, 10 Hz), positions/order/class/pit only, no names (log_pack)',
    'timing.packed.xz': "races: every car's lap position and order once a second, no names (log_pack)",
}
MAX_SUPPLEMENT_GROUPS = 12


def _zip(manifest, radio, reports, extras):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('radio.jsonl', '\n'.join(json.dumps(item, ensure_ascii=False) for item in radio))
        archive.writestr('reports.jsonl', '\n'.join(json.dumps(item, ensure_ascii=False) for item in reports))
        for extra in extras:
            for name, data in extra.files.items():
                archive.writestr(name, data, compress_type=zipfile.ZIP_STORED)   # already xz
    return buffer.getvalue()


def _collection_versions(radio):
    """Observed at collection, never guessed from the app exporting an older run."""
    result = {}
    for component in ('frontend', 'backend'):
        values = [event.get(f'{component}_version') for event in radio]
        known = [value for value in values if isinstance(value, str) and value]
        result[component] = sorted(set(known))
        result[f'unknown_{component}_events'] = len(values) - len(known)
    return result


def export(store, run_id, app_version, include_questions=False, extras=(), limit=None):
    """(file name, zip bytes) for one session, or None when that session left nothing.

    ``extras`` are callables taking the run (id, times, identity, session type) and returning an
    Extra or None. With ``limit``, extra groups are left out in EXTRA_ORDER until the file fits;
    the manifest says what was left out and why. A Deferred from an extra is passed on.
    """
    with store.connect() as db:
        rows = db.execute('SELECT created_at, event_json FROM radio_delivery WHERE run_id=? ORDER BY id', (run_id,)).fetchall()
        if not rows:
            return None
        started, ended = rows[0]['created_at'], rows[-1]['created_at']
        notes = db.execute('SELECT created_at, text, reply, source, reply_source, service_error, context_json FROM voice_notes '
                           'WHERE created_at BETWEEN ? AND ? ORDER BY created_at', (started - 120, ended + 120)).fetchall()
    radio = [scrub({**json.loads(row['event_json']), 'recorded_at': row['created_at']}) for row in rows]
    if not include_questions:
        # The answer to a microphone question is played like any call; its words stay out with the question.
        radio = [{**event, 'text': None} if event.get('key') == 'answer' else event for event in radio]
    reports, identity, session_type, questions = [], {}, None, 0
    for note in notes:
        context = json.loads(note['context_json'] or '{}')
        if note_run(context) != run_id:
            continue
        identity = context.get('identity') or identity
        session_type = (context.get('pit_wall') or {}).get('session_type') or session_type
        automatic = note['source'] == 'AUTO_REPORT'
        if not automatic:
            questions += 1
            if not include_questions:
                continue
        # A report's text is the app's own sentence; a question is the driver's words.
        reports.append({'recorded_at': note['created_at'], 'source': note['source'], 'text': None if automatic else note['text'],
                        'reply': note['reply'], 'reply_source': note['reply_source'], 'service_error': note['service_error'],
                        'context': scrub(context)})
    if not identity:
        identity = next((event['session'] for event in radio if isinstance(event.get('session'), dict)), {})
        session_type = session_type or identity.get('session_type')
    by_key = {}
    for event in radio:
        entry = by_key.setdefault(event.get('key') or '?', {})
        label = event.get('status', '?') + (':' + event['reason'] if event.get('reason') else '')
        entry[label] = entry.get(label, 0) + 1
    manifest = {
        'format': FORMAT, 'format_version': FORMAT_VERSION, 'app_version': app_version, 'exported_at': time.time(),
        'export_app_version': app_version, 'collection_versions': _collection_versions(radio),
        'run_id': run_id, 'game': identity.get('game'), 'started_at': started, 'ended_at': ended, 'minutes': round((ended - started) / 60, 1),
        'track': identity.get('track_name'), 'track_path': identity.get('track_path'), 'track_config': identity.get('track_config'),
        'car': identity.get('car_name'), 'car_path': identity.get('car_path'), 'session_type': session_type,
        'radio_events': len(radio), 'reports': sum(1 for r in reports if r['source'] == 'AUTO_REPORT'),
        'questions_in_session': questions, 'questions_included': bool(include_questions),
        'names_replaced': True, 'calls': by_key,
        'files': {'radio.jsonl': 'one line for each change in a call: started, completed, interrupted, expired, cancelled',
                  'reports.jsonl': 'engineer reports with the measured state behind them'},
    }
    run = {'run_id': run_id, 'started_at': started, 'ended_at': ended, 'identity': identity,
           'session_type': session_type, 'game': identity.get('game')}
    made = []
    for provider in extras or ():
        try:
            extra = provider(run)
        except Deferred:
            raise
        except Exception:
            logger.exception('A session log extra could not be made; the log is made without it')
            continue
        if extra is not None:
            made.append(extra)
    included = [extra for extra in made if extra.files]
    status = {extra.group: {'included': bool(extra.files), 'reason': extra.info.get('reason'),
                            'bytes': sum(len(data) for data in extra.files.values()),
                            **{k: v for k, v in extra.info.items() if k != 'reason'}} for extra in made}

    def build():
        files = dict(manifest['files'])
        for extra in included:
            for name in extra.files:
                files[name] = EXTRA_DESCRIPTIONS.get(name, extra.group)
        return _zip({**manifest, 'files': files, 'extras': status}, radio, reports, included)

    data = build()
    for group in EXTRA_ORDER:
        if limit is None or len(data) <= limit:
            break
        if any(extra.group == group for extra in included):
            size = len(data)
            included = [extra for extra in included if extra.group != group]
            status[group].update(included=False, reason='size_limit', file_bytes_with=size)
            data = build()
    # A supplement never displaces an existing file. Measure the complete ZIP,
    # including its manifest and directory entries, for every whole-corner bundle.
    summary = next((e for e in included if e.group == 'drive_summary' and e.supplement), None)
    budget = min(limit, SEND_LIMIT) if limit is not None else SEND_LIMIT
    if summary is not None and len(data) < budget:
        nearby = any(e.group == 'nearby_cars' for e in included)
        detail = Extra('handling_traces')
        status[detail.group] = {'included': False, 'reason': 'nearby_cars_included' if nearby else 'no_eligible_corners',
                                'bytes': 0, 'groups': 0, 'passes': 0, 'samples': 0, 'skipped_groups': 0}
        before = data
        metadata_only = build()
        if len(metadata_only) <= budget:
            data = metadata_only
            if not nearby:
                included.append(detail)
                try:
                    for addition in islice(summary.supplement(), MAX_SUPPLEMENT_GROUPS):
                        previous = dict(status[detail.group])
                        detail.files.update(addition.files)
                        status[detail.group] = {
                            'included': True, 'reason': None,
                            'bytes': previous['bytes'] + sum(len(v) for v in addition.files.values()),
                            'groups': previous['groups'] + 1,
                            'passes': previous['passes'] + addition.info['passes'],
                            'samples': previous['samples'] + addition.info['samples'],
                            'skipped_groups': previous['skipped_groups'],
                        }
                        candidate = build()
                        if len(candidate) <= budget:
                            data = candidate
                        else:
                            for name in addition.files:
                                detail.files.pop(name)
                            status[detail.group] = previous
                            status[detail.group]['skipped_groups'] += 1
                            if not detail.files:
                                status[detail.group]['reason'] = 'size_limit'
                    final = build()
                    if len(final) <= budget:
                        data = final
                except Deferred:
                    raise
                except Exception:
                    # Failure of optional detail must not lose the base log.
                    logger.exception('Handling trace supplement could not be made')
        else:
            status.pop(detail.group)
            data = before
    if limit is not None and len(data) > limit:
        raise ValueError('기본 로그만으로 보낼 수 있는 크기를 넘었습니다. 로그 파일 받기로 저장해 주세요.')
    stamp = time.strftime('%Y%m%d-%H%M', time.localtime(started))
    place = re.sub(r'[^A-Za-z0-9]+', '-', identity.get('track_path') or 'session').strip('-') or 'session'
    return f'coach-log-{place}-{stamp}.zip', data


UPLOAD_FORMAT, UPLOAD_LIMIT = 'iracing-coach-log-upload', 1_500_000
# Extras must leave room under the receiving script's limit (Code.gs MAX_BYTES).
SEND_LIMIT = 1_400_000


def send(url, name, data, note='', app_version=''):
    """Deliver one log file to the receiving script. Raises ValueError with a message for the driver when it did not arrive."""
    if not url:
        raise ValueError('보내기가 설정되지 않았습니다. 로그 파일을 받아서 직접 보내 주세요.')
    if len(data) > UPLOAD_LIMIT:
        raise ValueError('로그 파일이 너무 큽니다. 파일을 받아서 직접 보내 주세요.')
    body = json.dumps({'format': UPLOAD_FORMAT, 'name': name, 'note': (note or '').strip()[:500], 'app_version': app_version,
                       'data': base64.b64encode(data).decode('ascii')}).encode('utf-8')
    request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        # The script answers through a redirect, which is followed.
        with urllib.request.urlopen(request, timeout=30) as response:
            reply = json.loads(response.read(20000).decode('utf-8', errors='replace'))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ValueError('로그를 보내지 못했습니다. 인터넷 연결을 확인하거나 파일을 받아서 직접 보내 주세요.') from exc
    if not isinstance(reply, dict) or reply.get('ok') is not True:
        reason = reply.get('error') if isinstance(reply, dict) and isinstance(reply.get('error'), str) else ''
        raise ValueError('받는 쪽에서 로그를 받지 않았습니다.' + (f' ({reason[:80]})' if reason else ''))
    return True


def read_extras(data):
    """The extra parts of a log file, unpacked: {'drive_summary': {...}, 'traffic': (header, rows), ...}."""
    import drive_summary
    import log_pack
    parts = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        if 'drive_summary.json.xz' in names:
            parts['drive_summary'] = drive_summary.unpack(archive.read('drive_summary.json.xz'))
        trace_names = sorted(n for n in names if re.fullmatch(r'handling-traces/[0-9]+\.json\.xz', n))
        if trace_names:
            import handling_traces
            parts['handling_traces'] = [handling_traces.unpack(archive.read(n)) for n in trace_names]
        for key in ('traffic', 'timing'):
            if f'{key}.packed.xz' in names:
                parts[key] = log_pack.unpack(archive.read(f'{key}.packed.xz'))
    return parts


def read(data):
    """The three parts of a log file from its bytes. Raises ValueError when it is not one."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            lines = lambda name: [json.loads(line) for line in archive.read(name).decode('utf-8').splitlines() if line.strip()]
            if manifest.get('format') != FORMAT:
                raise ValueError('not a coach log')
            return manifest, lines('radio.jsonl'), lines('reports.jsonl')
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ValueError('iRacing 주행 코치의 로그 파일이 아닙니다.') from exc
