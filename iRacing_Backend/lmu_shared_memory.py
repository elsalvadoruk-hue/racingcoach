"""Explicit LMU transport using a native bridge built against the installed SDK.

Python never mirrors proprietary SDK structs. Import, construction, ABI probe and
build do not attach to the game; only ``open`` opens the game-created objects.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import threading
import uuid


SDK_DIR = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Le Mans Ultimate\Support\SharedMemoryInterface")
BUILD_DIR = Path(__file__).resolve().parents[1] / "logs" / "lmu-native"
BUNDLED_DIR = Path(__file__).with_name("lmu_bridges")
HEADERS = ("SharedMemoryInterface.hpp", "InternalsPlugin.hpp", "PluginObjects.hpp")
BRIDGE_SOURCE = Path(__file__).with_name("lmu_native_bridge.cpp")


class LmuLiveUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _unavailable(code):
    messages = {
        "GAME_NOT_RUNNING": "LMU가 실행 중이지 않습니다.",
        "MULTIPLE_GAME_PROCESSES": "LMU 프로세스가 여러 개여서 연결 대상을 확인할 수 없습니다.",
        "GAME_EXITED": "LMU가 종료되어 실시간 연결을 해제했습니다.",
        "NOT_OPEN": "실시간 연결을 먼저 시작해야 합니다.",
        "ABI_MISMATCH": "LMU 버전에 맞는 실시간 연결 파일을 확인하고 있습니다.",
        "NATIVE_NOT_BUILT": "LMU 실시간 연결 파일을 자동으로 준비합니다.",
        "SDK_UNAVAILABLE": "LMU 설치 파일을 확인할 수 없습니다. Steam에서 LMU 설치와 파일 무결성을 확인해 주세요.",
        "CONNECTION_NEEDED": "LMU 실시간 연결을 켜면 필요한 파일을 자동으로 준비합니다.",
        "CONNECTION_PREPARING": "LMU 버전에 맞는 연결 파일을 자동으로 받는 중입니다.",
        "CONNECTION_UPDATE_PENDING": "현재 LMU 버전용 연결 파일을 준비 중입니다. 준비되면 자동으로 다시 연결합니다.",
        "CONNECTION_DOWNLOAD_FAILED": "LMU 연결 파일을 받지 못했습니다. 인터넷 연결을 확인하고 다시 연결해 주세요.",
        "UNSUPPORTED_PLATFORM": "LMU 실시간 연결은 64비트 Windows Python에서 지원합니다.",
        "COMPILER_UNAVAILABLE": "Visual Studio C++ 빌드 도구를 찾을 수 없습니다.",
        "BUILD_FAILED": "LMU 실시간 브리지 빌드에 실패했습니다. 빌드 로그를 확인해 주세요.",
        "GAME_VERSION_INVALID": "LMU 게임 버전 값을 확인할 수 없어 실시간 연결을 중단했습니다.",
        "GAME_VERSION_CHANGED": "연결 중 LMU 게임 버전 값이 바뀌어 실시간 연결을 중단했습니다.",
    }
    return LmuLiveUnavailable(code, messages.get(code, "LMU 실시간 공유 메모리를 안전하게 연결할 수 없습니다."))


def resolve_sdk_dir(sdk_dir=None):
    """Resolve explicit > LMU_SDK_DIR > the installed game (any Steam library)."""
    configured = os.environ.get("LMU_SDK_DIR", "").strip()
    if sdk_dir is None and not configured:
        from lmu_install import default_game_folder
        return (default_game_folder() / "Support" / "SharedMemoryInterface").resolve()
    return Path(sdk_dir if sdk_dir is not None else configured).expanduser().resolve()


def sdk_configuration(sdk_dir=None, *, source=None):
    """Non-identifying diagnostics; never load a DLL or attach to the game."""
    folder = resolve_sdk_dir(sdk_dir)
    missing = [name for name in HEADERS if not (folder / name).is_file()]
    return {"source": source or ("EXPLICIT" if sdk_dir is not None else
                                "ENVIRONMENT" if os.environ.get("LMU_SDK_DIR", "").strip() else "DEFAULT"),
            "headers_available": not missing, "missing_headers": missing}


def header_fingerprint(sdk_dir=None):
    sdk_dir = resolve_sdk_dir(sdk_dir)
    digest = hashlib.sha256()
    try:
        for name in HEADERS:
            digest.update(name.encode("ascii") + b"\0")
            digest.update((sdk_dir / name).read_bytes())
    except OSError as exc:
        raise _unavailable("SDK_UNAVAILABLE") from exc
    return digest.hexdigest()


def _platform_check():
    if os.name != "nt" or struct.calcsize("P") != 8:
        raise _unavailable("UNSUPPORTED_PLATFORM")


def build_artifact_dir(sdk_dir=None):
    """Content-addressed native output; never overwrite a previous loaded DLL."""
    fingerprint = header_fingerprint(sdk_dir)
    identity = hashlib.sha256(bridge_source_bytes() + fingerprint.encode('ascii')).hexdigest()[:24]
    return BUILD_DIR / ('bridge-' + identity)


def downloaded_bridge_dir(sdk_dir=None):
    return BUILD_DIR / 'downloads' / build_artifact_dir(sdk_dir).name


def bridge_source_bytes():
    # Git source ZIPs use LF; Windows checkouts may use CRLF. C++ translation
    # treats those identically, so the identity must not depend on installation.
    return BRIDGE_SOURCE.read_bytes().replace(b'\r\n', b'\n')


def validate_bundled_bridges(folder=None):
    """Check redistributable artifacts without loading native code or reading game data."""
    folder = Path(folder) if folder is not None else BUNDLED_DIR
    artifacts = sorted(folder.glob('bridge-*'))
    if not artifacts:
        raise ValueError('배포할 LMU 브리지가 없습니다. python scripts/prepare_lmu_bridge.py를 실행하세요.')
    source_hash = hashlib.sha256(bridge_source_bytes()).hexdigest()
    for artifact in artifacts:
        try:
            manifest = json.loads((artifact / 'manifest.json').read_text(encoding='utf-8'))
            dll = (artifact / 'lmu_native_bridge.dll').read_bytes()
            fingerprint = manifest['header_fingerprint']
            identity = hashlib.sha256(bridge_source_bytes() + fingerprint.encode('ascii')).hexdigest()[:24]
            pe = struct.unpack_from('<I', dll, 0x3c)[0]
            valid = (isinstance(fingerprint, str) and len(fingerprint) == 64 and
                     all(c in '0123456789abcdef' for c in fingerprint) and
                     artifact.name == 'bridge-' + identity and manifest.get('bridge_version') == 1 and
                     manifest.get('source_sha256') == source_hash and
                     manifest.get('dll_sha256') == hashlib.sha256(dll).hexdigest() and
                     manifest.get('runtime') == 'STATIC_MSVC' and
                     dll[:2] == b'MZ' and dll[pe:pe + 4] == b'PE\0\0' and
                     struct.unpack_from('<H', dll, pe + 4)[0] == 0x8664 and
                     {p.name for p in artifact.iterdir()} == {'manifest.json', 'lmu_native_bridge.dll'})
        except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error) as exc:
            raise ValueError(f'LMU 배포 브리지 확인 실패: {artifact.name}') from exc
        if not valid:
            raise ValueError(f'LMU 배포 브리지 확인 실패: {artifact.name}')
    return artifacts


def build_native_bridge(sdk_dir=None, build_dir=None):
    """Explicit local build; does not open mappings, events or a game process."""
    _platform_check()
    source = SharedMemorySource(sdk_dir=sdk_dir, build_dir=build_dir)
    sdk_dir, build_dir = source.sdk_dir, source.build_dir
    fingerprint = header_fingerprint(sdk_dir)
    if source._build_dir_override is None and (build_dir / 'manifest.json').is_file():
        # A verified immutable artifact can already be mapped by another app.
        # Reuse it without invoking the linker or touching any existing file.
        try:
            return source.probe()
        except LmuLiveUnavailable:
            if build_dir.parent != BUNDLED_DIR:
                raise
            # Repair a damaged shipped pair locally, never overwrite a bundle
            # which may already be loaded by another running app.
            source = SharedMemorySource(sdk_dir=sdk_dir, build_dir=build_artifact_dir(sdk_dir))
            build_dir = source.build_dir
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    try:
        installations = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
            capture_output=True, text=True, check=True, timeout=20,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise _unavailable("COMPILER_UNAVAILABLE") from exc
    vcvars = Path(installations) / "VC/Auxiliary/Build/vcvars64.bat"
    if not installations or not vcvars.is_file():
        raise _unavailable("COMPILER_UNAVAILABLE")
    build_dir.mkdir(parents=True, exist_ok=True)
    # A generated preinclude avoids fragile command-shell quoting of string macros.
    preinclude = build_dir / "lmu-fingerprint.h"
    preinclude.write_text(f'#define LMU_HEADER_FINGERPRINT "{fingerprint}"\n', encoding="ascii")
    dll_path = build_dir / "lmu_native_bridge.dll"
    script = build_dir / "build.cmd"
    def q(value):
        text = str(value)
        if any(c in text for c in '\r\n%"'):
            raise ValueError("빌드 경로에 지원하지 않는 문자가 있습니다.")
        return f'"{text}"'
    script.write_text(
        f"@echo off\r\nset \"PATH={vswhere.parent};%PATH%\"\r\ncall {q(vcvars)} >nul\r\nif errorlevel 1 exit /b 1\r\n"
        f"cl /nologo /std:c++17 /EHsc /LD /MT /O2 /I{q(sdk_dir)} /FI{q(preinclude)} "
        f"/Fo{q(build_dir / 'lmu_native_bridge.obj')} /Fe{q(dll_path)} {q(BRIDGE_SOURCE)} "
        f"/link /IMPLIB:{q(build_dir / 'lmu_native_bridge.lib')}\r\n",
        encoding="utf-8",
    )
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=build_dir,
                            capture_output=True, timeout=120)
    (build_dir / "build.log").write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise _unavailable("BUILD_FAILED")
    manifest = {
        "bridge_version": 1,
        "header_fingerprint": fingerprint,
        "source_sha256": hashlib.sha256(bridge_source_bytes()).hexdigest(),
        "dll_sha256": hashlib.sha256(dll_path.read_bytes()).hexdigest(),
        "runtime": "STATIC_MSVC",
    }
    (build_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return source.probe()


class SharedMemorySource:
    supports_readonly_pit_rest = True
    def __init__(self, *, sdk_dir=None, build_dir=None):
        self._sdk_source = ("EXPLICIT" if sdk_dir is not None else
                            "ENVIRONMENT" if os.environ.get("LMU_SDK_DIR", "").strip() else "DEFAULT")
        self.sdk_dir = resolve_sdk_dir(sdk_dir)
        self._build_dir_override = Path(build_dir).resolve() if build_dir is not None else None
        self._artifact_dir = None
        self._dll = None
        self._handle = None
        self._guard = threading.RLock()
        self._connection_id = None
        self._fingerprint = None

    @property
    def build_dir(self):
        # Resolve lazily: constructing a source or inspecting its configuration
        # must still succeed on a machine without installed SDK headers.
        if self._build_dir_override is not None:
            return self._build_dir_override
        if self._artifact_dir is None:
            local = build_artifact_dir(self.sdk_dir)
            downloaded = downloaded_bridge_dir(self.sdk_dir)
            bundled = BUNDLED_DIR / local.name
            self._artifact_dir = (downloaded if (downloaded / 'manifest.json').is_file() else
                                  local if (local / 'manifest.json').is_file() else
                                  bundled if (bundled / 'manifest.json').is_file() else local)
        return self._artifact_dir

    def configuration(self):
        return sdk_configuration(self.sdk_dir, source=self._sdk_source)

    def _load(self):
        _platform_check()
        fingerprint = header_fingerprint(self.sdk_dir)
        dll_path = self.build_dir / "lmu_native_bridge.dll"
        try:
            manifest = json.loads((self.build_dir / "manifest.json").read_text(encoding="utf-8"))
            dll_hash = hashlib.sha256(dll_path.read_bytes()).hexdigest()
        except (OSError, ValueError) as exc:
            raise _unavailable("NATIVE_NOT_BUILT") from exc
        if not isinstance(manifest, dict):
            raise _unavailable("ABI_MISMATCH")
        if (manifest.get("header_fingerprint") != fingerprint or
            manifest.get("source_sha256") != hashlib.sha256(bridge_source_bytes()).hexdigest() or
            manifest.get("dll_sha256") != dll_hash):
            raise _unavailable("ABI_MISMATCH")
        if self._dll is None:
            try:
                dll = ctypes.CDLL(str(dll_path.resolve()))
                dll.lmu_error.argtypes, dll.lmu_error.restype = [], ctypes.c_char_p
                dll.lmu_abi.argtypes, dll.lmu_abi.restype = [ctypes.c_char_p, ctypes.c_size_t], ctypes.c_int
                dll.lmu_open.argtypes, dll.lmu_open.restype = [], ctypes.c_void_p
                dll.lmu_read.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_size_t]
                dll.lmu_read.restype = ctypes.c_int
                dll.lmu_close.argtypes, dll.lmu_close.restype = [ctypes.c_void_p], None
                self._dll = dll
            except (OSError, AttributeError) as exc:
                raise _unavailable("ABI_MISMATCH") from exc
        buffer = ctypes.create_string_buffer(8192)
        if self._dll.lmu_abi(buffer, len(buffer)) != 1:
            raise _unavailable("ABI_MISMATCH")
        try:
            abi = json.loads(buffer.value)
        except ValueError as exc:
            raise _unavailable("ABI_MISMATCH") from exc
        if not isinstance(abi, dict) or abi.get("pointer_size") != 8 or abi.get("bridge_version") != 1 or abi.get("header_fingerprint") != fingerprint:
            raise _unavailable("ABI_MISMATCH")
        self._fingerprint = fingerprint
        return abi

    def probe(self):
        """Verify native build and SDK ABI without attaching to the game."""
        with self._guard:
            return {"provider": "LMU_SHARED_MEMORY", "state": "READY", "attached": bool(self._handle),
                    "sdk_dir": str(self.sdk_dir), "sdk_configuration": self.configuration(),
                    "game_version_support": "UNVERIFIED_INSTALLED_SDK_ONLY",
                    "abi": self._load()}

    def open(self):
        with self._guard:
            diagnostics = self.prepare()
            if not self._handle:
                self._handle = self._dll.lmu_open()
                if not self._handle:
                    raise _unavailable(self._dll.lmu_error().decode("ascii", "replace"))
                self._connection_id = uuid.uuid4().hex
            return {**diagnostics, "state": "CONNECTED", "attached": True}

    def prepare(self, *, retry=False):
        """Use a shipped reader, or request its precompiled replacement in a worker."""
        if self._dll is None and self._build_dir_override is None:
            self._artifact_dir = None
        try:
            return self.probe()
        except LmuLiveUnavailable as exc:
            if self._build_dir_override is not None or exc.code not in ('NATIVE_NOT_BUILT', 'ABI_MISMATCH'):
                raise
            self.close()
            self._dll = None
            from lmu_bridge_downloads import downloads
            result = downloads.request(self.sdk_dir, retry=retry)
            if result['state'] == 'READY':
                self._artifact_dir = None
                return self.probe()
            raise _unavailable(result['state']) from exc

    def read(self, timeout_ms=200):
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 0 <= timeout_ms <= 1000:
            raise ValueError("대기 시간은 0~1000밀리초 정수여야 합니다.")
        with self._guard:
            if not self._handle:
                raise _unavailable("NOT_OPEN")
            try:
                if header_fingerprint(self.sdk_dir) != self._fingerprint:
                    raise _unavailable("ABI_MISMATCH")
            except LmuLiveUnavailable:
                self.close()
                raise
            # Bounded 104-car proximity transport, with no identifying strings.
            buffer = ctypes.create_string_buffer(262144)
            result = self._dll.lmu_read(self._handle, timeout_ms, buffer, len(buffer))
            if result < 0:
                code = self._dll.lmu_error().decode("ascii", "replace")
                self.close()
                raise _unavailable(code)
            if result == 0:
                return None
            try:
                data = json.loads(buffer.value)
                data["session_id"] = self._connection_id + ":" + data["session_id"]
            except (ValueError, TypeError, KeyError) as exc:
                self.close()
                raise _unavailable("SHARED_LAYOUT_INVALID") from exc
            return data

    def close(self):
        with self._guard:
            if self._handle:
                self._dll.lmu_close(self._handle)
            self._handle = None
            self._connection_id = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
