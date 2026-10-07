# 1세트 — 기존 기반 정비 완료 기록

기준일: 2026-09-16. 시작 코드 `f792914`, 결과는 현재 worktree의 미커밋 변경이다.
범위: 기존 단일 교차로 신호 제어, 학습·데모·평가 연결, 지표와 결과 보존.

## 완료한 세부 목표

| 단계 | 결과 | 확인 근거 |
|---|---|---|
| S1-1 진단 | 황색 누락·전체 적색 누락·최대 녹색 미적용 재현 | [이전 점검 기록](g0_reproduction_report.md) |
| S1-2 황색 수정 | 네 녹색 전환 모두 황색 후 전체 적색 적용, 기존 녹색 상태·순서 유지 | 정적 SUMO 실행, 신호 검사 |
| S1-3 공통 제어 | 학습·데모·평가가 같은 환경 사용, 기본29D/E2 53D 유지 | 매초 TraCI 전환·최소/최대 시간·reset 검사 |
| S1-4 기준선 정비 | Fixed/Webster가 실제 녹색 경과 시간 기준으로 전환 | 기준선 시간 검사, 5시나리오×4모드 실행 |
| S1-5 측정·보존 | 매초 지표와 완료 차량 통계 분리, 미도착·미진입 기록, 실행별 출력 | CSV 회계·입력 해시, 원본15개 해시 일치 |
| S1-6 검증·검토 | 관련 검사·짧은 학습/재개·평가 실행, 독립 검토 지적 해소 | 아래 실행 기록과 검토 결과 |

## 확정한 공통 규칙

- `CONTROL_VERSION = smart-signal-cyclic-v1`.
- 녹색 인덱스 0→1→2→3→0, 행동은 현재 단계 유지 또는 다음 단계 요청.
- 녹색 최소15초·최대60초. Keep을 계속 보내도 60초에서 다음 단계로 전환한다.
- 전환은 황색3초 → 전체 적색2초 → 다음 녹색. 우회전도 보호 구간을 따른다.
- 결정 간격5초. 전환 중 시간은 녹색 경과 시간에서 제외한다.
- 29D/53D 관측 차원은 유지한다. 최소 녹색 bit은 실제 녹색 경과 시간에 맞춘다.
- 기본29D는 녹색 one-hot4+최소녹색 bit1+차로 밀도12+대기열12다.
  E2 53D는 앞5개+12개 검지기의 대기열·밀도·정지수·점유율 각12개다.
- 보상은 기존 `diff-waiting-time`을 유지한다. 현재 차로별 누적 대기시간 합을100으로 나눈 값의 이전 대비 감소량이다.
- `green_phase`는 보호 구간 중 출발 단계, 새 녹색 시작 때 다음 단계로 바뀐다.
- 잘못된 액션은 SUMO 시간이 진행되기 전에 거부한다.
- 학습·평가 기본 teleport 기준은300초이며 데모의 기본값도 같다. 평가는 발생 횟수를 기록한다.

이는 이번 SUMO 실험의 제어 규칙이며 실제 도로의 신호 설계 적합성을 증명한 것은 아니다.
기존 모델은 차원상 로딩되지만 제어·관측 의미가 달라져 과거 성능이나 학습을 그대로 이어받지 않는다.
새 학습 체크포인트에는 제어 버전을 저장하며 다른 버전의 학습 재개를 거부한다.

## 공정 비교와 지표

평가는 저장된 flow 수요를 차량 ID·예정 출발 시각 목록으로 한 번 동결한다.
모드별로 같은 목록·SUMO seed·도로망·시간·신호 규칙을 사용한다.
실제 도로 진입 시점은 혼잡에 따라 달라질 수 있으므로 미진입과 미도착을 별도로 센다.
Webster는 시나리오 수요를 미리 알고 계산하는 근사 기준선이다.

| CSV 항목 | 의미 |
|---|---|
| mean_system_total_waiting_time_s | 현재 도로 위 모든 차량의 연속 정지 대기시간 합을 매초 측정한 평균 |
| mean_queue_vehicles / max_queue_vehicles | 매초 속도0.1m/s 미만 차량 수의 평균/최대 |
| mean_completed_waiting_time_s | 완료 차량의 여정 전체 대기시간 평균(tripinfo) |
| mean_completed_travel_time_s / mean_completed_time_loss_s | 완료 차량의 통행시간/시간손실 평균 |
| scheduled / departed / arrived_vehicles | 예정/실제 진입/도착 차량 수 |
| not_departed / departed_not_arrived_vehicles | 예정 중 미진입/진입 후 미도착 차량 수 |
| running / pending_vehicles_end | 종료 시 도로 위/진입 대기 차량 수 |
| teleport_events / collision_vehicle_events | 순간이동 시작 횟수/충돌 관련 차량 이벤트 수 |

