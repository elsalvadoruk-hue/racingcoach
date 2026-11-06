<h1 align="center">iRacing 주행 코치</h1>

<p align="center"><strong>달린 기록을 살펴보고, 다음 주행에서 바꿀 한 가지를 찾으세요.</strong></p>

<p align="center">iRacing과 Le Mans Ultimate의 주행 기록을 분석하고<br>한국어 스포터·피트월·오버레이로 주행을 돕는 Windows 로컬 앱입니다.</p>

<p align="center"><a href="https://github.com/elsalvadoruk-hue/racingcoach/releases/latest"><strong>앱 다운로드</strong></a> &nbsp;·&nbsp; <a href="https://elsalvadoruk-hue.github.io/racingcoach/"><strong>매뉴얼 바로 읽기</strong></a> &nbsp;·&nbsp; <a href="https://elsalvadoruk-hue.github.io/racingcoach/download.html">HTML 매뉴얼 다운로드</a></p>

<p align="center"><sub>Windows x64 &nbsp; / &nbsp; iRacing · Le Mans Ultimate &nbsp; / &nbsp; 개인·비상업적 사용 무료</sub></p>

<p><img src="manual/img/readme-2.1.0-hero.png" alt="GT 레이스카와 텔레메트리 곡선을 표현한 앱 소개 이미지" width="100%"></p>

## 할 수 있는 일

| 주행 후 분석 | 주행 중 지원 | 연습과 셋업 |
| --- | --- | --- |
| 랩·코너별 시간 차이, 지도·고스트·입력 그래프 | 한국어 스포터·피트월, 기본 / 짧은 무전 개별 선택 | 다음 주행의 연습 과제와 한 항목씩 바꾸는 셋업 A/B |
| 내 기록 / Garage61 기준 비교 | 세션별 코칭 오버레이와 별도 타이어 숫자·음성 안내 | 드라이버 리포트, 입력 성향, 체감·도전·주행 기록 |

