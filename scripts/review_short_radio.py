"""Local listening/selection tool. Only an explicit save writes the short manifest."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'iRacing_Backend'))
from reviewed_radio_selection import ShortRadioSelection


def render(store, server=False):
    template = (ROOT / 'scripts/short-radio-review.html').read_text(encoding='utf-8')
    analysis_path = store._path('diversity-analysis.json')
    if not analysis_path.exists():
        analysis_path = store._path('diversity-analysis.default.json')
    analysis = json.loads(analysis_path.read_bytes()) if analysis_path.exists() else None
    value = json.dumps({'server': server, 'state': store.state(), 'analysis': analysis}, ensure_ascii=False).replace('<', '\\u003c')
    return template.replace('__REVIEW_BOOTSTRAP__', value).encode('utf-8')


def write_preview(store):
    path = ROOT / 'output/radio-variation-rebuild-20261114/index.html'
    path.write_bytes(render(store))


def serve(store, port, open_browser=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value, mime='application/json; charset=utf-8'):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                if self.path == '/favicon.ico':
                    return self.reply(204, b'')
                if self.path in ('/', '/output/radio-variation-rebuild-20261114/index.html'):
                    return self.reply(200, render(store, True), 'text/html; charset=utf-8')
                if self.path == '/api/state':
                    return self.reply(200, store.state())
                if self.path.startswith('/data/reviewed_radio/clips/'):
                    name = self.path.removeprefix('/data/reviewed_radio/')
                    allowed = {row['audio'] for row in store.catalog()['manifest']['entries']}
                    if name in allowed:
                        return self.reply(200, store._path(name).read_bytes(), 'audio/ogg' if name.endswith('.ogg') else 'audio/wav')
                return self.reply(404, {'error': '없는 주소입니다.'})
            except (OSError, ValueError, KeyError) as error:
                return self.reply(400, {'error': str(error)})

        def do_POST(self):
            expected = f'http://127.0.0.1:{self.server.server_port}'
            if self.headers.get('Origin') != expected or self.headers.get('Content-Type') != 'application/json':
                return self.reply(403, {'error': '로컬 검수실에서 저장하세요.'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 262144:
                    raise ValueError('선택 파일 크기를 확인하세요.')
                value = json.loads(self.rfile.read(length))
                if self.path == '/api/validate':
                    manifest, _ = store.validate(value)
                    return self.reply(200, {'selectedFiles': len(manifest['entries']), 'valid': True})
                if self.path == '/api/apply':
                    result = store.apply(value)
                    if store.directory == (ROOT / 'data/reviewed_radio').resolve():
                        write_preview(store)
                    return self.reply(200, result)
                if self.path == '/api/shutdown':
                    self.reply(200, {'closed': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                return self.reply(404, {'error': '없는 주소입니다.'})
            except (OSError, ValueError, KeyError) as error:
                return self.reply(400, {'error': str(error)})

    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        url = f'http://127.0.0.1:{server.server_port}'
        print(url, flush=True)
        if open_browser:
            threading.Timer(0.2, lambda: webbrowser.open(url)).start()
        server.serve_forever(poll_interval=0.2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'data/reviewed_radio')
    parser.add_argument('--port', type=int, default=25486)
    parser.add_argument('--write-preview', action='store_true')
    parser.add_argument('--apply', type=Path)
    parser.add_argument('--open', action='store_true', help='Open the local listening page in your browser')
    args = parser.parse_args()
    store = ShortRadioSelection(args.directory)
    if args.apply:
        selection = json.loads(args.apply.read_text(encoding='utf-8-sig'))
        print(json.dumps(store.apply(selection), ensure_ascii=False))
        if store.directory == (ROOT / 'data/reviewed_radio').resolve():
            write_preview(store)
    elif args.write_preview:
        write_preview(store)
    else:
        if args.open and args.port:
            from urllib.error import URLError
            from urllib.request import urlopen
            url = f'http://127.0.0.1:{args.port}'
            try:
                with urlopen(url + '/api/state', timeout=2) as response:
                    existing = json.load(response)
                if existing['catalog']['sha256'] == store.catalog()['sha256']:
                    webbrowser.open(url)
                    raise SystemExit(0)
            except (URLError, ValueError, KeyError):
                pass
        serve(store, args.port, args.open)
