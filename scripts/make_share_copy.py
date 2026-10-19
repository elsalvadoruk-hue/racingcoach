"""Make a copy of the app that can be handed to someone else: one folder, nothing personal in it.

The copy holds the program, the built screen and a data/ folder with the voice clips
already fetched, the confirmed corner numbers and the measured pit facts. It does not
hold the analysis database, voice notes, telemetry, Garage61 laps, the Google key,
the Garage61 token, logs or backups. Whoever receives it enters their own folders,
key and token on the screen (Settings tab); those land in the copy's own data/ folder.

    python scripts/make_share_copy.py                       # writes to share/iracing-coach
    python scripts/make_share_copy.py --out E:\\coach-share
    python scripts/make_share_copy.py --no-audio            # without the 0.9 GB of voice clips
    python scripts/make_share_copy.py --out share/iracing-coach-1.2.0 --zip   # also the full installer zip beside it

Nothing is sent anywhere and this installation is not changed.
"""
from __future__ import annotations

import argparse
import ast
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "iRacing_Backend"))
import app_settings  # noqa: E402

# Only what is listed here is copied; a new personal file can never slip in by default.
ROOT_FILES = ("실행.bat", "설치.bat", "패치 노트.txt", "README.md", "package.json", "package-lock.json", "jsconfig.json", "components.json",
              "tailwind.config.js", "postcss.config.js", ".gitignore", "log-upload-url.txt")
ROOT_FOLDERS = ("src", "public", "build", "manual")
BACKEND_SKIP = ("gcp-key.json", "garage61_api_token.txt")
SCRIPTS = ("launch_app.py", "serve_ui.py", "prepare_radio_audio.py", "test_launch_app.py", "make_share_copy.py", "make_patch.py", "read_coach_log.py", "read_timing_log.py",
           # 설치.bat and 실행.bat pick a supported Python through this.
           "find_python.bat",
           # The web settings tab and the driving overlay run this window process.
           "overlay_sample.py", "overlay_trace.py", "overlay_celebration.py", "overlay_demo_data.py",
           "overlay_native.py", "overlay_theme.py", "overlay_rendering.py", "overlay_commands.py")
REQUIRED_OVERLAY_SCRIPTS = ("overlay_sample.py", "overlay_trace.py", "overlay_celebration.py", "overlay_demo_data.py",
                            "overlay_native.py", "overlay_theme.py", "overlay_rendering.py", "overlay_commands.py")
DATA_FILES = ("corner-definitions.json", "pit-knowledge.json")
TEXT_SUFFIXES = {".py", ".js", ".json", ".md", ".txt", ".bat", ".ps1", ".html", ".css", ".map", ".svg", ".yml", ".yaml", ".cfg", ".ini"}

