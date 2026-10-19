"""Synthesize every engineer wording and number sentence into the app's audio cache.

The same job as the screen's "전부 받아 두기" button, runnable without the app.
Sends real, billed Google TTS requests (about 17,400 short sentences on a
first run). Already cached clips are skipped, so it can be stopped with
Ctrl+C and started again.

    python scripts/prepare_radio_audio.py            # fetch what is missing
    python scripts/prepare_radio_audio.py --status   # count only, no requests
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "iRacing_Backend"))


def cache_directory() -> Path:
    # The folder the app uses: next to the voice note database unless overridden.
    import app_settings
    database = os.getenv("IRACING_VOICE_DB_PATH", "").strip()
    folder = Path(database).expanduser().parent if database else app_settings.data_dir()
    return Path(os.getenv("IRACING_RADIO_AUDIO_DIR") or folder / "radio_audio")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="합성 요청 없이 준비 상태만 출력")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import app_settings
    app_settings.apply_environment()
    from radio_audio import RadioAudioCache
    from voice_coach import AudioPreparation, GoogleSpeech

    directory = cache_directory()
    job = AudioPreparation(RadioAudioCache(GoogleSpeech(), directory))
    status = job.status()
    print(f"저장 위치: {directory}")
    print(f"준비됨 {status['ready']} / {status['total']} · 남은 {status['missing']}개, 약 {status['characters_missing']}자", flush=True)
    if args.status or not status["missing"]:
        return 0
    started = time.monotonic()
    job.start()
    try:
        while job.running():
            time.sleep(30)
            status = job.status()
            print(f"[{(time.monotonic() - started) / 60:5.1f}분] {status['ready']} / {status['total']} · 실패 {status['failed']}", flush=True)
    except KeyboardInterrupt:
        job.stop()
        job.join(30)
    job._ready = (0., None)
    status = job.status()
    print(f"끝: {status['state']} · 준비됨 {status['ready']} / {status['total']} · 실패 {status['failed']} {status['message']}", flush=True)
    return 0 if status["state"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
