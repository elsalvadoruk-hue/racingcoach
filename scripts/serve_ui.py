"""Serve the built screen (build/) on localhost without Node or the dev server."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ScreenHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        # Unknown extension-less paths are client-side routes of the single page.
        target = Path(self.translate_path(self.path))
        if not target.exists() and not target.suffix:
            self.path = "/index.html"
        return super().send_head()

    def end_headers(self):
        # Hashed assets may be cached; the entry page must always be re-read.
        if self.path in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        if args and str(args[1] if len(args) > 1 else "").startswith(("4", "5")):
            super().log_message(format, *args)


def main() -> int:
    parser = argparse.ArgumentParser(description="iRacing 주행 코치 화면(빌드) 서버")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "24157")))
    parser.add_argument("--directory", type=Path, default=ROOT / "build")
    args = parser.parse_args()
    if not (args.directory / "index.html").is_file():
        parser.error(f"빌드된 화면이 없습니다: {args.directory}. 먼저 npm run build를 실행하세요.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(ScreenHandler, directory=str(args.directory)))
    print(f"화면 서버: http://localhost:{args.port} ({args.directory})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
