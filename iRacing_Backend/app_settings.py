"""Where this installation keeps its data, and the settings entered on the screen.

Everything personal lives in one data folder: the databases, the cached voice clips,
the Google key and the settings file. The code itself names no drive, account or project.

The data folder is, in this order: IRACING_DATA_DIR, the folder of a database
named by IRACING_TUNER_DB_PATH, the folder named in data-location.txt next to
the project (for an installation that keeps its data elsewhere), or data/ inside
the project.

apply_environment() turns the folder and the saved settings into the environment
variables the rest of the app already reads. Variables that are already set win,
so tests and one-off scripts can still point somewhere else.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = Path(__file__).resolve().parent
LOCATION_FILE = ROOT / 'data-location.txt'
SETTINGS_NAME, KEY_NAME, TOKEN_NAME = 'settings.json', 'gcp-key.json', 'garage61_api_token.txt'
# What the screen may change. Secrets (the key, the token) are files of their own and are never read back.
DEFAULTS = {'telemetry_dirs': [], 'gcp_project_id': '', 'gcp_location': 'global', 'speech_region': 'us'}


def data_dir():
    configured = os.getenv('IRACING_DATA_DIR', '').strip()
    if configured:
        return Path(configured).expanduser()
    # A database placed somewhere on purpose (tests, one-off checks) takes its whole folder with it.
    database = os.getenv('IRACING_TUNER_DB_PATH', '').strip() or os.getenv('IRACING_MEMORY_DB_PATH', '').strip()
    if database:
        return Path(database).expanduser().parent
    try:
        named = LOCATION_FILE.read_text(encoding='utf-8-sig').strip().splitlines()
    except OSError:
        named = []
    if named and named[0].strip():
        return Path(named[0].strip()).expanduser()
    return ROOT / 'data'


def settings_path():
    return data_dir() / SETTINGS_NAME


def load():
    try:
        saved = json.loads(settings_path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        saved = {}
    result = dict(DEFAULTS)
    if isinstance(saved, dict):
        folders = saved.get('telemetry_dirs')
        if isinstance(folders, list):
            result['telemetry_dirs'] = [str(item).strip() for item in folders if isinstance(item, str) and item.strip()][:12]
        for name in ('gcp_project_id', 'gcp_location', 'speech_region'):
            if isinstance(saved.get(name), str) and saved[name].strip():
                result[name] = saved[name].strip()[:120]
    return result


def save(values):
    """Store the settings the screen sent; unknown names are dropped. Returns what was stored."""
    current = load()
    for name in DEFAULTS:
        if name in values and values[name] is not None:
            current[name] = values[name]
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (SETTINGS_NAME + '.tmp')
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(folder / SETTINGS_NAME)
    return load()


def key_path():
    """The Google service account key: in the data folder, or where older installations kept it."""
    stored = data_dir() / KEY_NAME
    legacy = BACKEND / KEY_NAME
    return stored if stored.exists() or not legacy.exists() else legacy


def key_summary():
    """What may be shown about the key: that it is there, and whose it is. Never the key itself."""
    path = key_path()
    try:
        key = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'present': False, 'project_id': None, 'account': None}
    return {'present': True, 'project_id': key.get('project_id'), 'account': key.get('client_email')}


def store_key(text):
    """Check that this is a service account key and keep it in the data folder. Returns key_summary()."""
    try:
        key = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError('JSON 형식의 서비스 계정 키 파일이 아닙니다.') from exc
    if (not isinstance(key, dict) or key.get('type') != 'service_account'
            or not all(isinstance(key.get(name), str) and key[name] for name in ('project_id', 'private_key', 'client_email'))):
        raise ValueError('Google Cloud 서비스 계정 키 파일이 아닙니다. 콘솔에서 받은 JSON 키 파일을 선택하세요.')
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / (KEY_NAME + '.tmp')
    temporary.write_text(json.dumps(key), encoding='utf-8')
    temporary.replace(folder / KEY_NAME)
    return key_summary()


def remove_key():
    for path in (data_dir() / KEY_NAME,):
        try:
            path.unlink()
        except OSError:
            pass
    return key_summary()


def token_path():
    return data_dir() / TOKEN_NAME


def spotter_audio_dir():
    stored, legacy = data_dir() / 'spotter_audio', BACKEND / 'spotter_audio'
    return stored if stored.exists() or not legacy.exists() else legacy


UPLOAD_FILE = ROOT / 'log-upload-url.txt'
UPLOAD_PREFIX = 'https://script.google.com/macros/s/'


def log_upload_url():
    """Where "send" delivers a session log: the maker's own receiving script, named in log-upload-url.txt.

    Only a Google Apps Script web app address is accepted, so a changed file cannot point the
    button at any other server. Empty when sending is not set up; the file can still be saved.
    """
    configured = os.getenv('IRACING_LOG_UPLOAD_URL', '').strip()
    if not configured:
        try:
            lines = UPLOAD_FILE.read_text(encoding='utf-8-sig').splitlines()
        except OSError:
            lines = []
        configured = next((line.strip() for line in lines if line.strip() and not line.lstrip().startswith('#')), '')
    return configured if configured.startswith(UPLOAD_PREFIX) and configured.endswith('/exec') and len(configured) < 300 else ''


def project_id(settings=None):
    """The Google Cloud project: what was entered, or the project the key belongs to."""
    return (settings or load())['gcp_project_id'] or key_summary()['project_id'] or ''


def apply_environment(force=False):
    """Set the variables the app reads from the data folder and the saved settings.

    force=True is for a change made on the screen while the app runs: the saved
    settings then replace what an earlier call put there.
    """
    settings, folder = load(), data_dir()

    def put(name, value, replace=False):
        if value and (replace or not os.getenv(name, '').strip()):
            os.environ[name] = str(value)
    put('IRACING_TUNER_DB_PATH', folder / 'iracing_tuner.sqlite3')
    put('IRACING_SPOTTER_AUDIO_DIR', spotter_audio_dir())
    key = key_path()
    if key.exists():
        put('GOOGLE_APPLICATION_CREDENTIALS', key, force)
    elif force:
        os.environ.pop('GOOGLE_APPLICATION_CREDENTIALS', None)
    put('GCP_PROJECT_ID', project_id(settings), force)
    put('GCP_LOCATION', settings['gcp_location'], force)
    put('IRACING_SPEECH_REGION', settings['speech_region'], force)
    if settings['telemetry_dirs']:
        put('IRACING_TELEMETRY_DIRS', os.pathsep.join(settings['telemetry_dirs']), force)
    elif force:
        os.environ.pop('IRACING_TELEMETRY_DIRS', None)
    return settings
