# LMU 연동 개발: 집 PC에서 이어하기

개발 브랜치: `feature/lmu-telemetry-isolation`

이번 단계는 **기존 iRacing을 그대로 실행할 수 있도록 LMU 파일 읽기와 실행 환경을 분리한 첫 단계**입니다. LMU 주행 분석이나 실시간 코칭이 완성된 버전은 아닙니다. `main` 병합·태그·릴리즈는 하지 않습니다.

## 분리 구조

| 항목 | iRacing | LMU |
| --- | --- | --- |
| 진입점 | 기존 `backend_api.py` / `실행.bat` | `lmu_api.py` |
| 로컬 포트 | `24157` | `24158` |
| Python 환경 | 기존 설치 환경 | 별도 `.venv-lmu` 권장 |
| 설치 목록 | 기존 `requirements*.txt` | `requirements_lmu.txt` |
| 파일 읽기 | 기존 IBT reader | `lmu_telemetry.py`, `.duckdb` 전용 |
| 파일 경로 | 기존 iRacing 설정 | `LMU_TELEMETRY_DIRS` |
| 실시간 수집 | 기존 `live_telemetry.py`, iRacing SDK | `lmu_live.py`, 현재 `NOT_IMPLEMENTED` |
| 상태·조회 API | 기존 API | `/api/lmu/...`, 별도 앱에만 등록 |
| 설정·캐시·개인 DB | 기존 저장소 | 현재 쓰기 기능·영구 저장소 없음 |

LMU 모듈은 iRacing 설정·DB·SDK·음성 서비스에 접근하지 않습니다. LMU 실행에는 Node.js·Garage61·Google Cloud 키·음성팩이 필요하지 않습니다. 이 로컬 서버는 사용 중인 PC에서만 실행하며 외부 서버 운영은 필요하지 않습니다.

## 가져오기와 실행

기존 작업 폴더에 미커밋 변경이 있으면 먼저 별도로 보존하세요. 브랜치가 원격에 push된 뒤 새 폴더에서 시작하는 경우:

```powershell
git clone --branch feature/lmu-telemetry-isolation https://github.com/elsalvadoruk-hue/racingcoach.git racingcoach-lmu
cd racingcoach-lmu
git config core.hooksPath .githooks
py -3.12 -m venv .venv-lmu
.\.venv-lmu\Scripts\python.exe -m pip install -r iRacing_Backend/requirements_lmu.txt
$env:LMU_TELEMETRY_DIRS='D:\SteamLibrary\steamapps\common\Le Mans Ultimate\UserData\Telemetry'
.\.venv-lmu\Scripts\python.exe -m uvicorn lmu_api:app --app-dir iRacing_Backend --host 127.0.0.1 --port 24158
```

Python 3.12가 없다면 설치된 Python 3.10 이상으로 가상환경을 만드세요. 경로는 집 PC의 실제 Steam 라이브러리 위치로 바꾸세요. `LMU_TELEMETRY_DIRS`는 **이 PowerShell 창의 프로세스에만** 적용되며 새 창에서는 다시 지정합니다. 지정하지 않으면 `ProgramFiles(x86)`와 `ProgramFiles` 아래 기본 Steam 경로만 찾습니다. 다른 드라이브의 라이브러리는 자동 탐색하지 않습니다.

### 원격 push가 불가능할 때

GitHub 쓰기 권한이 없는 계정으로 인증된 PC에서는 `403`으로 push가 실패할 수 있습니다. 로컬 브랜치와 커밋은 그대로 유지됩니다. 권한 있는 계정으로 인증한 뒤 `git push -u origin feature/lmu-telemetry-isolation`을 실행하면 됩니다.

Git bundle로 옮기는 경우 회사 PC에서 아래처럼 파일을 만들고 집 PC로 가져갑니다. 이 번들은 `main` 기준 커밋이 필요한 증분 번들입니다.

```powershell
git bundle create racingcoach-lmu.bundle feature/lmu-telemetry-isolation ^origin/main
git bundle verify racingcoach-lmu.bundle
```

집 PC에서 새 저장소를 복제한 뒤 가져옵니다. `D:\Transfer`는 번들을 복사한 실제 위치로 바꾸세요.

