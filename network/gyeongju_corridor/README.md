# 경주 금성로 3교차로 SUMO 네트워크

금성로를 따라 남측 교차로(`gj_south`), 동대사거리(`gj_center`),
알천남로 교차로(`gj_north`)를 연결한 승용차용 소규모 corridor다.
교차로 사이 중심 거리는 약 245 m와 233 m이며 OSM의 분리차도와 도로
기하를 유지했다. 신호 ID는 SUMO 네트워크와 계약 파일 모두 `gj_south`,
`gj_center`, `gj_north`를 사용한다.

## 재현

저장된 subset으로 재생성한다.

```powershell
python tools/build_gyeongju_corridor.py
```

핀 고정한 원본 응답에서 subset까지 다시 생성하려면 다음을 실행한다.
스크립트가 원본 SHA-256을 먼저 검사한다.

```powershell
python tools/build_gyeongju_corridor.py --source .codex-artifacts/set2-research/gyeongju-expanded.osm
```

30초 무수요 smoke test:

```powershell
sumo -c network/gyeongju_corridor/corridor.sumocfg
```

수요 생성기와 제어기는 [contract.json](contract.json)의 외부 경계 edge,
신호 단계, controlled movement를 사용한다.

차량을 넣는 전체 검증과 GUI 실행은 최상위 [README](../../README.md)의
SET2 절차를 따른다. 검증 결과의 `schedule.json`은 고정된 출발 일정,
`metrics.json`은 차량별 실제 통과 교차로와 도착 기록,
`probes/movements.json`은 회전별 내부 차로 통과 증거다.
수요는 실제 관측량이 아닌 재현 가능한 가상 입력이다.
빌더는 생성된 도로망·계약을 갱신하며, 검증기는 기존 출력 폴더 재사용을 거부한다.
SUMO/netconvert 1.26.0에서 재생성과 실행을 검증했다.

## 모델 가정

- OSM에 제한속도가 없는 대상 도로는 도시부 모델 가정으로 50 km/h를
  사용한다. 현장 측정값이나 법정 제한속도 주장으로 사용하지 않는다.
- OSM way `642113878`은 차로 수가 없으므로 SUMO의 편도 1차로 기본값을
  유지한다. 알천남로의 양방향 `lanes=4`는 방향별 2차로로 배분한다.
- OSM에 없는 회전차로 정보는 현장 차로 배치로 간주하지 않는다.
- 교차로 사이 생활도로·서비스도로·주거도로는 생략하고 유입·유출을 0으로
  둔다. 보행, 자전거, 버스정류장, 주차와 U-turn도 모델에서 제외한다.
- netconvert가 만든 각 green state의 `G/g` 우선권은 유지한다. 모든 green
  group은 green 30초, yellow 3초, all-red 2초로 정규화한다. 교차로별
  green group 수는 center 3개, south 2개, north 2개다.

원본과 라이선스 정보는 [source/SOURCE.md](source/SOURCE.md)에 있다.
