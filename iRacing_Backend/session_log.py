"""One driven session as a file a driver can send to whoever improves the app.

The file is a zip of plain JSON, readable without the app:

    manifest.json   what this is: app version, track, car, when, how many of what
    radio.jsonl     every call's life (started, completed, interrupted, expired, cancelled) with its reason
    reports.jsonl   the engineer's reports and the measured pit wall state behind each one
    session-health.json  bounded backend connection samples, also for drives with no voice

and, when they exist and the file stays small enough to send (EXTRA_ORDER, manifest "extras"):

    drive_summary.json.xz   the driver's own laps and corner passes as measured values (drive_summary)
    handling-traces/*.json.xz   selected whole corners across laps, within spare space after other files
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


def _health_rows(db, run_id=None, limit=20):
    # Read old databases without upgrading or writing them from the exporter.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_health'").fetchone():
        return []
    return db.execute('SELECT * FROM session_health ' + ('WHERE run_id=? ' if run_id else '')
                      + 'ORDER BY updated_at DESC LIMIT ?', (run_id, limit) if run_id else (limit,)).fetchall()


def sessions(store, limit=20):
    """Recent driven sessions, including independently observed silent drives."""
    with store.connect() as db:
        runs = db.execute("SELECT run_id, MIN(created_at) AS started, MAX(created_at) AS ended, COUNT(*) AS events, "
                          "SUM(status='completed') AS completed FROM radio_delivery WHERE run_id != '' "
                          "GROUP BY run_id ORDER BY ended DESC LIMIT ?", (limit,)).fetchall()
        merged = {row['run_id']: dict(row) for row in runs}
        identities = {}
        driven = set()
        from session_health import MAX_RUNS
        for health in _health_rows(db, limit=max(limit, MAX_RUNS)):
            key = health['run_id']
            identities[key] = json.loads(health['identity_json'])
            if any(sample.get('on_track') is True and sample.get('is_replay') is False
                   for sample in json.loads(health['health_json']).get('samples', [])):
                driven.add(key)
            row = merged.setdefault(key, {'run_id': key, 'started': health['started_at'],
                                          'ended': health['updated_at'], 'events': 0, 'completed': 0})
            row['started'] = min(row['started'], health['started_at'])
            row['ended'] = max(row['ended'], health['updated_at'])
        runs = sorted(merged.values(), key=lambda row: row['ended'], reverse=True)[:limit]
        result = []
        for run in runs:
            notes = db.execute("SELECT context_json, source FROM voice_notes WHERE created_at BETWEEN ? AND ? ORDER BY created_at",
                               (run['started'] - 120, run['ended'] + 120)).fetchall()
            observed = identities.get(run['run_id'], {})
            identity = observed
            session_type, reports, questions = identity.get('session_type'), 0, 0
            for note in notes:
                context = json.loads(note['context_json'] or '{}')
                if note_run(context) != run['run_id']:
                    continue
                identity = context.get('identity') or identity
                session_type = (context.get('pit_wall') or {}).get('session_type') or session_type
                reports += note['source'] == 'AUTO_REPORT'
                questions += note['source'] != 'AUTO_REPORT'
            # A report may predate the live track metadata. Keep the independently
            # observed identity instead of losing it to a sparse report snapshot.
            identity = {**identity, **observed}
            session_type = identity.get('session_type') or session_type
            if not identity:
                identity = _first_session(db, run['run_id'])
                session_type = session_type or identity.get('session_type')
            from oval_spotter import classify
            profile = classify(identity)[0] if identity.get('game') == 'iracing' else None
            result.append({'run_id': run['run_id'], 'started_at': run['started'], 'ended_at': run['ended'],
                           'minutes': round((run['ended'] - run['started']) / 60, 1), 'calls': run['events'],
                           'completed': run['completed'] or 0, 'reports': reports, 'questions': questions,
                           'track': identity.get('track_name'), 'track_path': identity.get('track_path'),
                           'car': identity.get('car_name'), 'session_type': session_type,
                           'game': identity.get('game'), 'spotter_profile': profile,
                           'has_driving_sample': run['run_id'] in driven})
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
    # Optional smaller representation, made only when the full group cannot fit.
    fallback: object = None


# Optional health samples go first, then nearby cars and the driver's own summary.
EXTRA_ORDER = ('session_health', 'nearby_cars', 'drive_summary')
# Oval validation prioritises traffic and the independent health record over
# corner analysis. Other games/profiles keep their existing order.
OVAL_EXTRA_ORDER = ('drive_summary', 'nearby_cars', 'session_health')
EXTRA_DESCRIPTIONS = {
    'banter-shadow.json': 'shadow-only banter evaluations and virtual phrase choices; no actual playback',
    'oval-procedures.json': 'shadow-only oval procedure signals, candidates, cancellations and client observations; no actual playback',
    'session-health.json': 'bounded backend connection samples; not proof of playback or exact disconnect timing',
    'drive_summary.json.xz': "xz JSON: own laps, every valid lap's corner passes and handling observations (measured values only)",
    'traffic.packed.xz': 'races and confirmed asphalt-oval practice: nearby cars (within 200 m, up to 10 Hz), no names; oval pacing signals; see export_selection for omitted ranges (log_pack)',
    'timing.packed.xz': "races: every car's lap position and order once a second, no names (log_pack)",
}
MAX_SUPPLEMENT_GROUPS = 12
MAX_TRAFFIC_SUPPLEMENT_GROUPS = 3   # race diagnostics add bounded work and never displace traffic


def _zip(manifest, radio, reports, extras):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('radio.jsonl', '\n'.join(json.dumps(item, ensure_ascii=False) for item in radio))
        archive.writestr('reports.jsonl', '\n'.join(json.dumps(item, ensure_ascii=False) for item in reports))
        for extra in extras:
            for name, data in extra.files.items():
                archive.writestr(name, data, compress_type=(zipfile.ZIP_DEFLATED if name == 'session-health.json' else zipfile.ZIP_STORED))
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
        health_rows = _health_rows(db, run_id, 1)
        health = health_rows[0] if health_rows else None
        banter = [scrub(json.loads(row[0])) for row in db.execute(
            'SELECT record_json FROM banter_reviews WHERE run_id=? ORDER BY id', (run_id,))]
        procedures = [scrub(json.loads(row[0])) for row in db.execute(
            'SELECT record_json FROM oval_procedure_reviews WHERE run_id=? ORDER BY id', (run_id,))]
        if not rows and health is None:
            return None
        started = min(([rows[0]['created_at']] if rows else []) + ([health['started_at']] if health else []))
        ended = max(([rows[-1]['created_at']] if rows else []) + ([health['updated_at']] if health else []))
        notes = db.execute('SELECT created_at, text, reply, source, reply_source, service_error, context_json FROM voice_notes '
                           'WHERE created_at BETWEEN ? AND ? ORDER BY created_at', (started - 120, ended + 120)).fetchall()
    radio = [scrub({**json.loads(row['event_json']), 'recorded_at': row['created_at']}) for row in rows]
    if not include_questions:
        # The answer to a microphone question is played like any call; its words stay out with the question.
        radio = [{**event, 'text': None} if event.get('key') == 'answer' else event for event in radio]
    observed = json.loads(health['identity_json']) if health else {}
    identity = observed
    reports, session_type, questions = [], identity.get('session_type'), 0
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
    identity = {**identity, **observed}
    session_type = identity.get('session_type') or session_type
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
    if identity.get('game') == 'iracing':
        from oval_spotter import classify, POLICY_VERSION
        profile, reason = classify(identity)
        manifest['spotter'] = scrub({'profile': profile, 'reason': reason, 'policy_version': POLICY_VERSION,
                               **{key: identity.get(key) for key in ('track_id', 'track_type', 'track_direction',
                                   'course_cautions', 'restarts', 'standing_start')}})
    run = {'run_id': run_id, 'started_at': started, 'ended_at': ended, 'identity': identity,
           'session_type': session_type, 'game': identity.get('game'),
           # Selection needs clocks/status only; no question text crosses this boundary.
           'radio_selection': [{key: event.get(key) for key in ('session_time', 'status')} for event in radio]}
    made = []
    if health is not None:
        document = scrub({**json.loads(health['health_json']), 'identity': json.loads(health['identity_json'])})
        made.append(Extra('session_health', {'session-health.json': json.dumps(document, ensure_ascii=False).encode('utf-8')},
                          {'samples': len(document['samples']), 'dropped_samples': document['dropped_samples']}))
    if banter:
        selected_banter = banter if len(banter) <= 128 else banter[:8] + banter[-120:]
        made.append(Extra('banter_shadow', {'banter-shadow.json': json.dumps(selected_banter, ensure_ascii=False).encode('utf-8')}))
    if procedures:
        selected_procedures = procedures if len(procedures) <= 256 else procedures[:16] + procedures[-240:]
        made.append(Extra('oval_procedures', {'oval-procedures.json': json.dumps(selected_procedures, ensure_ascii=False).encode('utf-8')}))
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
        if procedures:
            count = len(selected_procedures) if any(extra.group == 'oval_procedures' for extra in included) else 0
            manifest['oval_procedures'] = {'mode':'SHADOW', 'observations':len(procedures),
                'included_observations':count, 'omitted_observations':len(procedures)-count,
                'retention':'LATEST_4096_GLOBAL', 'queue_dropped_global':max(r.get('queue_dropped',0) for r in procedures),
                'actual_playback':False, 'complete_history':False}
        if banter:
            count = len(selected_banter) if any(extra.group == 'banter_shadow' for extra in included) else 0
            manifest['banter_shadow'] = {'mode': 'SHADOW', 'observations': len(banter),
                'included_observations': count, 'omitted_observations': len(banter) - count,
                'retention': 'LATEST_2048_GLOBAL', 'eligible': sum(r.get('status') == 'ELIGIBLE_SHADOW' for r in banter),
                'actual_playback': False}
        for extra in included:
            for name in extra.files:
                files[name] = EXTRA_DESCRIPTIONS.get(name, extra.group)
        return _zip({**manifest, 'files': files, 'extras': status}, radio, reports, included)

    data = build()
    order = ('banter_shadow', 'oval_procedures') + (OVAL_EXTRA_ORDER if (manifest.get('spotter') or {}).get('profile') == 'ASPHALT_OVAL' else EXTRA_ORDER)
    for group in order:
        if limit is None or len(data) <= limit:
            break
        if any(extra.group == group for extra in included):
            for index, extra in enumerate(included):
                if extra.group != group or not callable(extra.fallback):
                    continue
                try:
                    smaller = extra.fallback()
                except Exception:
                    logger.exception('A reduced session log extra could not be made')
                    smaller = None
                if smaller is not None and smaller.group == group and smaller.files:
                    included[index] = smaller
                    status[group] = {'included': True, 'reason': None,
                                     'bytes': sum(len(part) for part in smaller.files.values()), **smaller.info}
            data = build()
            if len(data) <= limit:
                break
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
        group_limit = MAX_TRAFFIC_SUPPLEMENT_GROUPS if nearby else MAX_SUPPLEMENT_GROUPS
        detail = Extra('handling_traces')
        status[detail.group] = {'included': False, 'reason': 'no_eligible_corners',
                                'bytes': 0, 'groups': 0, 'passes': 0, 'samples': 0, 'skipped_groups': 0}
        before = data
        metadata_only = build()
        if len(metadata_only) <= budget:
            data = metadata_only
            included.append(detail)
            try:
                for addition in islice(summary.supplement(), group_limit):
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
        if 'session-health.json' in names:
            parts['session_health'] = json.loads(archive.read('session-health.json'))
        if 'banter-shadow.json' in names:
            parts['banter_shadow'] = json.loads(archive.read('banter-shadow.json'))
        if 'oval-procedures.json' in names:
            parts['oval_procedures'] = json.loads(archive.read('oval-procedures.json'))
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
