# 경주 연결3교차로 — 다음 세트 준비

2026-10-04 사용자 요청: 지금은 사용량을 아끼며 환경/준비만 하고, 오늘15:45 KST 이후 자동 후속 실행한다.
앱 heartbeat 자동화 ID3,1회 실행 예약. 15:45 전 준비 단계에서는 실제 교통 운행/학습을 하지 않았다.

## 동결 계획

- 지도: 기존 지도 정리형의 용강/근화여고/승삼 `gj_yonggang/gj_geunhwa/gj_seungsam`.
- 입력: `network/adaptive_gyeongju_wave3_20261004/suite.json`. 기존 검증된 net/spec를 그대로 복사했다.
- 수요: 한산/혼잡/편중×새 표본2개=6조건. seed76000/76001/76010/76011/76020/76021.
- 이는 기존 경주 기하에 새 수요를 적용하는 평가다. 새 기하 일반화 평가로 부르지 않는다.
- 모델: 검증으로 선정한 기존 seed43와 `load_gate_pressure_wait35`, suite의 selection_freeze/해시 고정.
- 제어: Fixed/Webster/Max-Pressure/SmartV2 각6회, 총24회 순차 실행. 추가 학습/테스트 결과 재선정 없음.
- 신호/시간: 기존 min15/max60/황색3/전체적색2/decision5·유입600+정리300=900초 그대로.
- 출력 예정: `results/runs/adaptive_gyeongju_wave3_20261004/`(기존 결과 덮어쓰기 금지).
- 판단: 평균 대기행렬 Fixed 대비5% 감소·도착 수 비감소·수요군10% 초과 악화 없음.
  순간 최대 대기열/미완료/판단 시간/경고도 별도 보고한다. 기준 미달은 미달로 보존한다.
- 오류/충돌/teleport/원시 기록 불일치면 중단·진단. 급제동은 세부 원인을 남겨 검토한다.
- 한도: 최대24회·완료 경계45분. 큰 세트 종료 후 중간 결과 보고하고 멈춘다.

## 지금 준비·확인

```powershell
python -X utf8 -B tools/prepare_adaptive_gyeongju.py
python -X utf8 -B -m transferable_signal check --suite network/adaptive_gyeongju_wave3_20261004/suite.json --load
```

생성 PASS:3신호·새 수요6개. 기존 core/policy/model/net/spec를 변경하지 않았다.
준비 단계 SUMO0초 로드 PASS:1도로망/6수요. 이 검사에서는 실제 교통 운행0회다.
로그는 `.codex-artifacts/adaptive-gyeongju-wave3-preparation-20261004.log`,
`.codex-artifacts/adaptive-gyeongju-wave3-load-20261004.log`에 보존한다.

## 15:45 이후 실행 담당 인계

1. 이 문서/STATE와 자동화 요청을 확인하고 남은 사용량 제한 여부를 확인한다.
2. suite/원본/모델/정책 해시와 예정6조건/동일 제약을 재확인한다.
3. 새 전용 `tools/run_adaptive_gyeongju_wave.py`를 최소 구현한다(아래 예약 실행에서 완료).
   기존 wave/holdout 실행 도구는 해시를 보존해야 하므로 수정하지 않는다.
   기존 `common.audit_run`/원시 지표/manifest/CSV/경고/완료 경계 재개 패턴을 재사용한다.
   `runner.SmartSignal` 및 provenance의 V2 반영은 기존 holdout과 같은 방식으로 적용한다.
   표준 smart CLI는 원형AI이므로 V2라고 착각해서 바로 실행하지 않는다.
4. 가짜 환경의 순차 실행/중단/해시/재개 검사와 별도 읽기 전용 source review를 통과한 뒤 실제24회를 실행한다.
5. 실제 매회 원시 결과 검토→다음 실행, 전체 독립 감사·문서 갱신·간단 결과 보고로 마친다.

환경 준비 후 전용 평가 실행 코드는 사용량 리셋 이후 구현했다.

## 예약 후속 실행 상태

2026-10-04 15:45 KST heartbeat를 받아 진행을 재개했다. 계정 조회에서 일반 실행 가능,
5시간 구간 사용0%를 확인했다. 기존 규칙/입력/모델을 유지하며 신규 실행 도구/가짜 검사와 별도 검토를 진행한다.
준비된 입력의 SUMO0초 로드1망/6수요를 다시 통과했다. 아직 실제 교통 운행은 시작하지 않았다.

전용 `tools/run_adaptive_gyeongju_wave.py`와 `tests/test_adaptive_gyeongju_wave.py`를 추가했다.
가짜 검사9개를 구현 담당과 총괄이 각각 통과했다. 정확24조건·순차 원시 감사·동결모델,
중단/partial 및 완료 orphan 보존 재개·해시/기록 변조 거부·충돌 중단·시간 경계·급제동 기록을 확인한다.
실제 준비 데이터의 preflight·신규2파일 AST·CLI 도움말·`git diff --check`도 PASS다.
기존 실행 도구/정책/모델/원본 지도는 변경하지 않았다.

```powershell
python -X utf8 -B tests/test_adaptive_gyeongju_wave.py
python -X utf8 -B tools/run_adaptive_gyeongju_wave.py --out results/runs/adaptive_gyeongju_wave3_20261004
```

