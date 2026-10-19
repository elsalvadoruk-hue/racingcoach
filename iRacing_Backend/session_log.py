"""One driven session as a file a driver can send to whoever improves the app.

The file is a zip of plain JSON, readable without the app:

    manifest.json   what this is: app version, track, car, when, how many of what
    radio.jsonl     every call's life (started, completed, interrupted, expired, cancelled) with its reason
    reports.jsonl   the engineer's reports and the measured pit wall state behind each one

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

FORMAT, FORMAT_VERSION = 'iracing-coach-log', 1
NAME_KEYS = {'name', 'user_name', 'driver_name', 'team_name', 'abbrev_name', 'initials'}
PRIVATE_KEYS = {'session_id', 'subsession_id', 'recent'}
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
            result.append({'run_id': run['run_id'], 'started_at': run['started'], 'ended_at': run['ended'],
                           'minutes': round((run['ended'] - run['started']) / 60, 1), 'calls': run['events'],
                           'completed': run['completed'] or 0, 'reports': reports, 'questions': questions,
                           'track': identity.get('track_name'), 'track_path': identity.get('track_path'),
                           'car': identity.get('car_name'), 'session_type': session_type})
    return result


def export(store, run_id, app_version, include_questions=False):
    """(file name, zip bytes) for one session, or None when that session left nothing."""
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
    by_key = {}
    for event in radio:
        entry = by_key.setdefault(event.get('key') or '?', {})
        label = event.get('status', '?') + (':' + event['reason'] if event.get('reason') else '')
        entry[label] = entry.get(label, 0) + 1
    manifest = {
        'format': FORMAT, 'format_version': FORMAT_VERSION, 'app_version': app_version, 'exported_at': time.time(),
        'run_id': run_id, 'started_at': started, 'ended_at': ended, 'minutes': round((ended - started) / 60, 1),
        'track': identity.get('track_name'), 'track_path': identity.get('track_path'), 'track_config': identity.get('track_config'),
        'car': identity.get('car_name'), 'car_path': identity.get('car_path'), 'session_type': session_type,
        'radio_events': len(radio), 'reports': sum(1 for r in reports if r['source'] == 'AUTO_REPORT'),
        'questions_in_session': questions, 'questions_included': bool(include_questions),
        'names_replaced': True, 'calls': by_key,
        'files': {'radio.jsonl': 'one line for each change in a call: started, completed, interrupted, expired, cancelled',
                  'reports.jsonl': 'engineer reports with the measured state behind them'},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('radio.jsonl', '\n'.join(json.dumps(item, ensure_ascii=False) for item in radio))
        archive.writestr('reports.jsonl', '\n'.join(json.dumps(item, ensure_ascii=False) for item in reports))
    stamp = time.strftime('%Y%m%d-%H%M', time.localtime(started))
    place = re.sub(r'[^A-Za-z0-9]+', '-', identity.get('track_path') or 'session').strip('-') or 'session'
    return f'coach-log-{place}-{stamp}.zip', buffer.getvalue()


UPLOAD_FORMAT, UPLOAD_LIMIT = 'iracing-coach-log-upload', 1_500_000


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