완료 차량이 없으면 완료 차량 평균은 빈 값이다. 0초 평균으로 꾸미지 않는다.
기존 `avg_waiting_time`(결정 간격 표본)과 새 매초 평균은 이름뿐 아니라 표본 간격도 다르다.
CSV에 route 해시·제어 버전, manifest에 설정·입력/모델/소스 해시를 남긴다.
기존 `results/` 자료는 보존하고 새 기본 출력은 Git 제외된 `results/runs/`에 생성한다.

## 검증 명령과 결과

다음은 실제 실행한 명령이다. 지정 출력 폴더가 이미 있으면 새 이름으로 바꿔 재실행한다.

```powershell
python -X utf8 -B tests/test_signal_control.py
python -X utf8 -B tests/test_evaluation.py
python -X utf8 -B test_env.py
python -X utf8 -B tests/test_e2_observation.py
python -X utf8 -B tests/test_inference_parity.py
python -X utf8 -B tests/test_dashboard.py
python -X utf8 -B tests/inspect_signal_transitions.py --out .codex-artifacts/set1-signal-verified
python -X utf8 -B evaluate.py --episodes 1 --seconds 300 --out-dir ".codex-artifacts/set1 eval verified"
python -X utf8 -B demo.py --headless --mode webster --scenario medium --duration 40
python -X utf8 -B demo.py --headless --dashboard --dash_port 18010 --mode fixed --scenario medium --duration 40
python -X utf8 -B demo.py --headless --mode rl --scenario medium --duration 40
python -X utf8 -B smart_signal.py --episodes 1 --seconds 350 --out_dir .codex-artifacts/set1-train-smoke
python -X utf8 -B smart_signal.py --obs e2 --episodes 1 --seconds 20 --out_dir .codex-artifacts/set1-train-e2
python -X utf8 -B smart_signal.py --obs e2 --resume .codex-artifacts/set1-train-e2/checkpoint_e2_ep1.pth --episodes 2 --seconds 20 --out_dir .codex-artifacts/set1-resume-smoke
git diff --check
```

- 신호 검사 통과: 실제 황색3초·전체 적색2초·Next 최소15초·Keep 최대60초·순환·reset·잘못된 액션 거부.
- 기준선/동결/지표 단위 검사4개 통과. 기본29D, E2 53D, 추론100개 입력 일치, 대시보드 응답 검사 통과.
- 진단 원시 기록에서 황색 경고0, Next105초 동안 전체 적색10초, Keep 첫 녹색60초 확인.
- 평가20건(5시나리오×Fixed/Webster/기본AI/E2 AI, 각각300초) 모두 종료0, CSV20행·PNG 생성.
- 매건300개 초 표본, 모드별 같은 route 해시·seed·예정 차량 수, 예정=진입+미진입 및 진입=도착+미도착 확인.
- 이번 짧은 평가에서 teleport·충돌 차량 이벤트 모두0. 이것만으로 모든 수요에서 안전함을 주장하지 않는다.
- 기본 학습350초1회는 업데이트·모델 저장까지, E2 학습/재개는 각각20초로 연결·저장·로그 헤더 확인.
- 기존 출력 폴더 재사용2건과 구버전 학습 재개1건을 실제 거부했고 보호 파일 해시 유지.
- 원본 모델·CSV·PNG·수요15개 파일은 시작 시점 SHA-256과 모두 같다.
- 최종 구문 검사에서 발견한 headless 브라우저 분기 들여쓰기 오류는 수정 후 Fixed+관제/RL 데모로 재검증했다. 변경 Python11개 AST와 diff 검사도 통과했다.

원시 결과는 `.codex-artifacts/set1-*` 및 `.codex-artifacts/set1 eval verified/`에 있다.
전체 평가 실행 뒤 추가한 소스 해시 manifest와 0 기준선 표시 보강은 별도5초 평가로 확인했다.
위 실행은 연결·정확성 검사이며 본 평가나 성능 개선 증거로 사용하지 않는다.

## 독립 검토와 역할

신호 담당은 별도 worktree에서 구현했고 총괄이 실제3개 파일 diff를 확인해 통합했다.
평가·진입점은 총괄이 정비했으며 별도 검토 담당이 읽기 전용으로 검토했다.
기존 출력 덮어쓰기 가능성과 재개 CSV 헤더 누락을 찾아 수정했고, 재검토에서 blocking 사항 없음으로 확인했다.
빈 액션의 상위 라이브러리 우회 경로도 사전 거부와 무부작용 검사로 막았다.
사용자 소유의 새 담당 채팅이나 예약 자동화는 생성하지 않았다. 커밋·푸시도 하지 않았다.

## 다음 세트

2세트: 경주 연결 교차로3개 환경 구축. 첫 작업은 대상 구간 후보·지도 출처·수요 가정을 정하는 것이다.
도로/신호 ID와 회전·연결 규칙을 정한 뒤 차량 통행 검증으로 진행한다.
실물 Pi·새 도로망·다중 교차로 AI·장시간 학습·본 성능 평가는 아직 수행하지 않았다.