FIRST_RUN = """iRacing 주행 코치 {version} - 처음 실행

그림이 들어간 설명서는 manual 폴더의 "매뉴얼.html"입니다. 더블클릭하면 브라우저로 열립니다.


■ 설치 (처음 한 번)

1. Python 3.10 이상을 설치합니다. 권장은 3.12입니다(3.13도 됩니다).
   python.org 에서 받고, 설치 첫 화면에서 "Add Python to PATH"를 꼭 체크하세요.
   Python 설치 관리자를 쓰고 있다면 명령 창에서 "py install 3.12"를 실행해도 됩니다.
   이미 깔린 Python이 있으면 그대로 두세요. 여러 개가 있으면 설치.bat이 3.12 → 3.13을 먼저 고릅니다.
2. 이 폴더를 원하는 곳에 둡니다. 한글이나 공백이 있는 경로도 괜찮습니다.
3. "설치.bat"을 더블클릭합니다. 필요한 패키지를 인터넷에서 받습니다. 몇 분 걸립니다.


■ 실행 (매번)

1. "실행.bat"을 더블클릭합니다.
2. 검은 창이 하나 뜨고 브라우저에 http://localhost:24157 이 열립니다.
   검은 창을 닫으면 프로그램이 꺼집니다.
3. 주행 중 음성을 들으려면 "음성 코치" 탭을 열고 "스포터 켜기"를 누릅니다.
   이 탭이 열려 있어야 음성이 나옵니다.


■ 아무 설정 없이 되는 것

- 주행 파일(IBT) 분석, 코너 비교, 셋업 A/B 실험
- 스포터: 좌우 차량, 깃발, 피트 안내
- 엔지니어 자동 안내: 랩타임, 연료, 피트, 타이어, 손상, 코너 코칭
  음성 약 5만 개가 이미 들어 있어서 인터넷이나 키 없이 나옵니다.

iRacing 기본 위치(문서\\iRacing\\telemetry)의 주행 파일은 자동으로 찾습니다.
다른 곳에 저장한다면 "설정" 탭에서 그 폴더를 추가하세요.


■ 따로 준비해야 되는 것 (없어도 위의 것은 전부 됩니다)

- Google Cloud 키: 마이크로 묻기(음성 인식), AI가 설명하는 답변, 새 문장의 음성 합성.
  본인 Google Cloud 계정이 필요하고 사용한 만큼 본인에게 과금됩니다.
  만드는 방법은 "Google Cloud 서비스 계정 키 만들기"로 검색하세요.
  프로젝트에서 Speech-to-Text, Text-to-Speech, Vertex AI를 켜고
  서비스 계정의 JSON 키 파일을 받으면 됩니다.
  받은 파일은 "설정" 탭의 "키 파일 등록"으로 넣습니다.

- Garage61 토큰: 다른 드라이버의 기준 랩과 비교할 때만 필요합니다.
  만드는 방법은 "Garage61 API token"으로 검색하세요.
  받은 토큰은 "설정" 탭에 붙여 넣습니다.
  Garage61이 없어도 스포터와 엔지니어는 전부 동작하고, 분석은 내 랩끼리 비교합니다.


■ 알아 둘 것

- 모든 기록과 설정, 등록한 키와 토큰은 이 폴더의 data 폴더에 저장됩니다.
  백업하거나 다른 PC로 옮길 때는 이 폴더를 통째로 복사하면 됩니다.
- 이 폴더를 다시 남에게 줄 때는 통째로 주지 마세요. 내 기록과 키가 들어 있습니다.
  이 폴더에서 "python scripts\\make_share_copy.py"를 실행하면
  기록과 키를 뺀 공유본이 share 폴더에 만들어집니다.
- 브라우저 주소는 항상 http://localhost:24157 로 여세요.
  스포터 설정과 출력장치 선택이 이 주소의 브라우저에 저장됩니다.
- iRacing이나 Crew Chief의 스포터와 겹치지 않게 한 곳만 켜세요.
- 프로그램은 게임에 아무 명령도 보내지 않습니다. iRacing이 공개하는 값을 읽기만 합니다.


■ 개선을 부탁할 때

주행 중 안내가 이상했다면 "설정" 탭의 "세션 로그 보내기"에서 그 주행의
"보내기"를 누르면 그 주행의 로그가 앱을 만든 사람에게 바로 전달됩니다.
"보내기" 버튼이 없으면 "로그 파일 받기"로 받은 zip 파일을 직접 보내 주세요.
음성, 주행 파일, 키, 토큰, 폴더 경로는 들어가지 않고 드라이버 이름은 번호로 바뀝니다.


■ 업데이트

새 버전은 작은 업데이트 파일(zip)로 나옵니다. 이 폴더에 풀어 덮어쓰면 되고 기록과 설정은 그대로 남습니다.
버전 순서대로 하나씩 적용하세요(예: 1.0.2 → 1.1.0 → 1.2.0). 건너뛰면 그 사이에 추가된 음성이 빠집니다.
지금 버전은 "설정" 탭 맨 위에 나오고, 버전마다 달라진 것은 "패치 노트.txt"에 있습니다.


■ 문제가 생기면

- 설치.bat에서 "Failed building wheel for PyYAML", "Microsoft Visual C++ 14.0 or greater is required":
  1.1.0 이하의 설치.bat에서 Python 3.15 같은 새 버전을 쓸 때 나던 오류입니다(1.2.0에서 고침). 최신 업데이트를 적용하고
  설치.bat을 다시 실행하세요. Visual C++을 설치할 필요는 없습니다.
- 설치.bat이 "Installation failed on Python 3.xx"와 "py install 3.12"를 안내함: 아주 새 Python에
  아직 준비되지 않은 패키지가 있는 것입니다. Python 3.12를 추가로 설치하고 설치.bat을 다시 실행하세요.
  다른 버전은 지우지 않아도 됩니다.
- 실행.bat이 바로 꺼짐: Python 설치와 "설치.bat" 실행 여부를 확인하세요.
- 주행 파일이 안 보임: iRacing에서 텔레메트리 기록을 켰는지, 차에서 내렸는지,
  "설정" 탭의 주행 파일 폴더에 실제 저장 위치가 있는지 확인하세요.
- 음성이 안 나옴: "음성 코치" 탭이 열려 있는지, 스포터를 켰는지,
  출력장치와 볼륨을 확인하고 "테스트 소리"를 눌러 보세요.
- 그 밖의 것은 프로그램 안의 "사용 가이드" 탭과 manual 폴더의 설명서를 보세요.
"""