실제 실행 전 읽기 전용 source review PASS를 별도로 확인했다. 15:54:00 KST PowerShell 실제24회 비교를 시작했다.
콘솔은 `.codex-artifacts/adaptive-gyeongju-wave3-console-20261004.log`에 보존한다.

## 24회 결과 — 완료

16:12:35 KST에24/24회 완료(콘솔 기준18분35초). 추가 학습 없이 같은 seed43 모델/보정안을
세 교차로에 함께 적용했다. 사전에 정한 목표는 `target_met=true`다.
각 제어기가 동일6조건·총예정3,100대·조건당900초를 사용했다.
대기행렬은 세 교차로 전체의 순간 대기 차량 합계를 시간 평균한 뒤6조건 평균한 값이다.
도착/미완료는6조건 합계다.

| 제어 | 평균 대기행렬(대) | 도착(대) | 미완료(대) | 최대 대기행렬(대) | 완료 차량 평균 대기(초) |
|---|---:|---:|---:|---:|---:|
| Fixed | 97.159 | 2,448 | 652 | 274 | 138.517 |
| Webster | 84.892 | 2,674 | 426 | 252 | 128.924 |
| Max-Pressure | 78.697 | 2,533 | 567 | 258 | 112.156 |
| SmartSignal V2 | **69.883** | **2,747** | **353** | **247** | **106.077** |

Smart 대기행렬은 Fixed 대비28.07%, Webster 대비17.68%, Max-Pressure 대비11.20% 감소했다.
Fixed보다299대 더 도착했고 미완료는652→353대로 줄었다.
완료 평균 대기는 완료 차량 수로 가중한 값이며 미완료 차량을 제외한다.
완료 차량 평균 통행시간은 Fixed284.002초·Webster276.133초·Max-Pressure268.508초·Smart254.946초다.
추론 판단은 이 PC/이번 조건에서 교차로당 평균0.166ms다. Pi 지연은 아직 측정하지 않았다.

| 수요 | Smart 대기행렬(대) | Fixed 대비 | Smart 도착/예정 |
|---|---:|---:|---:|
| 한산 | 21.295 | −48.86% | 702/702 |
| 혼잡 | 117.233 | −19.16% | 1,093/1,331 |
| 편중 | 71.122 | −32.14% | 952/1,067 |

6개 개별 조건에서도 Fixed 대비 대기행렬은 모두 줄었고 도착 수는 모두 늘었다.
미완료353대(혼잡238·편중115)는 남아 있다. 유입600초+정리300초의 종료 시점에 남은 차량이며,
완전한 정체 해소 또는 모든 환경의 최적 신호를 확인한 것은 아니다.

## 경고·원시 검증

- 전체24회 충돌/teleport0. Smart6회 경고0.
- 급제동1건은 Webster/`gyeongju_busy_s01`/차량`veh_00206`/`:gj_geunhwa_10_0`/620초/decel7m/s²다.
  원시 로그와 경고 서명을 보존했다. 다른 경고/실행 오류/기록 불일치는 없다.
- 이 위치의5초 신호 기록은615초 S_green50초·620초 S_green55초·625초 W_green0초다.
  인접한 전환 구간은 확인했으나 차량별1초 진단은 이번24회 한도 밖이므로 원인을 확정하지 않았다.
  경고를 숨기거나 신호 제한을 줄이지 않았다.

```powershell
python -X utf8 -B .codex-artifacts/audit_adaptive_gyeongju_wave3_20261004.py --require-complete
```

`.codex-artifacts/adaptive-gyeongju-wave3-audit-20261004.json`: `audit=PASS`,24회·목표 판정 PASS.
metrics/tripinfo/CSV·실제 SUMO 입력 헤더·모델/정책/코드/수요 해시·차량 수/완료 통계·경고를 대조했다.
기록된5초 신호 경계12,960개를 Clock 규칙과 대조했고, 원본의 보호 녹색 충돌은0이다.
이전 독립48회/보정126회/1차102회/원형180회도 재감사했다.
가중 완료 지표와6개 조건 대조는 `.codex-artifacts/adaptive-gyeongju-wave3-additional-metrics-20261004.json`에 남겼다.

## 변경·다음 행동

이번 예약 실행의 변경은 신규 실행 도구/관련 검사와 이 문서/STATE/GOAL/project_charter다.
신규 가짜 검사9개·실제 입력검사·독립 source review·최종24회 원시 감사·신규2파일 AST·문서 형식·`git diff --check` PASS,
기존 모델/정책/지도/실행 도구/평가 결과는 그대로 보존했다.

한 세트를 완료했으므로 후속 운행을 멈췄다. 새 기하2개에 이어 기존 경주 연결3교차로의
새 수요6조건에서도 재학습 없는 적용과 기준선 대비 개선을 확인했다.
다음은 동결 모델/보정안의 기본 실행 경로를 정리하고, 교차로별 미완료·진출 정체가 남는 조건을
진단할지 또는 웹 관제/Pi 연결로 넘어갈지 이번 결과를 바탕으로 정한다.
추가 연구/학습/운행은 이번 예약에서 실행하지 않았다. 표준 smart CLI는 아직 원형AI이며 V2는 전용 도구를 사용한다.
