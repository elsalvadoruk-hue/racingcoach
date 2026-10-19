"""Check staged files or all reachable Git history without printing secret values."""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {'data', 'node_modules', '__pycache__', '.venv', 'backups', 'logs',
                   'artifacts', 'verification', 'share', 'tools', 'ui-lab', '.claude', '.vscode'}
FORBIDDEN_SUFFIXES = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.ibt', '.sqlite', '.sqlite3',
                      '.db', '.pem', '.p12', '.pfx', '.key', '.zip', '.log', '.pyc'}
FORBIDDEN_NAMES = {'gcp-key.json', 'garage61_api_token.txt', 'settings.json',
                   'data-location.txt', 'data-location.txt.old', 'log-upload-url.txt',
                   'credentials.json', 'token.json', 'id_rsa', 'id_ed25519'}
PATTERNS = {
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----'),
    'Google API key': re.compile(rb'AIza[0-9A-Za-z_-]{35}'),
    'GitHub token': re.compile(rb'(?:gh[pousr]_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{30,})'),
    'AWS access key': re.compile(rb'(?:AKIA|ASIA)[A-Z0-9]{16}'),
    'Slack token': re.compile(rb'xox[baprs]-[0-9A-Za-z-]{20,}'),
    'credential in URL': re.compile(rb'https?://[^\s/@:]+:[^\s/@]+@'),
    'service-account document': re.compile(rb'"type"\s*:\s*"service_account"'),
    'private Windows profile': re.compile(rb'[A-Za-z]:[\\/]+Users[\\/]+(?!Driver\b|Public\b|Default\b|User\b|YourName\b|someone\b|me\b|tester\b|test\b|<)[^\s\\/"\']+', re.I),
}
TEXT_SUFFIXES = {'.py', '.js', '.jsx', '.ts', '.tsx', '.json', '.md', '.txt', '.html', '.css',
                 '.svg', '.yml', '.yaml', '.bat', '.ps1', '.toml', '.ini', '.cfg', '.gs'}


def forbidden_path(name: str) -> bool:
    path = Path(name)
    lowered = name.replace('\\', '/').lower().split('/')
    return ((lowered[0] in FORBIDDEN_PARTS)
            or any(part in {'node_modules', '__pycache__', '.venv'} for part in lowered)
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or path.name.lower() in FORBIDDEN_NAMES
            or (path.name.startswith('.env') and path.name != '.env.development'))


def findings(name: str, content: bytes, markers: list[bytes] = ()) -> list[str]:
    problems = ['forbidden path'] if forbidden_path(name) else []
    # Compare exact local identifiers in every format, including uncompressed binary metadata.
    lower = content.lower()
    if any(marker.lower() in lower for marker in markers if len(marker) >= 4):
        problems.append('local private identifier')
    if Path(name).suffix.lower() in TEXT_SUFFIXES or Path(name).name.startswith('.'):
        # Raster data in official SVG maps can randomly contain token-shaped base64 substrings.
        # Scan their decoded metadata for exact private markers, and scan the surrounding SVG text.
        def image(match):
            try:
                decoded = base64.b64decode(match[1])
                if any(marker.lower() in decoded.lower() for marker in markers if len(marker) >= 4):
                    problems.append('local private identifier in embedded image')
            except ValueError:
                problems.append('invalid embedded image')
            return b'data:image/verified;base64,OMITTED'
        text = re.sub(rb'data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)', image, content)
        # This exact six-letter placeholder is the deliberately invalid key in the settings tests.
        placeholder = b'-----BEGIN ' + b'PRIVATE KEY-----\\nSECRET\\n-----END ' + b'PRIVATE KEY-----'
        text = text.replace(placeholder, b'SYNTHETIC_TEST_KEY')
        problems.extend(label for label, pattern in PATTERNS.items()
                        if (label != 'service-account document' or Path(name).suffix == '.json') and pattern.search(text))
    return problems


def local_markers(root: Path = ROOT) -> list[bytes]:
    # Read comparison values only into memory. Never print them or write them to a report.
    values = [Path.home().name, getpass.getuser(), str(root)]
    data = Path(os.environ.get('IRACING_DATA_DIR') or root / 'data')
    try:
        location = (root / 'data-location.txt').read_text(encoding='utf-8-sig').strip().splitlines()
        if location and not os.environ.get('IRACING_DATA_DIR'):
            data = Path(location[0])
    except OSError:
        pass
    values.append(str(data))
    keys = [root / 'iRacing_Backend/gcp-key.json', data / 'gcp-key.json']
    if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        keys.append(Path(os.environ['GOOGLE_APPLICATION_CREDENTIALS']))
    for file in keys:
        try:
            key = json.loads(file.read_text(encoding='utf-8-sig'))
            values.extend(str(key.get(k) or '') for k in ('project_id', 'client_email', 'private_key_id', 'private_key'))
        except (OSError, ValueError):
            pass
    for file in [root / 'iRacing_Backend/garage61_api_token.txt', data / 'garage61_api_token.txt']:
        try:
            values.append(file.read_text(encoding='utf-8-sig').strip())
        except OSError:
            pass
    for name in ('GARAGE61_API_TOKEN', 'GOOGLE_API_KEY', 'GEMINI_API_KEY'):
        values.append(os.environ.get(name, ''))
    return [v.encode('utf-8') for v in set(values) if len(v) >= 4]


def git(*args: str, root: Path = ROOT) -> bytes:
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def audit(root: Path = ROOT, history: bool = False) -> dict:
    markers = local_markers(root)
    if history:
        # Check every unique tree path too: the same blob can appear under a forbidden filename.
        commits = git('rev-list', '--all', root=root).decode().splitlines()
        objects = set()
        for commit in commits:
            for line in git('ls-tree', '-rz', '--full-tree', commit, root=root).split(b'\0'):
                if line:
                    meta, name = line.split(b'\t', 1)
                    objects.add((meta.split()[2].decode(), name.decode('utf-8')))
    else:
        objects = set()
        for line in git('ls-files', '--stage', '-z', root=root).split(b'\0'):
            if line:
                meta, name = line.split(b'\t', 1)
                objects.add((meta.split()[1].decode(), name.decode('utf-8')))
    # One cat-file process keeps checks fast for the many official map assets.
    process = subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    issues, scanned = [], 0
    seen = {}
    try:
        for oid, name in sorted(objects):
            if oid not in seen:
                process.stdin.write((oid+'\n').encode()); process.stdin.flush()
                header = process.stdout.readline().split()
                if len(header) != 3 or header[1] != b'blob':
                    raise RuntimeError('Unexpected Git object')
                content = process.stdout.read(int(header[2])); process.stdout.read(1)
                # Content rules depend on the suffix; cache bytes only for this small batch.
                seen[oid] = content
            for label in findings(name, seen[oid], markers):
                issues.append({'path': name, 'rule': label})
            scanned += 1
    finally:
        process.stdin.close(); process.stdout.close(); process.stderr.close(); process.wait()
    return {'mode': 'history' if history else 'index', 'checked': scanned, 'findings': issues}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    result = audit(history=args.history)
    print(json.dumps(result, ensure_ascii=True))
    return int(bool(result['findings']))


if __name__ == '__main__':
    raise SystemExit(main())
