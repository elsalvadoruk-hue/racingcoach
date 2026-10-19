"""Serve the built screen (build/) on localhost without Node or the dev server."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import threading

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


class IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def loopback_servers(port: int, handler) -> list[ThreadingHTTPServer]:
    """The screen on 127.0.0.1 and, where the PC has IPv6, on ::1 too. The browser opens "localhost",
    which Windows tries as ::1 first: with 127.0.0.1 alone each new connection waited about 0.2 s."""
    servers = [ThreadingHTTPServer(("127.0.0.1", port), handler)]
    try:
        servers.append(IPv6Server(("::1", port), handler))
    except OSError:
        pass                               # no IPv6: localhost falls back to 127.0.0.1 as before
    return servers


def main() -> int:
    parser = argparse.ArgumentParser(description="iRacing 주행 코치 화면(빌드) 서버")
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "24157")))
    parser.add_argument("--directory", type=Path, default=ROOT / "build")
    args = parser.parse_args()
    if not (args.directory / "index.html").is_file():
        parser.error(f"빌드된 화면이 없습니다: {args.directory}. 먼저 npm run build를 실행하세요.")
    servers = loopback_servers(args.port, partial(ScreenHandler, directory=str(args.directory)))
    print(f"화면 서버: http://localhost:{args.port} ({args.directory})", flush=True)
    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
