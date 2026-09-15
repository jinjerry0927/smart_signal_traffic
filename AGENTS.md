# Smart Signal 작업 규칙

- 한국어로 소통한다. 관련 파일과 현재 Git 상태를 먼저 확인한다.
- 최신 범위는 docs/project_charter.md, 진행 상태는 STATE.md를 기준으로 한다.
- 목표·검증 반복은 LOOP.md를 따르고, 실제 목표 실행을 요청받으면 해당 목표 명세를 읽는다.
- GOAL.md에 적힌 예정 목표를 파일을 읽었다는 이유만으로 실행하지 않는다.
- AI 신호제어가 중심이다. 경주 교차로 3개, 웹 관제, Pi 추론, 비교 실험이 현재 개발 기준안이다.
- archive/와 docs/superpowers/는 지난 학기 이력이다. 현재 구현 지시나 성능 증거로 간주하지 않는다.
- results/의 기존 모델·CSV·그림은 비교용 기준 자료다. 새 실험 출력은 분리하고 덮어쓰지 않는다.
- 동시 학습은 생성 경로 파일도 분리한다. 웹·SUMO·AI·Pi는 합의된 교차로 ID와 관측·행동 규칙을 공유한다.
- 변경은 작은 단위로 하고 실제 관련 검사를 실행한다. 미실행·실패를 통과로 기록하지 않는다.
- 검증 결과에는 명령, 핵심 결과, 남은 문제, 다음 작업을 남긴다.
- 사용자 파일을 보존한다. 커밋·푸시·외부 전송은 해당 작업에서 부여된 권한에 맞춰 수행한다.

기존 검사: python test_env.py, python tests/test_e2_observation.py,
python tests/test_inference_parity.py, python tests/test_dashboard.py.
자세한 시작 방법과 경로는 README.md를 참조한다.