def copy_tree(source: Path, target: Path, skip=lambda path: False) -> int:
    count = 0
    for path in source.rglob("*"):
        if path.is_dir() or "__pycache__" in path.parts or skip(path):
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)      # keeps times, so build/ stays newer than src/
        count += 1
    return count


def personal_markers() -> dict[str, str]:
    """Strings that must not appear in the copy, by what they are. The values are never printed."""
    # The name in the home folder is what paths carry; a shell may report another login name.
    markers = {"Windows 사용자 이름": Path.home().name, "로그인 이름": getpass.getuser(), "데이터 폴더 경로": str(app_settings.data_dir())}
    try:
        key = json.loads(app_settings.key_path().read_text(encoding="utf-8"))
        markers.update({"Google 프로젝트 ID": key.get("project_id") or "", "Google 서비스 계정": key.get("client_email") or "",
                        "Google 키 ID": key.get("private_key_id") or ""})
    except (OSError, ValueError):
        pass
    try:
        markers["Garage61 토큰"] = (app_settings.data_dir() / app_settings.TOKEN_NAME).read_text(encoding="utf-8-sig").strip()
    except OSError:
        pass
    return {name: value for name, value in markers.items() if value and len(value) >= 4}


def scan(folder: Path, markers: dict[str, str]) -> list[tuple[str, str]]:
    found = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 30_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        for name, value in markers.items():
            if value.lower() in text:
                found.append((str(path.relative_to(folder)), name))
    return found


def overlay_runtime_scripts() -> set[str]:
    """Follow local HUD imports so future extractions cannot silently disappear from a share."""
    pending = list(REQUIRED_OVERLAY_SCRIPTS)
    found: set[str] = set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        source = ROOT / "scripts" / name
        if not source.is_file():
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"), filename=name)):
            modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                       else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                local = module.split(".")[0]
                if not local.startswith("overlay_"):
                    continue
                filename = local + ".py"
                # Shared backend helpers are already copied by the backend's Python-file rule.
                if not (ROOT / "scripts" / filename).is_file() and (ROOT / "iRacing_Backend" / filename).is_file():
                    continue
                pending.append(filename)
    return found


def root_bat_scripts() -> set[str]:
    """Files in the scripts folder that the root bat files (설치.bat, 실행.bat) run."""
    found = set()
    for name in ROOT_FILES:
        if name.endswith(".bat") and (ROOT / name).is_file():
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
            found.update(re.findall(r"scripts[\\/]([\w.-]+\.(?:bat|py))", text, flags=re.IGNORECASE))
    return found


def validate_share_inputs() -> None:
    """Fail before copying if a known runtime part would be missing from a share."""
    missing = [name for name in sorted(root_bat_scripts())
               if name not in SCRIPTS or not (ROOT / "scripts" / name).is_file()]
    if missing:
        raise RuntimeError("설치.bat·실행.bat이 쓰는 파일이 공유본 목록에 없습니다: " + ", ".join(missing))
    missing = [name for name in sorted(overlay_runtime_scripts())
               if name not in SCRIPTS or not (ROOT / "scripts" / name).is_file()]
    if missing:
        raise RuntimeError("공유본에 필요한 오버레이 파일이 없습니다: " + ", ".join(missing))
    requirements = ROOT / "iRacing_Backend" / "requirements_core.txt"
    try:
        lines = requirements.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeError(f"핵심 패키지 목록을 읽을 수 없습니다: {requirements}") from error
    if not any(line.strip().lower().startswith("pillow") for line in lines):
        raise RuntimeError("오버레이에 필요한 Pillow가 requirements_core.txt에 없습니다.")


