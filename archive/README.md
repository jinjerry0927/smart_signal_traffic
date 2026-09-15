# 지난 학기 자료 보관

현재 개발 범위는 [프로젝트 차터](../docs/project_charter.md)와 [진행 상태](../STATE.md)를 따른다.
이 폴더의 문서·도구는 당시 맥락을 보존한 자료이며 현재 작업 지시가 아니다.

| 위치 | 보관 이유 |
|---|---|
| semester1/project_charter.md | 2026-06-02 기준 차터 원본과 당시 결과·결정 |
| perception_track/ | CCTV 인지 실험, 한계 분석, 경주 금장교 도로 초안 |
| v1/ | 구 DQN 코드·최종 모델·학습 로그·평가 결과 |
| training_history/ | 학습 실패와 이전 실행 기록 |
| pres9_v1/, pres11/ | 지난 학기 발표 생성 코드와 자료 |
| legacy_tools/claude/ | 이전 작업 큐를 사용하던 Claude 명령 원본 |
| local_cache/ | 정리 시 보관한 로컬 캐시; Git에서는 제외 |

v1/old_checkpoints/의 100~900회 중간 가중치는 로컬에 보존하고 현재 Git 추적에서 제외했다.
필요하면 정리 이전 Git 이력에서도 복구할 수 있다. v1 최종 모델과 원본 결과는 계속 추적한다.
perception_track/의 다운로드된 YOLO 가중치 역시 기존과 같이 로컬 자료다.

옛 문서의 실행 경로·하이퍼파라미터·성능 주장은 당시 상태를 나타낸다.
특히 pres11/build.js에는 과거 PC의 출력 경로가 남아 있어 현재 실행 도구로 사용하지 않는다.