```powershell
git clone https://github.com/elsalvadoruk-hue/racingcoach.git racingcoach-lmu
cd racingcoach-lmu
git fetch D:\Transfer\racingcoach-lmu.bundle feature/lmu-telemetry-isolation:feature/lmu-telemetry-isolation
git switch feature/lmu-telemetry-isolation
```

이후 위의 가상환경 설치부터 이어갑니다. 번들에는 커밋된 소스 변경만 포함되며 로컬 주행 파일·개인 설정은 포함되지 않습니다.

여러 폴더를 검사하려면 Windows에서 `;`로 구분합니다. 타인에게 직접 받은 파일은 별도 폴더에 보관하고 그 폴더를 추가할 수 있습니다. 아직 레퍼런스 선택·비교 기능에는 연결되지 않습니다.

```powershell
$env:LMU_TELEMETRY_DIRS='D:\SteamLibrary\steamapps\common\Le Mans Ultimate\UserData\Telemetry;D:\LMU-References'
```

브라우저에서 **http://127.0.0.1:24158** 을 엽니다. 연결 상태 → 파일 선택 → 파일 검사 순서로 확인합니다. 종료는 실행 창의 `Ctrl+C`입니다. 기존 iRacing은 기존 `실행.bat`으로 별도 실행할 수 있습니다.

`[Errno 10048]`이 나오면 다른 프로그램이 해당 포트를 사용 중입니다. 기존 프로그램을 종료하지 말고 명령의 `--port 24158`을 `--port 24168`처럼 빈 포트로 바꾼 뒤 브라우저 주소도 맞추세요.

## 이번 단계에서 가능한 것

- 지정한 폴더의 `.duckdb` 목록 조회. `.ibt`·`.duckdb.wal`은 읽지 않습니다.
- 선택한 파일만 읽기 전용으로 열어 메타데이터·채널 이름·단위·주파수·이벤트·샘플 수 확인.
- 게임의 연속된 `Lap` 이벤트 시각으로 양쪽 경계가 있는 랩을 표시. 파일 앞뒤의 미완성 구간은 완주로 만들지 않습니다.
- `Lap Time`을 해당 종료 시각으로 연결. 기록 누락·시간 불일치·피트 포함·샘플 부족·큰 거리 역행을 진단.
- 동명 파일도 폴더별 식별자로 구분. SteamID와 CarSetup은 API에 전달하지 않습니다.

`Lap Dist` 리셋을 랩 경계로 쓰지 않으며, 단위나 스티어링 각도를 값 범위로 추측하지 않습니다. 파일이 잠겨 있거나 기록 중이면 종료한 기록으로 다시 검사하세요. 파일·DB·iRacing 설정에 쓰지 않고 외부 확장 로딩도 비활성화합니다.

### 랩 상태 해석

| 상태 | 뜻 |
| --- | --- |
| `VALIDITY_UNKNOWN` | 경계 간 시간과 기록 시간이 일치하지만 게임의 유효 랩 판정은 미확인 |
| `UNTIMED` | 해당 종료 시각에 양수인 `Lap Time`이 없음 |
| `PIT_LAP` | 피트 진입 상태가 포함됨 |
| `TIMING_MISMATCH` | 경계 간 시간과 기록 시간이 다름 |
| `INCOMPLETE` | 거리 샘플이 부족하거나 기록 범위를 벗어남 |
| `DISTANCE_DISCONTINUITY` | 랩 내부에 큰 거리 역행·리셋이 있음 |

이 상태는 모든 이상을 판별하는 분석기가 아닙니다. 진단 하나만 대표로 표시할 수 있습니다. 컷·패널티·포메이션·텔레포트 등의 유효성 판정은 구현되지 않았습니다. 따라서 모든 랩의 `valid`는 `null`, `best_lap_time_s`는 `null`이며 유효 베스트 랩을 자동 선택하지 않습니다.

## 다음 작업 순서

