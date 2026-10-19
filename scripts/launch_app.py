"""Double-click launcher: supervise the API and the screen without npm commands.

The built screen (build/) is served when it is at least as new as its sources;
otherwise, or with --dev, the React dev server is used.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
# Ports few other programs use; 8000 and 3001 collided with development tools too often.
API_PORT = 24158
API_URL = f"http://localhost:{API_PORT}"
UI_PORT = 24157
UI_URL = f"http://localhost:{UI_PORT}"
LOG_DIR = ROOT / "logs"
BUILD_DIR = ROOT / "build"
USE_DEV_SERVER = False
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_http(url: str) -> bytes | None:
    try:
        with HTTP.open(url, timeout=1) as response:
            return response.read(256_000) if response.status == 200 else None
    except (OSError, urllib.error.URLError):
        return None


def api_ready() -> bool:
    data = read_http(f"{API_URL}/openapi.json")
    if not data:
        return False
    try:
        return json.loads(data).get("info", {}).get("title") == "iRacing Telemetry Setup Tuner"
    except (ValueError, AttributeError):
        return False


def ui_ready() -> bool:
    data = read_http(UI_URL)
    if not data:
        return False
    expected = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    title = re.search(r"<title>.*?</title>", expected, re.DOTALL)
    return bool(title and title.group() in data.decode("utf-8", errors="replace"))


def port_busy(port: int) -> bool:
    # A bind probe also rejects recently closed sockets in TIME_WAIT on Windows.
    # Probe active IPv4 and IPv6 listeners so an immediate restart still works.
    for address in ("127.0.0.1", "::1"):
        try:
            with socket.create_connection((address, port), timeout=.3):
                return True
        except OSError:
            continue
    return False


def build_is_current() -> bool:
    """True when build/ exists and no screen source is newer than it."""
    index = BUILD_DIR / "index.html"
    if not index.is_file():
        return False
    built = index.stat().st_mtime
    sources = [ROOT / name for name in ("package.json", "tailwind.config.js", "postcss.config.js", ".env.development")]
    for folder in ("src", "public"):
        sources.extend(path for path in (ROOT / folder).rglob("*") if path.is_file())
    return all(not path.is_file() or path.stat().st_mtime <= built for path in sources)


def commands(need_api: bool, need_ui: bool) -> dict[str, list[str]]:
    result = {}
    if need_api:
        missing = [name for name in ("fastapi", "uvicorn", "numpy", "yaml", "pydantic")
                   if importlib.util.find_spec(name) is None]
        if missing:
            raise RuntimeError("분석 서버 패키지가 없습니다: " + ", ".join(missing)
                               + "\n먼저 프로그램 폴더의 설치.bat을 실행하세요."
                               + f'\n현재 Python: {sys.executable}')
        result["backend"] = [sys.executable, str(ROOT / "iRacing_Backend" / "backend_api.py")]
    if need_ui:
        node = shutil.which("node")
        current = build_is_current()
        # A shared copy has no node_modules: its build is the screen even when an unzip tool that does not
        # keep file times makes src/ look newer, or when the receiver happens to have Node.js installed.
        dev_tools = (ROOT / "node_modules" / "react-scripts").is_dir()
        if not USE_DEV_SERVER and (current or ((not node or not dev_tools) and (BUILD_DIR / "index.html").is_file())):
            if not current and dev_tools:
                print("화면 소스가 빌드보다 새롭습니다. Node.js가 없어 기존 빌드를 사용합니다.", flush=True)
            result["frontend"] = [sys.executable, str(ROOT / "scripts" / "serve_ui.py")]
            return result
        if not node:
            raise RuntimeError("Node.js가 없습니다. Node.js LTS를 설치한 뒤 다시 실행하세요.")
        if not USE_DEV_SERVER:
            print("화면 빌드가 없거나 오래되어 개발 서버로 실행합니다. 가볍게 실행하려면 npm run build를 한 번 실행하세요.", flush=True)
        start = ROOT / "node_modules" / "react-scripts" / "scripts" / "start.js"
        if not start.is_file():
            raise RuntimeError("화면 패키지가 없습니다. 프로젝트 폴더에서 최초 한 번 npm ci를 실행하세요.")
        result["frontend"] = [node, str(start)]
    return result


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # Only process trees started by this launcher are stopped.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run(startup_timeout: float, open_browser: bool) -> int:
    api_running, ui_running = api_ready(), ui_ready()
    for port, ready in ((API_PORT, api_running), (UI_PORT, ui_running)):
        if not ready and port_busy(port):
            raise RuntimeError(f"{port}번 포트를 다른 프로그램이 사용 중이거나 서버가 아직 준비 중입니다."
                               "\n해당 프로그램을 확인하거나 잠시 후 다시 실행하세요.")
    launch_commands = commands(not api_running, not ui_running)
    processes: dict[str, subprocess.Popen] = {}
    logs = []
    try:
        LOG_DIR.mkdir(exist_ok=True)
        for name, command in launch_commands.items():
            env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
            if name == "backend":
                env["PORT"] = str(API_PORT)
            else:
                env.update({"PORT": str(UI_PORT), "HOST": "127.0.0.1", "BROWSER": "none"})
            log = (LOG_DIR / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            # Inherit the BAT console so closing that window also closes its children.
            processes[name] = subprocess.Popen(command, cwd=ROOT, env=env,
                                               stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=os.name != "nt")
            print(f"{'분석 서버' if name == 'backend' else '화면'} 시작 중…", flush=True)
        if api_running or ui_running:
            print("이미 실행 중인 서버는 그대로 사용합니다.", flush=True)
        deadline = time.monotonic() + startup_timeout
        while not (api_ready() and ui_ready()):
            for name, process in processes.items():
                if process.poll() is not None:
                    raise RuntimeError(f"{name} 실행이 종료되었습니다. 로그를 확인하세요: {LOG_DIR / (name + '.log')}")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"시작 대기 시간이 지났습니다. 로그를 확인하세요: {LOG_DIR}")
            time.sleep(.5)
        print(f"\n준비 완료: {UI_URL}", flush=True)
        if open_browser and not webbrowser.open(UI_URL):
            print("브라우저에서 위 주소를 직접 열어주세요.", flush=True)
        if not processes:
            return 0
        print("이 창을 유지하세요. 종료하려면 Ctrl+C를 누르거나 이 창을 닫으세요.", flush=True)
        while True:
            for name, process in processes.items():
                if process.poll() is not None:
                    raise RuntimeError(f"{name} 실행이 종료되었습니다. 로그: {LOG_DIR / (name + '.log')}")
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n이번에 시작한 서버를 종료합니다.", flush=True)
        return 0
    finally:
        for process in processes.values():
            stop_process(process)
        for log in logs:
            log.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="iRacing 주행 코치 실행")
    parser.add_argument("--check", action="store_true", help="필수 프로그램만 확인하고 종료")
    parser.add_argument("--no-browser", action="store_true", help="브라우저 자동 열기 생략")
    parser.add_argument("--startup-timeout", type=float, default=120, help="시작 대기 시간(초)")
    parser.add_argument("--dev", action="store_true", help="빌드 대신 React 개발 서버로 화면 실행")
    args = parser.parse_args()
    global USE_DEV_SERVER
    USE_DEV_SERVER = args.dev
    if os.name == "nt":
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    try:
        print("iRacing 주행 코치 실행 도우미", flush=True)
        if args.check:
            commands(True, True)
            print("Python·Node.js·필수 패키지 확인 완료.", flush=True)
            return 0
        return run(args.startup_timeout, not args.no_browser)
    except (RuntimeError, OSError) as error:
        print(f"\n실행하지 못했습니다. {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
