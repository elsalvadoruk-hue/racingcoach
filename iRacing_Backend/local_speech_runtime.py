"""Optional speech runtime with bounded CPU, one owner and killable inference."""
import atexit
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

from local_speech_worker import decode_audio
import local_speech_enhancement as enhancement

MODELS = ('sensevoice', 'base', 'small')
FLAGS = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0


class LocalSpeechRuntime:
    def __init__(self, root):
        self.root = Path(root)
        self.packages = self.root / f'packages-py{sys.version_info.major}{sys.version_info.minor}'
        self.worker = Path(__file__).with_name('local_speech_worker.py')
        self.lock = threading.RLock()
        self.process = None
        self.owner = None
        self.model = 'sensevoice'
        self.expires = 0
        self.busy = False
        self.setup_state = 'idle'
        self.error = ''
        self.setup_process = None
        self.closed = False
        self.enhancement_ready = False
        atexit.register(self.close)

    def package_dir(self, model):
        return self.root / (self.packages.name + "-sensevoice") if model == "sensevoice" else self.packages

    def installed(self, model):
        if model == "sensevoice":
            packages = self.package_dir(model)
            return (packages / "sherpa_onnx").is_dir() and (packages / "numpy").is_dir() and all(
                (self.root / model / name).is_file() for name in ("model.int8.onnx", "tokens.txt"))
        return (self.packages / 'faster_whisper').is_dir() and all(
            (self.root / model / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json'))

    def status(self):
        with self.lock:
            if self.owner and time.monotonic() > self.expires:
                self._stop()
            if self.process and self.process.poll() is not None:
                self._stop()
            running = bool(self.process and self.process.poll() is None)
            return {'installed': {name: self.installed(name) for name in MODELS},
                    'enhancement_installed': enhancement.installed(self.root / 'sensevoice'),
                    'enhancement_ready': running and self.enhancement_ready,
                    'model_loaded': running, 'idle_policy': 'resident_sleep',
                    'models': {
                        'sensevoice': {'url': 'https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17', 'model_mb':240, 'installed_mb':347,
                                      'packages_url':'https://pypi.org/project/sherpa-onnx/2.0.3/', 'packages_mb':107},
                        'base': {'url': 'https://huggingface.co/Systran/faster-whisper-base', 'model_mb': 148, 'installed_mb': 440},
                        'small': {'url': 'https://huggingface.co/Systran/faster-whisper-small', 'model_mb': 486, 'installed_mb': 778},
                    }, 'packages_url': 'https://pypi.org/project/faster-whisper/1.2.1/', 'shared_packages_mb': 292,
                    'state': 'busy' if self.busy and running else 'ready' if running else 'off',
                    'model': self.model, 'setup': self.setup_state, 'error': self.error,
                    'cpu_threads': 2, 'device': 'cpu', 'compute_type': 'int8', 'max_seconds': 10}

    def setup(self, model, enhancement_only=False):
        if model not in MODELS:
            raise ValueError('인식 모델을 확인하세요.')
        with self.lock:
            if self.closed:
                raise ValueError('앱이 종료 중입니다.')
            if self.owner:
                raise ValueError('마이크를 끈 뒤 인식기를 설치하세요.')
            if self.setup_state in ('packages', 'model'):
                return self.status()
            if enhancement_only and (model != 'sensevoice' or not self.installed(model)):
                raise ValueError('SenseVoice를 먼저 설치하세요.')
            self.setup_state, self.error = ('model' if enhancement_only else 'packages'), ''
            threading.Thread(target=self._setup, args=(model, enhancement_only), daemon=True, name='local-speech-install').start()
        return self.status()

    def _run_setup(self, command):
        with self.lock:
            if self.closed:
                raise RuntimeError('closed')
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=FLAGS)
            self.setup_process = process
        try:
            if process.wait(timeout=900):
                raise RuntimeError('installation failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            with self.lock:
                if self.setup_process is process:
                    self.setup_process = None

    def _setup(self, model, enhancement_only=False):
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if enhancement_only:
                self._run_setup([sys.executable, '-I', str(self.worker), '--packages', str(self.package_dir(model)),
                                 '--model', str(self.root / model), '--download-enhancement'])
                with self.lock:
                    self.setup_state = 'complete'
                return
            marker = self.root / (self.packages.name + '-v1.ready')
            # An explicit repair also restores missing transitive dependencies.
            self._run_setup([sys.executable, '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                '--only-binary=:all:', '--upgrade', '--target', str(self.package_dir(model)), '-r',
                str(Path(__file__).with_name('requirements_sensevoice.txt' if model == 'sensevoice' else 'requirements_local_speech.txt'))])
            marker.touch()
            with self.lock:
                self.setup_state = 'model'
            self._run_setup([sys.executable, '-I', str(self.worker), '--packages', str(self.package_dir(model)),
                             '--model', str(self.root / model), '--download', model])
            with self.lock:
                self.setup_state = 'complete'
        except Exception:
            with self.lock:
                self.setup_state = 'failed'
                self.error = '인식기 설치에 실패했습니다. 인터넷·디스크 여유·Python의 pip 설치를 확인한 뒤 다시 시도하세요.'

    @staticmethod
    def _read(process, timeout):
        result = queue.Queue(maxsize=1)
        def read():
            try:
                result.put(process.stdout.readline())
            except (OSError, ValueError):
                result.put('')
        threading.Thread(target=read, daemon=True, name='local-speech-response').start()
        try:
            line = result.get(timeout=timeout)
        except queue.Empty as exc:
            raise ValueError('인식 시간이 초과됐습니다. 마이크를 다시 준비하세요.') from exc
        if not line:
            raise ValueError('인식기가 종료됐습니다. 마이크를 다시 준비하세요.')
        return json.loads(line)

    def activate(self, owner, model):
        if model not in MODELS or not isinstance(owner, str) or not 8 <= len(owner) <= 100:
            raise ValueError('인식기 요청을 확인하세요.')
        with self.lock:
            self.status()
            if self.owner and self.owner != owner:
                raise ValueError('다른 앱 창에서 로컬 마이크를 사용 중입니다.')
            if self.process:
                if self.model != model or self.busy:
                    raise ValueError('마이크를 끈 뒤 모델을 바꾸세요.')
                self.expires = time.monotonic() + 45
                return self.status()
            if self.closed or not self.installed(model) or self.setup_state in ('packages', 'model'):
                raise ValueError('먼저 로컬 인식기를 설치하세요.')
            self.owner, self.model, self.busy = owner, model, True
            self.expires = time.monotonic() + 60
            try:
                process = subprocess.Popen([sys.executable, '-I', str(self.worker), '--packages', str(self.package_dir(model)),
                    '--model', str(self.root / model), '--engine', 'sensevoice' if model == 'sensevoice' else 'whisper'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, encoding='utf-8', creationflags=FLAGS)
            except OSError:
                self._stop()
                raise
            self.process = process
        try:
            ready = self._read(process, 45)
            if not ready.get('ready'):
                raise ValueError('인식 모델을 불러오지 못했습니다.')
            with self.lock:
                if self.process is not process:
                    raise ValueError('마이크 준비가 취소됐습니다.')
                self.busy = False
                self.enhancement_ready = bool(ready.get('enhancement_ready'))
                self.error = ''
                threading.Thread(target=self._lease_watch, args=(process,), daemon=True,
                                 name='local-speech-lease').start()
                return self.status()
        except Exception:
            with self.lock:
                if self.process is process:
                    self._stop()
            raise

    def _lease_watch(self, process):
        while True:
            time.sleep(5)
            with self.lock:
                if self.process is not process:
                    return
                if time.monotonic() > self.expires:
                    self._stop()
                    return

    def heartbeat(self, owner):
        with self.lock:
            if not self.owner or self.owner != owner or not self.process or self.process.poll() is not None:
                raise ValueError('마이크를 다시 준비하세요.')
            self.expires = time.monotonic() + 45

    def recognize(self, owner, audio, enhance=False):
        decode_audio(audio)
        with self.lock:
            self.heartbeat(owner)
            if self.busy:
                raise ValueError('이전 질문을 처리 중입니다.')
            self.busy = True
            process = self.process
        try:
            process.stdin.write(json.dumps({'audio': audio, 'enhance': enhance}) + '\n')
            process.stdin.flush()
            result = self._read(process, 15)
            if result.get('error'):
                raise ValueError(result['error'])
            with self.lock:
                if self.process is not process:
                    raise ValueError('마이크가 꺼져 질문을 취소했습니다.')
            return result
        except Exception:
            with self.lock:
                if self.process is process:
                    self._stop()
            raise
        finally:
            with self.lock:
                if self.process is process:
                    self.busy = False

    def deactivate(self, owner):
        with self.lock:
            if self.owner == owner:
                self._stop()

    def _stop(self):
        process, self.process = self.process, None
        self.owner, self.busy = None, False
        if process:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (process.stdin, process.stdout):
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass

    def close(self):
        with self.lock:
            self.closed = True
            self._stop()
            if self.setup_process and self.setup_process.poll() is None:
                self.setup_process.kill()
