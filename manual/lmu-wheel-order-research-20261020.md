# LMU 휠 순서 근거 조사

조사일: 2026-10-20. 조사 범위는 설치된 SDK 헤더, Studio 397의 텔레메트리 기록 안내, 공개 LMU-Telemetry-Lab 소스입니다. 외부 코드를 실행하거나 설치하지 않았고 게임 설정·원본 데이터는 수정하지 않았습니다.

**SDK 배열의 순서는 확인됐지만 DuckDB exporter가 같은 순서로 `value1..4`를 저장한다는 직접 근거는 확인되지 않았습니다. DuckDB 휠 위치 매핑 상태는 `UNVERIFIED`로 유지합니다.**

| 대상 | 확인 결과 | 근거의 한계 |
| --- | --- | --- |
| SDK `TelemInfoV01.mWheel[4]` | FL, FR, RL, RR 순서 확인 | 공유 메모리 배열의 계약이며 DuckDB exporter의 계약은 아님 |
| 공식 DuckDB 기록 기능 | native 기록·채널 설정·주파수 설정 확인 | `value1..4`와 휠 위치의 대응 설명 없음 |
| LMU-Telemetry-Lab reader | `value1,value2,value3,value4` 순서로 배열을 읽음 | 제3자 reader 구현의 사실 |
| LMU-Telemetry-Lab 휠 의미 | 인덱스 `0,1,2,3`을 FL, FR, RL, RR로 사용 | exporter 구현·제조사 스키마·독립 검증 자료를 인용하지 않은 소비자 가정 |

## SDK에서 확인한 내용

설치 파일: `C:\Program Files (x86)\Steam\steamapps\common\Le Mans Ultimate\Support\SharedMemoryInterface\InternalsPlugin.hpp:340`

```cpp
TelemWheelV01 mWheel[4];       // wheel info (front left, front right, rear left, rear right)
```

이 주석은 설치된 SDK의 `mWheel` 순서만 확정합니다. 내부 exporter가 이 배열을 재정렬하지 않고 `value1..4`에 복사하는지는 이 선언으로 확인할 수 없습니다.

## 공식 기록 안내에서 확인한 내용

[Telemetry Recording](https://guide.lemansultimate.com/hc/en-gb/articles/14524956311695-Telemetry-Recording)은 PC 버전 1.2의 native DuckDB 기록과 `config.json`의 채널·주파수 설정을 안내합니다. `Wheel Speed`, `TyresPressure`, `TyresTempLeft/Centre/Right` 등 채널 이름이 나옵니다.

본문에는 `value1`, `front left`, `rear left`, `wheel order`, `mWheel`에 해당하는 휠 위치 매핑 설명이 없습니다. 채널을 기록할 수 있다는 설명과 휠 열의 의미가 문서화됐다는 주장은 구분해야 합니다. 확인한 본문은 `updated_at=2026-09-30T21:21:28Z`입니다.

## 공개 구현에서 확인한 내용

고정 커밋: [`8dd3302e49d63350e5346a4129591b3d93d727d8`](https://github.com/rabbit20031225/LMU-Telemetry-Lab/commit/8dd3302e49d63350e5346a4129591b3d93d727d8), 커밋일 `2026-10-02T17:21:23Z`.

1. [telemetry_service.py:711–718](https://github.com/rabbit20031225/LMU-Telemetry-Lab/blob/8dd3302e49d63350e5346a4129591b3d93d727d8/backend/app/services/telemetry_service.py#L711-L718)는 `wheel_cols = ['value1', 'value2', 'value3', 'value4']`로 열 순서를 지정합니다.
2. [연속 채널 읽기:833–846](https://github.com/rabbit20031225/LMU-Telemetry-Lab/blob/8dd3302e49d63350e5346a4129591b3d93d727d8/backend/app/services/telemetry_service.py#L833-L846)는 `df[wheel_cols].values`를 가져온 뒤 각 `w` 열을 그대로 보간합니다. [이벤트 읽기:763–791](https://github.com/rabbit20031225/LMU-Telemetry-Lab/blob/8dd3302e49d63350e5346a4129591b3d93d727d8/backend/app/services/telemetry_service.py#L763-L791)도 같은 열 순서를 보존합니다.
3. [휠 의미 적용:908–929](https://github.com/rabbit20031225/LMU-Telemetry-Lab/blob/8dd3302e49d63350e5346a4129591b3d93d727d8/backend/app/services/telemetry_service.py#L908-L929)는 주석 `FL(0), FR(1), RL(2), RR(3)`에 따라 타이어 안쪽·바깥쪽 온도를 계산합니다.
4. [types.ts:87](https://github.com/rabbit20031225/LMU-Telemetry-Lab/blob/8dd3302e49d63350e5346a4129591b3d93d727d8/frontend/src/types.ts#L87)도 `wheelIndex?: number; // 0:FL, 1:FR, 2:RL, 3:RR`라고 선언합니다. [차트 목록:12–21](https://github.com/rabbit20031225/LMU-Telemetry-Lab/blob/8dd3302e49d63350e5346a4129591b3d93d727d8/telemetry_charts_list.md#L12-L21)도 동일 인덱스를 사용합니다.

따라서 이 공개 reader가 `value1→FL`, `value2→FR`, `value3→RL`, `value4→RR`를 채택한 사실은 확인됩니다. 다만 이 프로젝트는 exporter의 원본 코드가 아닙니다. reader의 매핑이 공식 exporter 계약을 증명하지는 않습니다. 조사한 backend·Markdown 텍스트 24개에서 이 매핑을 공식 SDK 또는 exporter 스키마와 연결하는 출처도 찾지 못했습니다. 이는 조사 범위 안에서의 결과이며 인터넷 전체에 근거가 없다는 뜻은 아닙니다.

## 개발 적용

- 현재 DuckDB 표시는 `value1..4` 또는 `휠 채널 1..4`로 유지하고 위치 매핑은 `UNVERIFIED`로 표시합니다.
- 확인되지 않은 위치를 사용한 좌우·전후·타이어 안쪽/바깥쪽 판정은 확정 결과로 제공하지 않습니다.
- 매핑 승격에는 제조사의 exporter 열 순서 명세·직접 구현 근거, 또는 동일 세션에서 SDK 각 휠과 DuckDB 각 열을 시각적으로 동기화해 비교한 별도 검증이 필요합니다. 비교 시 단위·각속도 부호·native 기록 주파수·시간축 차이도 함께 확인해야 합니다.
- 이번 조사에는 새 주행이 필요하지 않았습니다. 실제 동기 대조를 검증 단계로 선택할 때 사용자에게 필요한 절차를 별도로 안내할 수 있습니다.
