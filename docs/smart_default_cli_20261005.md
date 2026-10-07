# 검증 SmartSignal 기본 CLI 연결

## 완료 요약

2026-10-05 기본 CLI의 `smart`를 검증된 seed43/V2로 연결했다. `--model`을 생략해도 자동 선택하며 재학습하지 않는다.
신규16개 + 기존16/12/11개, 총55개 관련 검사·독립 source review PASS.
실제900초 운행3회와 최종 원시 기록 검증 PASS, 경고/충돌/teleport0.
혼잡·한산2회는 이전 전용 V2의 모든 교통 metrics(벽시계 판단 시간 제외)/차량별 trip 속성/신호540행과 일치한다.

## 바로 실행

PowerShell 또는 VS Code 터미널에서 프로젝트 폴더로 이동해 실행한다.

```powershell
cd "C:\Users\James\.codex\worktrees\f73e\sumo_project"
python -B -m transferable_signal run --scenario gyeongju_busy --controller smart --gui --out results/runs/smart_default_demo_01
```

모델 경로와 V2 보정 이름을 입력할 필요가 없다. GUI 없이 실행하려면 `--gui`를 생략한다.
검증한 실제 운행은 headless이고 `--gui` 전달은 가짜 호출 검사로 확인했다. GUI 실행 경로 자체는 기존 구현을 유지한다.
`--out`에는 아직 없는 새 폴더명을 사용한다. 기존 폴더를 덮어쓰지 않는다.

```powershell
python -B -m transferable_signal compare --split test --out results/runs/smart_default_compare_01
```

위 비교 명령은 기본으로4제어기, 기본 suite의 경주3수요를 순차 실행한다.
이번 실제 비교 검증은 한산1수요/Smart만 수행했고,4제어기 기본 선택과 동일 CSV 열은 가짜 검사로 검증했다.
원형 모델의 학습/평가에는 `--smart-policy raw --model <model.json>`을 명시한다.
하위 `runner.run/compare` Python API는 기본 raw를 유지하며 `smart_policy='validated'`로 새 기본 정책을 명시할 수 있다.

## 실행 전 목표

- 사용자 요청: 검증된 모델을 기본 실행 명령에 연결하고 검증/확인을 필수로 수행한다.
- 범위: CLI run/compare와 명시 정책 선택·동결 프로필·관련 검사. 정책/학습/도로/기존 결과는 유지한다.
- 선택: CLI `smart`는 기본 `validated`(seed43/load_gate_pressure_wait35), `--smart-policy raw`는 기존 모델 실행이다.
- 하위 Python API는 기본 raw를 유지한다. 전역 patch 없이 keyword-only 정책 옵션으로 연결한다.
- validated의 모델 미지정은 저장된 seed43을 자동 선택하며, 지정 모델은 동일 SHA만 허용한다.
- 프로필/모델/정책 소스/관측·신호 규격/안전 제약과 test 누출을 출력 생성·SUMO 운행 전에 검증한다.
- compare는 모든 대상의 선행 검사를 마친 뒤 순차 실행한다. CSV 열은 모든 제어기에서 같은 구조를 유지한다.
- 완료 기준: CLI/default/raw/baseline/오류 차단/전용 V2 행동 동등성/관련 회귀·독립 검토 PASS.
- 실제 검증 한도: 기존 경주 새 수요의 busy_s00 개별 run 900초, quiet_s00 한 조건 compare 900초,
  그리고 `--suite`를 생략한 기본 경주 busy run 900초, 총3회. 기본 입력 경로까지 실제 확인한다.
  같은 원시 metrics·tripinfo·signals를 이전 전용 V2 결과와 대조하고 벽시계 판단 시간만 제외한다.
- 실행 오류·충돌·teleport·기록/정책 불일치면 중단하고 원인을 확인한다. 추가 학습이나 모델 재선정은 없다.
- 기존 패키지/도구 소스와 모델·이전 감사 증거 해시를 변경 전 별도 snapshot에 보존한다.
  새 CLI의 소스 해시 변화와 과거 실행의 해시를 구분하며 과거 결과/동결 감사 기준을 수정하지 않는다.
- 담당: 구현과 독립 검토 분리, 실제 SUMO는 root의 PowerShell에서만 실행한다.

## 현재 상태

완료. 구현 전 source29개/기존 증거8개/모델3개 SHA를 보존했고, 아래 실제 실행·검증까지 완료했다.

## 변경 파일과 동작

- `transferable_signal/deployment.py`, `configs/smart_signal_default.json`: 검증 모델/정책 factory와 동결 프로필.
  모델 seed43/해시·wait35 설정·관측/안전 규격·추론 source10개 SHA를 검사한다.
  프로필 identity는 정렬된 JSON의 SHA로 고정해 공백/줄바꿈 형식 차이와 의미 변경을 구분한다.
  기본 모델 경로는 프로젝트 ROOT 기준 상대 경로다. 명시 모델은 동일 SHA의 복사본도 사용할 수 있다.
- `transferable_signal/runner.py`: keyword-only 정책 선택, 전체 대상 사전 검증, manifest 정책 기록.
  모델/프로필 오류·안전 제한 차이·test 학습 누출·중복 scenario/controller·기존 출력은 실행 전에 거부한다.
  원형 API와 전용 도구의 기존 factory hook은 유지한다. 정책 metadata는 manifest에만 추가해 CSV 열을 유지한다.
- `transferable_signal/__main__.py`: run/compare의 `--smart-policy validated|raw`, 기본 validated.
  예상 입력 오류는 조치가 포함된 짧은 오류로 표시하고 예상하지 못한 실행 오류는 숨기지 않는다.
  baseline의 모델/정책 선택, train/prepare/check/compare-models 동작은 유지한다.
