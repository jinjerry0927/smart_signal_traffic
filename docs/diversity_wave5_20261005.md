# 다양한 교통 상황 검증 — 5차 세트

## 목표 카드

- 요청: 실제 교통량 확보가 어려우므로 다양한 가상 상황에서 적응성을 확인한다.
- 분류: high / 범위가 정해진 연구 평가. 현장 교통량 검증은 자료 확보 전 보류한다.
- 질문: 동결한 seed43/wait35가 기존 한산·혼잡·편중 밖의 흐름에서도 안정적으로 작동하는가?
- 모델·정책·지도·신호 제한은 유지한다. 추가 학습, 테스트 결과에 따른 모델 재선정은 하지 않는다.
- 범위: 신규 수요 생성·순차 실행 도구, 관련 검사, 상태/목표/결과 문서.
- 입력: 기존 경주 연결3교차로, 학습에 없는 네트워크, 새 수요 seed78000 이상.
- 조건: sparse(낮은 유입), wave(150초마다 유입량 변화), reversal(300초에 주방향 반전),
  side_surge(측면도로 유입 증가), left_bias(진입 후 첫 회전의 좌회전 비중 증가), heavy_mix(버스/화물 비중 증가).
- 각 조건 새 표본2개, Fixed/Webster/Max-Pressure/검증 Smart 각1회: 총48회.
- 수요600초 + 정리300초 =900초. 같은 수요·차량·안전 제한·SUMO seed로 비교한다.
- 전 과정 가상 수요다. 현장 신호/교통량 재현이나 모든 교차로의 보장으로 해석하지 않는다.
- 완료: 신규 입력 해시/중복/정적 검사, 관련 코드 검사, 매 운행 원시 계수·tripinfo·신호·정책 검사,
  48회 결과와 조건별 약점/경고/미완료 보고. 안전 문제나 시간 한도면 보존하고 중단한다.
- 사전 성능 기준: 평균 대기행렬 Fixed 대비5% 감소, 도착 수 비감소,
  조건별2표본 평균 대기행렬10% 초과 악화 없음. 통과 여부와 실험 완료를 구분한다.
- 한도: 최대48회·완료 경계45분. 실패/충돌/teleport/기록 불일치면 즉시 다음 운행을 중단한다.
  급제동 경고는 모두 원문 기록·별도 평가하며 안전 통과로 숨기지 않는다.
- 검증: 관련 단위검사, SUMO0초 로드, 실제 순차 실행, 별도 원시 기록 재확인.
- 저장: network/diversity_wave5_20261005/, results/runs/diversity_wave5_20261005/.
- 상태: 48회 실제 순차 운행과 최종 원시 검증 완료, 사전 성능 기준 통과.

## 실행 명령

```powershell
python -X utf8 -B tools/diversity_wave.py prepare --out network/diversity_wave5_20261005
python -X utf8 -B -m transferable_signal check --suite network/diversity_wave5_20261005/suite.json --load
python -X utf8 -B tools/diversity_wave.py run --suite network/diversity_wave5_20261005/suite.json --out results/runs/diversity_wave5_20261005
python -X utf8 -B tools/diversity_wave.py verify --suite network/diversity_wave5_20261005/suite.json --out results/runs/diversity_wave5_20261005
```

원본 모델/정책/기존 입력/기존 결과는 보존한다. 이번 결과의 약점은 다음 별도 학습/검증 배치의
설계에 사용할 수 있지만 같은 평가 표본을 반복 튜닝해 독립 검증으로 다시 부르지 않는다.

## 완료 결과

2026-10-05 PowerShell로 48회를 순차 실행했다. 각 운행의 입력/정책/코드 해시, 차량 계수,
원시 tripinfo·로그·5초 신호 기록을 확인한 뒤 다음으로 넘어갔다. 실행/검증 종료 코드는0이다.
학습/모델 재선정은0회이고 기존43/V2가 그대로 실행됐다. 전체 결과 `target_met=true`.

| 제어 | 평균 대기 차량(대) | 도착/예정 |900초 종료 시 미완료 |
|---|---:|---:|---:|
| Fixed |51.481|3,997/4,417|420|
| Webster |45.569|4,073/4,417|344|
| Max-Pressure |36.071|4,186/4,417|231|
| SmartSignal V2 |**32.686**|**4,297/4,417**|**120**|

평균 대기 차량은 조건마다900초 동안 세 교차로 전체의 대기 차량 합계를 평균하고,
12조건에 같은 비중을 둔 값이다. 도착/미완료는12조건 합계다.
같은 조건의 Smart는 Fixed 대비36.51%, Webster 대비28.27%, Max-Pressure 대비9.38% 낮다.
Fixed보다300대 더 도착했다. 전체12개 조건에서 Fixed보다 대기 차량이 줄었고 도착 수는 감소하지 않았다.

| 새 교통 상황 | Smart 평균 대기 차량 | Fixed 대비 감소 | Smart 도착/예정 | 미완료 |
|---|---:|---:|---:|---:|
| 낮은 유입(sparse) |4.263|60.17%|213/213|0|
| 유입 파동(wave) |37.051|37.78%|784/800|16|
| 주방향 반전(reversal) |18.461|53.40%|660/660|0|
| 측면도로 혼잡(side_surge) |63.799|30.57%|956/1,023|67|
| 좌회전 편중(left_bias) |24.850|38.64%|862/862|0|
| 대형차 증가(heavy_mix) |47.692|28.42%|822/859|37|

