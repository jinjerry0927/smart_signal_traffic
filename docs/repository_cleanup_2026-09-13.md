# 저장소 정리와 실행 점검

작업 시작: 2026-09-13. 마무리: 2026-09-16.
목적: 최종 프로젝트를 시작하기 전 자료·진입 문서를 정리하고 기본 실행을 검증한다.

## 변경

- 원격에만 있던 50c05b2 문서 변경을 fast-forward로 반영했다.
- README와 현재 차터를 3개 교차로·AI·웹·Pi·비교 실험 범위로 갱신했다.
- STATE/LOOP/GOAL과 AGENTS.md에 목표·검증 반복 및 채팅별 작업 기준을 기록했다.
- 지난 차터는 archive/semester1/, 발표 생성기는 archive/pres11/, 옛 도구 명령은 archive/legacy_tools/로 보관했다.
- 로컬 영상은 presentations/media/로 이동하고 PPT 4종과 원고는 presentations/에 보존했다.
- Python/pytest/차트 캐시는 archive/local_cache/2026-09-13/로 보관했다.
- .codex-artifacts/, 캐시, node_modules를 Git 제외 항목에 추가했다.
- 구 v1 중간 체크포인트 9개와 개인 VS Code 설정은 파일을 로컬에 유지하고 Git 추적만 해제했다.
- 현재 모델·실험 결과·수요·경주 도로 자료·로컬 학습 체크포인트는 보존했다.

처음의 복합 삭제·이동 명령은 자동 승인 검토에서 정책상 차단되어 실행되지 않았다.
대신 경로를 확인한 뒤 원본 보존 이동과 Git 추적 해제로 정리했다. 디스크 공간을 대량 삭제한 작업이 아니다.

## 실행 경로 수정

기존 test_env.py와 기본 데모·SUMO 설정은 Git에서 제외된 network/intersection.rou.xml에 의존했다.
저장된 scenarios/eval/medium.rou.xml을 기본 경로로 사용하도록 수정했다.
기본 검사는 GUI 없이 300초 실행하며 --gui와 --seconds로 조정할 수 있다.
기존 결과를 덮어쓰는 재학습·본 평가는 이번 정리에서 실행하지 않았다.

## 확인 환경

Python 3.11.0, SUMO 1.26.0, sumo-rl 1.4.5, PyTorch 2.11.0,
NumPy 2.4.4, Gymnasium 1.3.0, Matplotlib 3.10.9. 현재 PyTorch에서 CUDA 사용 불가.

## 검증 결과

| 검사 | 결과 |
|---|---|
| python -B test_env.py | PASS: 29D, 녹색 단계 4개, 300초/60 steps |
| python -B tests/test_e2_observation.py | PASS: 53D, 관측 범위 |
| python -B tests/test_inference_parity.py | PASS: 100개 입력의 행동·Q값 일치 |
| python -B tests/test_dashboard.py | PASS: /state 응답 왕복 |
| sumo -c network/intersection.sumocfg --end 30 --no-step-log true | PASS: 저장된 경로 사용, 30초 종료 |
| 활성 Python 파일 AST 구문 검사 | PASS: 17개 파일 |
| 기존 자료 보존 비교 | PASS: 모델·결과·환경·수요 19개, 보관 원본 4개 |

2026-09-16에 git checkout-index로 커밋 대상 파일만 별도 폴더에 복사했다.
생성 경로 network/intersection.rou.xml이 없는 상태에서도 위 Python 검사 4개가 모두 통과했다.
이 재검증에는 콘솔 문자 출력을 위해 python -X utf8 -B를 사용했다.
운영 문서 10개의 내부 링크와 git diff --cached --check도 통과했다.

최초 바이트 비교는 Windows CRLF와 Git LF 차이로 일부 텍스트를 불일치로 판정했다.
Git의 경로별 정규화를 적용한 해시 비교에서 위 원본 내용이 유지됨을 확인했다.
모델 등 바이너리는 줄바꿈 정규화 대상이 아니다.

## 남은 점검

- SUMO가 center 신호의 phase 4/9 전환에서 Missing yellow phase 경고를 출력한다.
- 검사 통과는 신호 안전 제약과 기준선 공정성을 모두 검증했다는 뜻이 아니다.
- 기존 avg_waiting_time은 차량별 평균이 아니다. 지표 의미와 기준선 전환 로직을 G0에서 점검한다.
- 실물 Pi, 새 도로망, 새 AI 학습과 최종 성능 평가는 수행하지 않았다.
- 현재 evaluate.py는 결과 출력이 고정되어 있다. 새 실험 전에 출력 분리 작업이 필요하다.

다음 시작점은 [GOAL.md](../GOAL.md)의 남은 G0 점검이다.