1. 집 PC의 LMU에서 기록한 파일로 스키마·단위·시각 기준 확인. 정상 랩, 컷 무효 랩, 아웃/인 랩, 기록 중단 구간을 각각 검사합니다.
2. 게임의 유효 랩 판정 근거를 확인한 뒤 **같은 차량·트랙·레이아웃의 자기 최단 유효 랩**을 기준으로 선택합니다. 타인에게 받은 파일은 별도 명시 선택으로 연결합니다. 드라이버·세션 조건·파일 출처도 구분해야 합니다.
3. LMU 전용 정규화·비교 분석기를 추가합니다. 조향 단위·시간축·트랙 구간을 검증한 뒤 기존 분석 표현과 연결합니다.
4. 설치된 게임의 공식 shared-memory 헤더·버전을 확인하고 `lmu_live.py`에 LMU 수집기를 구현합니다. 현재 shared memory에 연결하지 않으며 iRacing SDK로 대신 연결하지 않습니다.
5. 실제 주행에서 수집 주기, 연결/해제, 세션 전환, 플래그, 피트, 상대 차량, 음성/코칭을 순차 검증합니다. 새 저장 기능도 LMU 전용 경로에 둡니다.

MyLMU 자동 수집·유료 서비스·외부 서버 운영은 현재 설계에 포함하지 않습니다.

## 검증

LMU 전용 환경의 개발 검증:

```powershell
.\.venv-lmu\Scripts\python.exe -m pip install -r iRacing_Backend/requirements_lmu_dev.txt
.\.venv-lmu\Scripts\python.exe -m unittest discover -s iRacing_Backend -p 'test_lmu_*.py' -v
```

합성 테스트는 읽기 전용 유지, 타임스탬프 매칭, 미완성·누락·불연속 데이터, 의존성 누락, 잘못된 파일, 게임 간 import 분리, 동명 파일 및 API 오류를 확인합니다. 기존 iRacing 환경에는 DuckDB를 추가할 필요가 없으며 DuckDB가 없으면 관련 파일 읽기 테스트는 건너뜁니다.

회사 PC에서 추가로 [oasis4/LMU-telemetry](https://github.com/oasis4/LMU-telemetry/tree/2c81e3645410b33bfff4037fa28b077ea0a3654e/tests/fixtures)의 공개 테스트용 기록 9개를 저장소 밖에서 읽었습니다. 모두 읽기에 성공했고 읽기 전후 SHA256이 같았습니다. Monza 파일의 경계 간 시간은 `131.085 / 116.560 / 111.000`초, 미완주 파일은 0랩이었습니다. 이 자료는 호환성 확인용이며 빠른 드라이버의 기준 랩이나 실제 LMU 구동 검증을 뜻하지 않습니다. 외부 소스 코드나 기록 파일은 이 저장소에 복사하지 않았습니다.

### 2026-10-20 회사 PC 검증 결과

| 검사 | 결과 |
| --- | --- |
| LMU 전용 테스트 | 기존 환경 및 새 가상환경에서 각각 22/22 통과 |
| 별도 설치 | `requirements_lmu_dev.txt`만 설치한 새 Python 3.14 환경에서 통과; Python 3.12 실검증은 아직 없음 |
| 기존 코드 보존 | iRacing 진입점·실시간 수집기·기존 설치 목록·React 소스·npm 설정은 `origin/main`과 동일 |
| 백엔드 전체 | 733개 중 732개 통과, 기존 `test_speech_config_uses_korean_and_vocabulary_with_valid_wav`는 Google Cloud 프로젝트 미설정으로 오류. 변경 전 711개 검사에서도 같은 오류 확인 |
| 스크립트 전체 | 49/49 통과 |
| 기존 화면 테스트 전체 | 336개 중 335개 통과. `useAudioOutput.test.js`의 요청 개수 검사 1개 실패 |
| 위 화면 테스트 단독 재검사 | 36/36 통과. 전체 검사를 모두 통과했다고 보지는 않음 |
| 기존 화면 빌드 | `npm run build` 통과 |
| LMU 브라우저 확인 | 파일 목록·선택·검사·3랩 표시·미확인 유효성 안내·가로 넘침 없음 확인 |

LMU 파일 읽기와 환경 분리까지 검증한 결과입니다. 집 PC에서는 실제 게임 기록·실시간 수집 검증부터 이어갑니다.

커밋 전 `python scripts/audit_git.py --staged`, push 전 `python scripts/audit_git.py --history`를 실행합니다. `.duckdb`와 WAL 파일도 개인 기록으로 Git에서 제외하고 감사에서 차단합니다. 실제 주행·실시간 수집·분석 결과의 정확성은 아직 검증되지 않았습니다.