- `tests/test_transferable_default_cli.py`: 기본 선택/raw 호환/기준선 분리/전체 사전 차단/행동 동등/CSV/구형 입력 회귀16개.
- `README.md`, `docs/transferable_signal.md`, `STATE.md`, `GOAL.md`, 이 문서: 현재 기본 명령·원형 옵션·검증 상태를 갱신했다.

## 발견한 호환 오류와 보완

실제 `--suite` 생략 실행에서 기존 v1 suite에 공통 `limits`가 없어 새 사전 검사에 차단됐다.
기존 입력은 각 intersection spec에 안전 제한을 저장한다. 최초 실패는 출력/차량 운행을 만들기 전에 발생했다.
각 spec 제한은 항상 엄격히 대조하고, suite 공통 제한은 키가 있을 때 추가 대조하도록 검사 조건만 보완했다.
공통 값이 존재하는데 None/잘못된 값이면 계속 거부한다. 실제 제한/입력/모델/정책은 바꾸지 않았다.
기본 suite를 읽는 가짜 CLI 회귀와 공통 제한 생략 상태의 잘못된 spec 차단도 추가했다.
보완 후 관련16/16/12 검사를 다시 실행했고 독립 검토 및 실제 기본 명령을 통과했다.

## 실행과 확인

| 검증 |900초 도착/예정|잔류|평균 대기행렬|원시 확인|
|---|---:|---:|---:|---|
|CLI run busy_s00|557/673|116|121.002|이전 V2와 모두 일치|
|CLI compare quiet_s00|336/336|0|20.814|이전 V2/CSV와 모두 일치|
|기본 suite run gyeongju_busy|569/680|111|106.734|모델/입력/신호 기록·차량 계수 PASS|

각 운행은900초, 전체3회 경고/충돌/teleport0이다.
잔류는900초 종료 시 실제 running 수다. 이번 목적은 CLI 연결 검증으로, 전원 도착이나 새 성능 최적화를 주장하지 않는다.
이전2회는 구형 입력 보완 전의 소스 revision, 기본 명령은 보완 후 revision으로 기록했다.
두 revision의 SHA와 실제 소스 바이트를 보존하고 해당 시점의 provenance로 대조했으므로 이전 운행을 반복하지 않았다.
보완은 사전 limits 검사 조건 하나이며 전용 V2 행동/관측/신호 코드는 변경하지 않았다.

실행기: `C:/Users/James/AppData/Local/Programs/Python/Python311/python.exe`, Python3.11/SUMO1.26.
관련 명령은 모두 종료0이며, 위 구형 입력 검사의 최초 거부(종료2, 실제 운행0)는 별도 로그로 보존했다.

```powershell
python -X utf8 -B tests/test_transferable_default_cli.py          #16 PASS
python -X utf8 -B tests/test_transferable_signal.py               #16 PASS
python -X utf8 -B tests/test_transferable_validation.py           #12 PASS
python -X utf8 -B tests/test_transferable_adaptive_v2.py          #11 PASS
python -X utf8 -B -m transferable_signal run --help
python -X utf8 -B -m transferable_signal check --suite .codex-artifacts/cli_v2_smoke_20261005/suite.json --load
python -X utf8 -B -m transferable_signal run --suite network/adaptive_gyeongju_wave3_20261004/suite.json --scenario gyeongju_busy_s00 --controller smart --out results/runs/smart_default_cli_20261005/run_busy
python -X utf8 -B -m transferable_signal compare --suite .codex-artifacts/cli_v2_smoke_20261005/suite.json --split test --controllers smart --out results/runs/smart_default_cli_20261005/compare_quiet
python -X utf8 -B -m transferable_signal run --scenario gyeongju_busy --controller smart --out results/runs/smart_default_cli_20261005/run_default
python -X utf8 -B .codex-artifacts/verify_default_cli_20261005.py --out .codex-artifacts/smart-default-cli-final-audit-20261005.json
git diff --check
```

실행한 폴더/JSON은 덮어쓰지 않으므로 재실행에는 새 출력 이름을 사용한다.
새 Python4파일과 원시 검증 artifact의 AST 확인도 PASS. 단위/회귀 검사는 fake/정적 검사를 포함하며 실제 교통 학습을 추가한 것이 아니다.

## 증거·보존과 다음 작업

실제 결과는 `results/runs/smart_default_cli_20261005/`, 최종 검증은 `.codex-artifacts/smart-default-cli-final-audit-20261005.json`이다.
변경 전 source29개/기존 증거8개는 `.codex-artifacts/cli_v2_before_20261005/`, 첫 revision 소스는 `.codex-artifacts/cli_v2_integration_v1_source/`에 있다.
모드/seed/모델 SHA/variant/config/프로필 SHA·canonical SHA/factory SHA는 각 run의 manifest에 기록한다.
이전 모델3개·기존 증거8개·보호한 추론/제어·기존 실험 tools의 SHA는 모두 그대로다.

코드 개편으로 runner/CLI SHA와 package source 목록은 바뀌었다.
과거 동결 실험의 엄격한 현재 소스 검사/재개가 거부되는 것은 의도된 보호이며 과거 성능 기록을 무효로 만드는 것이 아니다.
과거 검증은 보존 snapshot/원래 source hash를 기준으로 수행하고 과거 manifest/CSV/판정을 새 SHA로 바꾸지 않는다.
이번 모델 재선정/학습/도로/수요/정책 변경은0이며 추가 비교 배치는 시작하지 않았다.

다음은 공통 factory와 관측/행동 규격을 웹/Pi에 연결하는 작업이다. 실제 교통 수요와 Pi 추론 실측은 미검증이다.
