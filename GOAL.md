# G0 — 기존 Smart Signal 재현성과 측정 기준 점검

상태: PLANNED. 실제 Codex 지속 목표는 아직 시작되지 않았다.
운영 규칙: [LOOP.md](LOOP.md). 통합 상태: [STATE.md](STATE.md).

저장소 정리 중 기본 실행 검사는 완료했다. [정리 기록](docs/repository_cleanup_2026-09-13.md)을 먼저 읽고,
다음 실행에서는 남은 황색 신호 경고·제어 규칙·평가 의미 점검을 이어간다. G0 전체가 완료된 것은 아니다.

## 목표

기존 구현의 실행 상태와 관측·행동·평가 의미를 현재 환경에서 확인하고,
다중 교차로 확장 전에 필요한 후속 작업을 근거와 함께 정리한다.

## 담당과 권장 모델

- 담당: 총괄 또는 기존 기능 점검 채팅 하나.
- 첫 설계·측정 의미 검토: Astra Extra High 권장.
- 원인이 명확해진 작은 후속 구현은 별도 목표로 나눠 Sol High에 넘길 수 있다.

## 먼저 읽을 파일

- README.md, requirements.txt, docs/project_charter.md
- smart_signal.py, e2_observation.py, evaluate.py, baselines.py
- demo.py, edge_server.py, dashboard.py
- test_env.py, tests/test_e2_observation.py, tests/test_inference_parity.py, tests/test_dashboard.py

## 범위

- Python·SUMO·필수 라이브러리 버전, 모델·네트워크·시나리오 파일 존재 확인.
- 기존 검증 스크립트의 내용과 부작용을 확인한 뒤 실행.
- 기본/E2 관측값, Keep/Next 의미, 신호 전환 규칙의 실제 적용 점검.
- avg_waiting_time, throughput 등 평가 값의 계산 단위와 집계 방식 점검.
- 현재 학습·데모·평가 설정 차이와 재현에 필요한 조건 정리.
- 재사용 가능한 부분과 별도 수정·설계가 필요한 부분 분류.
- 결과 보고서와 STATE.md 업데이트.

신규 도로망·웹 앱 구현, 알고리즘 교체, 장시간 재학습은 후속 목표로 다룬다.
이 점검 목표에서 발견한 소스 결함은 재현 근거와 수정 목표를 남긴다.

## 검증 절차 후보

프로젝트 루트에서 사용 가능한 Python 실행기를 확인한다.
아래 스크립트의 기본 실행은 정리 과정에서 통과했다. 변경이나 새로운 우려가 있으면 필요한 검사를 다시 실행한다.

```powershell
python test_env.py
python tests/test_e2_observation.py
python tests/test_inference_parity.py
python tests/test_dashboard.py
```

스크립트마다 필요한 파일·포트·GUI·의존성을 먼저 확인한다.
사용한 실행기, 명령, 종료 코드와 핵심 결과를 기록한다.
실행이 안 되면 정확한 오류와 다음 조치를 남긴다. 검증 불가를 성공으로 처리하지 않는다.
실물 Pi가 없어도 로컬 추론 일치를 확인할 수 있는지 조사하고, 실물 검증 여부는 구분한다.

## 완료 조건

- [ ] 현재 환경, 확인된 실행 명령과 의존 조건을 기록했다.
- [ ] 각 기존 검증의 PASS/FAIL/NOT RUN과 근거를 남겼다.
- [ ] 관측·행동·신호 전환과 평가 지표의 의미를 코드 근거로 정리했다.
- [ ] 실제 확인과 문서·주석의 주장, 아직 확인하지 못한 내용을 구분했다.
- [ ] 재사용·수정·추가 설계 목록과 다음 작은 목표를 정리했다.
- [ ] 실행 보고서를 docs/g0_reproduction_report.md에 작성하고 STATE.md에 연결했다.

docs/g0_reproduction_report.md는 이 목표의 예정 산출물이다. 현재 생성된 보고서가 아니다.
G0는 점검 목표이므로 결함을 명확히 재현하고 후속 목표로 기록해도 완료할 수 있다.
그 경우 시스템 전체가 정상이라는 의미로 완료를 표현하지 않는다.

## 접근 전환과 인계

같은 명령을 같은 조건에서 이유 없이 반복하지 않는다.
필수 환경·자료 부재를 해결할 수 없으면 가능한 점검을 마치고 정확한 미해결 조건을 인계한다.
별도 수정 목표의 완료 조건과 검사 방법을 작성한 뒤 이번 점검을 종료한다.
