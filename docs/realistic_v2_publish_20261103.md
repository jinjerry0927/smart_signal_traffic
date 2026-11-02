# Realistic V2 GitHub 게시 기록

2026-11-03, 사용자의 별도 커밋·푸시 요청으로 준비했다.
기준은 최신 `origin/master` (`ea32af2`), 게시 브랜치는 `codex/realistic-v2-20261103`이다.

## 커밋 범위

1. `feat: integrate web decision diagnostics and shared traffic map`: V2가 사용하는 기존 미게시 웹·판단 상세·지도 코드와 관련 검사. 기존 Pi 동작은 그대로 지원한다.
2. `feat: add isolated Realistic V2 signal control`: 독립 망·계약·이동별 전환기·관측·CLI·웹 선택·검사·문서. Windows 재체크아웃에서 해시가 바뀌지 않도록 V2 파일의 줄바꿈을 보존한다.

원래 작업 트리의 대규모 정리 삭제, 발표 자료, 임시 폴더 및 관련 없는 미커밋 변경은 포함하지 않았다.
원격 master의 이력과 신규 교차로 기능을 보존하고, feature 브랜치로 게시한다.
동결 모델·정책·기존 네트워크·실험 결과·학습/평가/봉인 검증 소스는 origin/master 대비 변경하지 않았다.

## 검증과 인계

최신 master에서 필수 의존성 68검사, V2 포함 84검사, `python -X utf8 -B -m realistic_signal check` 통과.
검사는 SUMO 프로세스 실행을 거부하는 감사 훅 아래 수행했고 실제 SUMO 시도는 0건이다.
[검사 로그](realistic_v2_evidence/publish_unit_tests.txt)를 보존했다.

이번 게시도 SUMO·학습·브라우저 시연을 수행하지 않았다.
V2 모델 성능·차량 운행·현장 안전은 미검증이며, 보행자·조건부 적색 우회전·V2 Pi는 미구현이다.
[직접 실행 안내](realistic_v2_manual.md)에 따라 사용자가 후속 운행과 로그 감사를 수행한다.