화면과 자세한 사용법은 **[매뉴얼 바로 읽기](https://elsalvadoruk-hue.github.io/racingcoach/)**에서 확인하세요. 기능별 활용 순서, 설정 항목의 의미·선택·저장 방법, 증상별 문제 해결을 확대 가능한 화면으로 안내합니다. [HTML 다운로드](https://elsalvadoruk-hue.github.io/racingcoach/download.html)는 그림까지 포함한 파일 하나이며, 저장한 뒤 브라우저로 열면 인터넷 없이 읽을 수 있습니다.

[처음 사용하기](https://elsalvadoruk-hue.github.io/racingcoach/#quick) · [음성·장치 설정](https://elsalvadoruk-hue.github.io/racingcoach/#audio) · [오버레이 설정](https://elsalvadoruk-hue.github.io/racingcoach/#overlay) · [타이어 설정](https://elsalvadoruk-hue.github.io/racingcoach/#tire-targets) · [증상별 문제 해결](https://elsalvadoruk-hue.github.io/racingcoach/#help)

<a name="getting-started"></a>

## 설치와 첫 실행

**[처음 설치용 ZIP 바로 받기 (2.5.2)](https://github.com/elsalvadoruk-hue/racingcoach/releases/download/v2.5.2/racingcoach-setup-2.5.2.zip)**

1. 처음 설치용 ZIP을 받아 **새 폴더에 압축을 모두 풉니다.** 다른 버전은 [최신 Releases](https://github.com/elsalvadoruk-hue/racingcoach/releases/latest)에서 `racingcoach-setup-*.zip`을 선택하세요.
2. `설치.bat`을 한 번 실행한 뒤 `실행.bat`으로 앱을 켭니다. 최초 설치에는 인터넷이 필요합니다.
3. **설정 → 업데이트 → 빠진 음성 받기**로 고정 안내 음성을 받습니다. 처음에는 약 0.85GB이며 기존 사용자는 추가분만 받습니다.
4. 상단에서 게임을 선택하고 **설정 → 설치 설정**에서 주행 파일 폴더를 확인합니다.

화면 주소는 **http://localhost:24157**입니다. 평소에는 `실행.bat`만 실행하고, 함께 열린 실행 창은 유지하세요. 처음 설치용 ZIP에는 Python과 완성된 화면이 포함되어 별도 Python·Node.js·PATH 설정이 필요 없습니다.

| 다운로드 파일 | 용도 |
| --- | --- |
| **racingcoach-setup-*.zip** | **처음 설치용.** Python과 완성된 화면이 포함됩니다. |
| racingcoach-app-*.zip | 기존 앱 업데이트용. Python은 없습니다. 기존 사용자는 앱 안의 업데이트 기능을 사용하세요. |
| Source code (zip / tar.gz) | 개발용 소스. Python과 완성된 화면이 없어 Python·Node.js가 필요합니다. |

기존 앱은 **설정 → 업데이트 → 업데이트 받기 → 지금 다시 시작해서 적용**으로 갱신합니다. 기존 데이터 폴더는 유지하세요. 자세한 이전·수동 설치 방법은 [설치 상세 안내](manual/설치-상세-안내.md)를 참고하세요.

## 지원 게임

| 기능 | iRacing | Le Mans Ultimate |
| --- | --- | --- |
| 주행 기록·랩·코너 분석 | IBT | DuckDB |
| 스포터·피트월 | 지원 | 게임에서 확인 가능한 항목 지원 |
| 마이크·F8 질문·AI 답변 | Google Cloud 연결 시 지원 | 미지원 |
| Garage61 기준 비교 | 지원 | 미지원 |
| 셋업 A/B 효과 비교 | 지원 | 미지원 |
| 추가 타이어 오버레이·음성 | GT3 11종·Porsche Cup 992.2 계기판 인식 | 공유 메모리 압력·표면 온도 |

iRacing의 **날씨 예보를 활용한 압력 추천·음성 안내는 게임 안 Weather(날씨) 패널의 오른쪽 예보 칸을 계속 띄워 두어야 합니다.** 게임이 걸친 모니터를 순서대로 찾고, 발견 뒤에는 해당 영역만 5분마다 읽습니다.

타이어는 **bar·°C**로 표시하며 숫자 표시와 음성을 따로 선택합니다. 차량·관측 조건과 근거가 부족하면 판단이나 추천을 보류합니다. [측정 조건과 안내 범위](manual/tire-voice.md)를 확인하세요. 개인 주행에서 반복되는 좋은 코너 통과의 온도·압력 조합을 학습해 온도(온도 표시가 없는 차량은 압력)를 초록색으로 표시하고 기존 음원으로 도달을 안내합니다. iRacing에서 지원하며 반복 근거가 부족하면 학습을 계속합니다.

Garage61은 **설정 → Garage61 기준·수집 → 가져올 데이터**에서 **조건에 맞는 데이터**(기본) 또는 **가장 빠른 데이터**를 선택합니다. 가장 빠른 방식도 같은 차량·트랙·레이아웃의 공개 후보 안에서 고르며, 날씨·연료 등 조건은 다를 수 있습니다. [수집 방식 안내](manual/garage61-collection.md)를 참고하세요.

## 비용과 데이터

- **기본 분석·고정 안내:** Google 키 없이 사용합니다. 스포터와 자동 보고는 AI를 호출하지 않습니다.
- **선택 서비스:** 마이크 음성 인식·AI 답변·새 문장 합성은 본인 Google Cloud 사용료가 발생할 수 있습니다. Garage61 수집은 본인 토큰이 필요합니다.
- **게임 연결:** SDK는 읽기 전용이며 게임 셋업을 자동으로 변경하지 않습니다. 확인하지 못한 수치는 추측하지 않습니다.
- **개인 기록:** 기록·설정은 로컬 데이터 폴더에 보관합니다. 녹음 원음과 개인 질문·답변 음성은 디스크에 저장하지 않습니다.
- **문제 제보:** 기록 → 세션 로그에서 해당 주행의 로그 ZIP을 받습니다. 자동 보내기는 전송 항목에 동의해 켰을 때만 동작합니다. 타이어 화면 이미지·OCR 인식 문자열·개인 DB 전체는 포함하지 않습니다.

## 도움말과 변경 내역

- [화면으로 보는 사용 매뉴얼](https://elsalvadoruk-hue.github.io/racingcoach/) · [오프라인 HTML 다운로드](https://elsalvadoruk-hue.github.io/racingcoach/download.html)
- [버전별 변경 내역](https://github.com/elsalvadoruk-hue/racingcoach/releases) · [패치노트](<패치 노트.txt>)
- [설치 상세 안내](manual/설치-상세-안내.md) · [개발 안내](manual/개발-안내.md)
- [문의·문제 제보](https://github.com/elsalvadoruk-hue/racingcoach/issues)

## 저작권과 이용조건

**개인·비상업적 사용은 무료입니다.** 본인 사용을 위한 백업·로컬 수정·빌드를 허용합니다. 소스가 공개되어 있어도 MIT 등 자유 재배포 라이선스가 적용되는 것은 아닙니다.

프로그램·소스·문서·음성팩의 재배포·재업로드·판매 및 다른 제품에 포함하는 행위는 사전 서면 허락이 필요합니다. 소개할 때는 공식 저장소 링크를 공유해 주세요. 외부 라이브러리·폰트·지도·상표의 권리는 각 권리자에게 있습니다.

Copyright © 2026 iRacing 주행 코치 제작자. [전체 이용조건](LICENSE.md)