6종 각각2표본의 평균이다. wave는150초마다80→360→80→360대/시/차로,
reversal은300초에 남측/북측 간선 유입360/90을 반전시키고 측면은160을 유지한다.
side_surge는 간선160·측면360, sparse60, left_bias/heavy_mix240대/시/차로다.
좌회전 비중은 진입 직후 첫 회전에 대한75/15/10 가중치이며 존재하는 회전 사이에서 정규화한다.
다른 후속 교차로의 회전 비율을75%로 고정했다는 뜻은 아니다.
대형차 조건의 가중치는 승용50/버스25/화물25%, 나머지는80/10/10%이며 실제 생성 개수는 suite에 기록했다.
경주의 실제 교통량·차종 비율·시간대 관측치가 아니라 가상 스트레스 조건이다.

### 경고와 남은 한계

- 전체48회 충돌/teleport0, Smart12회 경고0.
- Webster/heavy_mix_s00에서 급제동1건을 그대로 기록했다: `veh_00185`, `:gj_geunhwa_3_0`,
  684초, decel7/wished4/severity1. 충돌로 집계하지도, 경고0으로 숨기지도 않았다. 원인은 이번 운행만으로 확정하지 않는다.
- Smart의120대는900초 종료 미완료 차량이다. 특히 측면도로67대·대형차37대에 잔류가 많다.
  이번 조건에서 더 오래 운행한 결과는 없으며 과거 다른 수요의 전원 도착 결과를 대신 쓰지 않는다.
- Max-Pressure 대비 평균은 개선됐지만 wave_s00 +0.08%, reversal_s01 +2.54%의 대기행렬 악화도 있다.
  sparse2개와 reversal_s00은 Max-Pressure와 교통 지표가 같다. 이 결과는 AI와 규칙을 결합한 V2의 결과이지
  순수 AI만의 추가 효과를 분리한 실험이 아니다.
- 알려진 경주 실험 도로망의 새 수요6종·2표본씩에 대한 제한된 검증이다. 새 도로 형태/센서 오차/현장 성능의 보장은 아니다.

## 변경·검증·보존

신규 `tools/diversity_wave.py`는 수요 생성/동결 프로필 검증/순차 운행/중단/변경 없는 재개/원시 확인을 제공한다.
새 단위검사 `tests/test_diversity_wave.py`와 본 문서, STATE/GOAL/차터만 작업 범위에서 수정했다.
기존 신호·AI·기본 CLI 코드는 수정하지 않았다.

```powershell
python -X utf8 -B tests/test_diversity_wave.py
python -X utf8 -B .codex-artifacts/verify_diversity_raw_20261005.py
git diff --check
```

- 새11개 검사 PASS: 명시 일정/차종/편중 구성·재현성·모델/입력 변경 차단·기존 출력 보존·48회 순서·안전 중단·재개·시간 한도.
- SUMO0초 로드:1망12수요 PASS. 차량 시간 진행은 이후 실제48회에만 포함한다.
- AST: 신규 도구/검사와 별도 검증기 구문 PASS. `git diff --check` PASS.
- 실행 도구의 최종 verify와 별도 표준 라이브러리 검증기 모두 PASS.
  별도 검증기는 실행 도구/정책 모듈을 import하지 않고 차량 ID/일정/차종/첫 회전,
  tripinfo 평균/95분위·계수·25,920개 신호 표본의 시간/전환 규칙·CSV/집계·경고·코드/모델 SHA를 확인했다.
  신호 기록은5초 경계이며 서브초 현장 관측을 의미하지 않는다.
- 같은 Python3.11.0/SUMO1.26.0로 실행했다. 실제 PC 판단 지연은 각 metrics에 있으며 Pi 실측은 아니다.
- 기존 모델3개와 과거 실험 증거8개 SHA도 별도 대조 PASS, 선택 모델 SHA는
  `8eca32d46b9cc285162cf0e70fceb1a8d696254bf72b6954d0a764a482c8b999`로 유지됐다.

증거:

- `results/runs/diversity_wave5_20261005/manifest.json`, `comparison.csv`와 조건/제어기별 원시 파일.
- `.codex-artifacts/diversity-wave5-console-20261005.log`: 실제 순차 실행/경고.
- `.codex-artifacts/diversity-preparation-proof-20261005.json`: 운행 전 입력/모델 동결 기록.
- `.codex-artifacts/diversity-wave5-independent-verification-20261005.json`: 별도 원시 검증 결과.
- `.codex-artifacts/diversity-preservation-20261005.json`: 이전 모델/증거 보존 확인.

## 다음 단계

이번48회 세트는 완료하고 멈춘다. 다음은 교차로 구조·차로 용량·관측 지연/누락 등 다른 요인의
별도 미사용 조건을 정하는 것이다. 같은 조건을 반복 실행하면 평가 횟수만 늘고 모델은 학습하지 않는다.
약점이 발견될 경우 다양화한 학습용 조건과 별도 평가용 조건을 분리해서 추가 학습을 설계한다.
실제 교통량 검증은 자료 확보 전 미완료이며 웹/Pi 연결은 별도 후속 작업이다.
