# Realistic V2 검증 기록

실행일: 2026-11-03. Python 3.11.0 / NumPy 2.4.4 / sumolib·traci 1.26.0.
**SUMO·sumo-gui 프로세스 실행 0회, 시뮬레이션 0회, 학습 0회, 브라우저 자동 시연 0회.**

## 작업 기준과 보존

작업 시작 브랜치는 `codex/traffic-map-quality-20261102`, HEAD는 `959e231`이었다.
로컬 `master`도 같은 커밋이고 로컬 원격 추적 `origin/master`는 `ea32af2`였다.
이전 통합·정리·지도 작업의 다수 미커밋 변경이 이미 있어 현재 작업 트리에서 이를 보존했다.
구현 단계에서는 브랜치 전환·reset·commit·push·원격 파일 전송을 하지 않았다.
후속 GitHub 게시 단계는 [게시 기록](realistic_v2_publish_20261103.md)을 따른다.

작업 전 `results/`, 기존 `network/`, `transferable_signal/`, `configs/`의 1,516개 파일을 SHA256으로 기록했다.
작업 종료 시 삭제/변경 0개다. 새 `network/realistic_v2/` 파일은 별도 생성했다.
정렬된 경로→SHA256 맵의 집계 해시는 전후 모두:

```text
8f54338bb53e607924bbc2376e281c6520a6fc1436030f002638234978dbbbd0
```

로컬 근거:
[작업 전 해시](../.codex-artifacts/realistic_v2_20261103/preserved_before.json),
[작업 후 대조](../.codex-artifacts/realistic_v2_20261103/preservation_after.json),
[최종 단위 검사 로그](../.codex-artifacts/realistic_v2_20261103/final_unit_tests.log).
이 경로는 로컬 검사 자료이며 동결 연구 결과 디렉터리에 쓰지 않았다.

## 실행한 정적 검사

| 검사 | 명령/방법 | 결과 |
|---|---|---|
| V2 생성 | `python -X utf8 -B -c "from realistic_signal.build import build; build()"` | 별도 출력 생성, netconvert Success; SUMO 호출 없음 |
| V2 계약·수요·동결 추론 | `python -X utf8 -B -m realistic_signal check` | 교차로 3, 그룹 4/5/5, 이동 36, 수요 3; 통과 |
| Python 문법 | 변경·추가 Python 12개 파일에 `compile(text, path, 'exec')` | 통과, pyc 생성 없음 |
| 웹 문법 | Node에서 두 HTML inline script를 `new Function`으로 컴파일 | 두 페이지 통과; DOM 실행/브라우저 없음 |
| CLI 일치 | `python -X utf8 -B -m realistic_signal run --help`, `python -X utf8 -B -m signal_demo web --help` | 안내 옵션과 구현 일치 |
| 차이 공백 | `git diff --check -- signal_demo/runtime.py signal_demo/web.py signal_demo/__main__.py signal_demo/dashboard.html README.md STATE.md` | 오류 없음, 기존 Windows 줄끝 변환 안내만 출력 |
| 보존 대조 | 작업 전 SHA256 맵과 모든 파일 재해시 비교 | 1,516/1,516 일치 |

`netconvert`는 도로망을 생성하는 오프라인 변환기이며 차량 시뮬레이터 `sumo`/`sumo-gui`는 호출하지 않았다.
새 망의 TLS 인덱스/내부 request 인덱스에 따른 상충, 공유차로 동일 등화,
각 이동의 녹색 제공·정지 가능, 버스/트럭/승용차의 기존 OD 연결을 파일에서 검사했다.

## 단위·회귀 검사

```powershell
python -X utf8 -B -m unittest discover -s tests -p test_realistic_signal.py
$env:PYTHONPATH = 'tests'
python -X utf8 -B -m unittest test_signal_demo test_presentation_readiness test_map_visualization test_transferable_default_cli test_transferable_adaptive_v2 test_gyeongju_simple
```

개별 실행: 신규 16검사 통과, 기존 관련 68검사 통과.
최종 결합 실행: 같은 7개 모듈을 `unittest.defaultTestLoader.loadTestsFromNames`로 로드하여
**84검사 / 11.157초 / 실패 0·오류 0**.
기존 지도 회귀 안에서 Node의 기하·차량 보간·이동별 등화 8검사도 통과했다.
기존 Pi는 PC loopback HTTP 300행동·진단 일치를 검사했으며 실물 Pi 시험이 아니다.

