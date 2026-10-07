# SET2 — 경주 교차로 3개 환경 검증

검증일: 2026-09-17. Python 3.11.0 / SUMO·netconvert 1.26.0.
범위와 역할은 [목표 카드](set2_plan.md), 기존 기반은 [SET1](set1_completion.md)을 따른다.

## 구현 결과

- 금성로의 연결 교차로 3개: `gj_south`(4지), `gj_center`(동대사거리, 4지), `gj_north`(알천남로 접속, 3지).
- 승용차 도로 edge 19개, 외부 진출입 경계 7곳, 방향별 이동 경로 42개, 신호 제어 차로 연결 35개.
- 현재 OSM 원본에서 기하·분리차도·명시 차로 태그를 유지했다. 중간 골목을 제외한 모델이며 출처·가정을 별도 기록했다.
- 공통 ID, 좌표, 경계 edge, 신호 단계와 제어 차로 연결은 [contract.json](../network/gyeongju_corridor/contract.json)으로 제공한다.
- 녹색은 south/north 각 2그룹, center 3그룹이다. 각 단계 녹색30초 → 황색3초 → 전체적색2초로 순환한다.
- 수요·출발 일정·명령·입력 해시·통행 기록을 실행별로 분리한다. 출력 폴더 재사용은 거부한다.

## 실행 검증

```powershell
python -X utf8 -B tests/test_gyeongju_corridor.py
python -X utf8 -B tools/verify_gyeongju_corridor.py --out .codex-artifacts/set2-validation-final
```

| 확인 | 결과 |
|---|---|
| 회귀 검사 5개 | PASS: 42경로, 충돌 녹색·전체적색 누락 거부, 수요 재현, 출력 보존·짧은 기간 거부 |
| 차로별 회전 probe | 35/35 도착, 지정된 내부 차로 실제 통과 |
| 경계 간 이동 | 42/42 경로를 각 수요에서 실제 운행, 차량별 교차로 통과 순서 일치 |
| 신호 | 정적 프로그램·30/3/2초·모든 연결 서비스·보호 녹색 충돌 검사, 실행 중 순환·시간 검사 PASS |
| 좌표·계약 | SUMO 자체 좌표 변환으로 계약 좌표 확인, 신호·연결·경계 데이터 일치 |
| 경고·충돌·teleport | 모든 probe 및 수요 실행에서 0 |

출발 일정은 처음 42대를 2초 간격으로 모든 이동 경로에 배정하고, 나머지 구간에 수요별 가중 추출을 적용한다.
seed=42, 출발 구간180초, 마지막 출발 이후 최대600초 배출 한도다.

| 가상 수요 | 예정 / 출발 / 도착 | 미진입 / 미도착 | 마지막 도착 시각 |
|---|---|---|---|
| light | 62 / 62 / 62 | 0 / 0 | 275초 |
| balanced | 90 / 90 / 90 | 0 / 0 | 366초 |
| northbound_peak | 90 / 90 / 90 | 0 / 0 | 366초 |

총 242대이며, 회전 probe 35대는 별도다. 북행 편중은 세 교차로를 북쪽으로 통과하는 경로의
추출 가중치를 4배로 둔다. 검증용 부하이므로 실제 교통량·용량·AI 개선 효과를 의미하지 않는다.

## 재생성과 보존

저장소 생성물과 독립된 `.codex-artifacts/set2-rebuild`에 빌더·subset·설정만 복사한 뒤 실행했다.

```powershell
python -X utf8 -B tools/build_gyeongju_corridor.py
python -X utf8 -B tools/build_gyeongju_corridor.py --source <보존한 gyeongju-expanded.osm의 절대 경로>
```

- 저장된 subset에서 재생성, 핀 고정 원본에서 subset까지 재생성: 모두 PASS.
- 도로망·계약·subset의 SHA-256이 통합본과 각각 같다. netconvert 경고 없음.
- 도로망 SHA-256: `8563890f22dc30e38d2e21899aea60dd07faf35536619a89208bc644d316caf4`.
- SET2 시작 시 보관한 SET1 코드·도로망 12개 파일 SHA-256 그대로 유지. 기존 결과 경로는 수정하지 않았다.
- 재생성 근거: `.codex-artifacts/set2-rebuild/verification.json`.
- 전체 통행 근거: `.codex-artifacts/set2-validation-final/summary.json`, 각 수요의 `tripinfo.xml`, `metrics.json`, `schedule.json`, `input_hashes.json`, `sumo-errors.log`.
- `.codex-artifacts/`는 로컬 검증 기록이며 Git에 포함하지 않는다. 재실행에 필요한 subset·도로망·빌더·검증기는 신규 저장소 파일이다.

## 출처와 제한

[OSM 원본 취득 정보](../network/gyeongju_corridor/source/SOURCE.md)와
[모델 가정](../network/gyeongju_corridor/README.md)을 따른다.
모든 접근로 제한속도50km/h, 미기재 편도 차로1개, 알천남로 방향별2차로,
자동 생성 회전 연결과 신호 계획은 현장 검증 정보가 아니다.
현 단계에서는 중간 골목·보행·자전거·버스정류장·주차·U-turn 유입을 모델링하지 않는다.
현장 신호 설계의 안전성이나 실제 경주 교통에 대한 성능을 주장하지 않는다.

## 진행 중 발견한 문제와 수정

- 최초 지도 범위가 남측 접근로 전체를 포함하지 않아 남쪽으로 확장해 다시 받았다.
- 자동 변환의 80km/h 추정값을 50km/h 모델 가정으로 명시하고 후보 좌회전 경고가 사라지는 것을 확인했다.
- 검증기 초기 경계 탐색이 분리차도 경계만 골라 계약의 7개 물리 경계를 사용하도록 수정했다.
- 좌표 검사에 선택 의존성 `pyproj`가 없어서, 설치 변경 없이 SUMO 자체 변환을 사용했다.
- 일부 검사 생략 시 PASS로 오해할 수 있는 옵션을 제거했고, 수요 기간과 계획된30초 신호를 명시적으로 검사한다.

## 역할 및 다음 작업

환경 담당이 별도 worktree에서 신규 파일을 구현했고, 총괄이 7개 전달 파일을 확인·통합했다.
총괄은 수요·검증기를 구현하고 통합본에서 실제 검사했다. 별도 담당의 최종 독립 검토에서 blocking 결함이 없었다.
독립 검토는 원본12개 way의 연속 노드열·좌표 보존과 결과 해시, 35회전·242대의 원시 통행 기록을 대조했다.
사용자 소유의 별도 역할 채팅 ID는 확인되지 않아 내부 담당 에이전트로 분담했다.

완료 상태: DONE. 다음 SET3 / G2: 이 계약을 사용하는 시뮬레이션 상태·명령 규칙을 정하고,
웹에서 세 교차로 상태를 표시하며 실행·정지·초기화를 검증한다.
SET1의 4단계·29D/53D 모델을 단계 수가 다른 이 도로망에 바로 적용하지 않는다.
다중 교차로 관측·행동 설계와 학습은 G3에서 진행한다.