def zip_share(out: Path) -> Path:
    """The full installer: the share folder as one zip beside it, with the folder itself at the top."""
    import zipfile
    import make_patch
    target = out.with_name(out.name + ".zip")
    partial = target.with_name(target.name + ".part")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(out.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                stored = path.suffix.lower() in make_patch.STORED   # already compressed: mp3, png, fonts
                archive.write(path, (Path(out.name) / path.relative_to(out)).as_posix(),
                              compress_type=zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    partial.replace(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "share" / "iracing-coach")
    parser.add_argument("--no-audio", action="store_true", help="받아 둔 음성을 넣지 않습니다")
    parser.add_argument("--zip", action="store_true", help="처음 설치하는 사람에게 줄 전체 설치본 zip도 만듭니다")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        print(f"이미 내용이 있는 폴더입니다: {out}\n비어 있는 폴더를 지정하거나 그 폴더를 직접 지운 뒤 다시 실행하세요.")
        return 1
    if not (ROOT / "build" / "index.html").is_file():
        print("빌드된 화면(build/)이 없습니다. 먼저 npm run build를 실행하세요.")
        return 1
    try:
        validate_share_inputs()
    except RuntimeError as error:
        print(f"공유본을 만들 수 없습니다: {error}")
        return 1
    out.mkdir(parents=True, exist_ok=True)
    for name in ROOT_FILES:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, out / name)
    for name in ROOT_FOLDERS:
        print(f"{name}/ 복사 중...", flush=True)
        copy_tree(ROOT / name, out / name)
    backend = ROOT / "iRacing_Backend"
    (out / "iRacing_Backend").mkdir()
    for path in sorted(backend.iterdir()):
        if path.is_file() and path.name not in BACKEND_SKIP and (path.suffix == ".py" or path.name.startswith("requirements")):
            shutil.copy2(path, out / "iRacing_Backend" / path.name)
    (out / "scripts").mkdir()
    for name in SCRIPTS:
        shutil.copy2(ROOT / "scripts" / name, out / "scripts" / name)

    source, data = app_settings.data_dir(), out / "data"
    data.mkdir()
    for name in DATA_FILES:
        if (source / name).is_file():
            shutil.copy2(source / name, data / name)
    clips = 0
    if not args.no_audio:
        for name, folder in (("radio_audio", source / "radio_audio"), ("spotter_audio", app_settings.spotter_audio_dir())):
            if folder.is_dir():
                print(f"{name} 복사 중...", flush=True)
                clips += copy_tree(folder, data / name, skip=lambda path: path.suffix.lower() != ".mp3")
    import make_patch
    (out / "처음 실행.txt").write_text(FIRST_RUN.replace("{version}", make_patch.version_of(out)), encoding="utf-8-sig")

    size = sum(path.stat().st_size for path in out.rglob("*") if path.is_file())
    print(f"\n공유본: {out}\n크기 {size / 1e9:.2f} GB · 음성 {clips:,}개")
    found = scan(out, personal_markers())
    if found:
        print("\n개인 정보로 보이는 내용이 남아 있습니다. 공유하기 전에 확인하세요:")
        for path, name in found:
            print(f"  {path}: {name}")
        return 2
    print("개인 정보 검사: 사용자 이름, 데이터 폴더 경로, Google 프로젝트·계정·키 ID, Garage61 토큰이 들어 있는 파일이 없습니다.")
    if not args.no_audio:
        # What this version contains, so a later update can be a small patch (scripts/make_patch.py).
        import make_patch
        kept = make_patch.record(out)
        print(f"버전 기록: {kept}" if kept else f"버전 기록은 처음 만든 것을 유지합니다: releases/{make_patch.version_of(out)}.json")
    if args.zip:
        print("전체 설치본 압축 중...", flush=True)
        archive = zip_share(out)
        print(f"전체 설치본: {archive} ({archive.stat().st_size / 1e9:.2f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