최종 결합 실행에는 Python `sys.addaudithook`으로 실제 `sumo`/`sumo-gui` 하위 프로세스를 거부하는
감시를 추가했다. Windows 실행 이벤트 형식에 대한 자체 검사 후 실제 SUMO 프로세스 시도 0건을 확인했다.
초기 감시 구현에서는 `Popen` 교체가 asyncio 상속을 깨뜨렸고,
이어 Windows 이벤트의 executable=None 처리가 누락되어 일반 장비 정보/Node 호출도 실패했다.
감시 도구를 수정한 뒤 84검사를 다시 실행해 통과했다. 초기 실패를 제품 검사 통과로 계산하지 않았다.
[실패 감시 로그](../.codex-artifacts/realistic_v2_20261103/failed_windows_guard.log)도 보존했다.

신규 검사 범위:

- 실제 파일의 그룹 유효성·상충 보호 녹색 차단·공유차로 차등 등화 거부·이동 서비스 누락 거부.
- 원본 OD/수요 보존과 N/S 안쪽 차로 좌회전 전용화.
- 최소녹색, 잘못된 행동/형식 거부, 전환 중 목표 잠금.
- 정확히 3초 황색+2초 소거, 중복 직진 유지, 신규 상충 이동 지연.
- 현시 변경을 통한 연속녹색 리셋 차단, 최대녹색 강제 전환, 최장 미서비스 우선.
- 합법 전환 조합과 유지/무작위 선택의 순수 Python 시계 검사.
- 원본 가중치의 4/5후보 유한 점수·합법 행동과 세 기준선의 동일 마스크 사용.
- 망/spec 변조 거부, 실제 연결 인덱스 지도 전달, V2 Pi 전송 사전 거부.
- 가짜 TraCI 연결의 CLI 100초·웹 작업 경로 30초 정수 시각 진행과 300/90행 기록 재생.
- 저장 신호 변조와 차량 회계 불일치 검출.

위의 가짜 연결 시간은 테스트용 Python 정수다. **SUMO 시험 운행이나 차량 성능 지표가 아니다.**

## 별도 핵심 검토

구현 후 전환·관측·저장 연결을 다시 읽고 다음을 확인했다.

1. 종료 이동과 유지 녹색은 원래 안전 그룹의 부분집합이며, 모든 프레임에 황색 포함 상충 검사를 적용한다.
2. 새 녹색은 종료 이동 황색/소거가 끝나야 시작하고, 공통 녹색 타이머는 현시 이름 변경으로 초기화하지 않는다.
3. 관측·압력은 해당 그룹에서 실제 허용하는 이동과 차로를 사용하며 새 행동 의미를 Legacy 모델 검증으로 표기하지 않는다.
4. V2 실행은 별도 환경 클래스를 선택하고 실제 프레임 전에는 적용 완료로 표시하지 않는다.
5. Legacy 원격 프로토콜과 동결 SHA256 검사를 변경하지 않았다.
6. 감사는 완료 실행만 통과시키고 초별 요청/상태, 기간, 차량 보존 회계, tripinfo 도착 수를 대조한다.

## 미실행과 제한

- `sumo --version`을 포함한 SUMO 실행, 0초 로드, 짧은 운행, 전체 통합 도구, 브라우저 시연을 수행하지 않았다.
- 신규망 `--unseen` 통합 검사는 봉인 fingerprint가 SUMO 버전 프로세스를 호출하므로 이번 실행에서 제외했다.
  관련 소스·입력·해시 및 `unseen_dashboard.html`의 바이트는 유지했다.
- 실제 차량의 lane-change/좌회전 포켓 용량/역류/미진입/충돌/teleport/지체는 미검증이다.
- 모델의 V2 적합성·성능 향상·재학습 불필요, V2 Pi, 보행자와 조건부 적색 우회전은 검증 완료가 아니다.
- 사용자의 운행 후 `realistic_signal audit --run 개별실행폴더`와 로그 회수는 [직접 실행 안내](realistic_v2_manual.md)를 따른다.

다음 작업: 사용자가 Fixed로 현시와 차로를 관찰하고 quiet/busy/directional 실행 원본을 제공한다.
그 뒤 안전·회계를 먼저 검토하고 AI/기준선 비교 및 재학습 여부를 별도 결정한다.

## GitHub 게시본 재검증

최신 `origin/master` 기반 별도 작업 트리에서도 84검사와 `realistic_signal check`가 통과했다.
SUMO 프로세스 시도는 0건이다. [게시본 검사 로그](realistic_v2_evidence/publish_unit_tests.txt),
[구현 전후 보호 해시 요약](realistic_v2_evidence/implementation_preservation.json)을 함께 게시한다.
위 `.codex-artifacts` 상세 링크는 원래 PC의 로컬 자료이며 GitHub 배포 파일에 포함하지 않는다.
