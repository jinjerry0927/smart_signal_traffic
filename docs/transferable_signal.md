# 범용 SmartSignal 환경·실행 코드

작업일: 2026-10-02. 범위: 사용자 요청 1~4번. 환경 제작과 코드 검증만 수행하며
차량 운행·학습·비교 실험은 사용자가 실행한다. 기존 단일 교차로 코드·모델을 보존한다.

## 이번 구현의 기준

- 공통 교차로 설정에서 차선 연결, 허용 녹색, 최소/최대 녹색, 황색/전체적색을 읽는다.
- 각 녹색 후보를 같은 크기의 교통 특징으로 표현한다. 교차로 ID와 나침반 방향은 모델 입력에 넣지 않는다.
- 행동은 5초마다 허용 녹색 중 하나를 요청하는 것이다. 같은 녹색을 다시 선택하면 연장하므로
  실제 녹색 시간 배분이 바뀐다. 안전 전환 장치가 최소/최대 녹색과 전환 시간을 강제한다.
- Fixed, 관측 기반 Webster, 차로 기반 Max-Pressure, 학습된 SmartSignal을 같은 장치로 실행한다.
- 가상 3지/4지·편도1~3차로를 학습/검증용으로 만들고, 경주 3교차로는 최종 테스트로 분리한다.
- SmartSignal은 모든 신호 후보에 같은 작은 신경망을 적용한다. 기존 29D/53D 모델은 호환되지 않는다.
- 현재 범위는 차량용 보호 신호다. 보행자·비보호 회전·실제 신호기 연결은 포함하지 않는다.

## 완료 조건과 검증 범위

총괄이 구현·통합·문서를 담당하고, 독립 검토 단계에서 안전 전환·평가 집계·데이터 분리를 점검한다.
서로 다른 차로/신호 수의 설정을 같은 코드가 읽고, 허용 동작을 생성하고, 실행 결과를 분리 저장해야 한다.
학습/검증/최종 테스트 수요를 동결하고 입력·코드·모델 해시를 기록한다. 기존 결과를 덮어쓰지 않는다.
검증은 정적 네트워크 검사, 가짜 연결을 이용한 제어/학습 코드 검사, SUMO의 0초 로드까지다.
실제 운행 성능·재학습 없는 전이 성능은 사용자의 실행 전까지 미검증이다.

## 준비된 파일

| 위치 | 내용 |
|---|---|
| `configs/transferable_signal.json` | 교차로 종류·차로 수·신호 제한·600초 유입/300초 정리 시간 |
| `network/transferable_v1/suite.json` | 학습 7개/검증 2개/최종 테스트 1개 도로망과 30개 동결 수요 |
| `transferable_signal/spec.py` | 도로망에서 공통 계약 생성, 보호 신호 충돌/공유 차로 검사 |
| `transferable_signal/environment.py`, `control.py` | 공통 관측, 1초 신호 전환, 5초 행동 요청 |
| `transferable_signal/controllers.py` | Fixed·Webster·Max-Pressure 비교 제어기 |
| `transferable_signal/model.py`, `learning.py` | 같은 모델로 신호 후보를 평가하는 작은 Double DQN, NumPy 추론 |
| `transferable_signal/runner.py`, `__main__.py` | 개별 실행·학습·비교·CSV/JSON 기록 |

최종 테스트의 `gyeongju` 한 도로망에 용강·근화여고·승삼 **세 교차로가 함께** 있다.
학습에는 별도로 만든 짧은 3교차로 연결망도 포함한다. 경주 도로망 자체는 학습하지 않는다.
현재 학습 환경은 소규모 시작 세트다. 모든 교차로·센서 오차·현장 상황을 대표하지 않는다.

## 실행 방법

### 검증된 SmartSignal 기본 실행 — 2026-10-05

검증된 seed43 + V2 `load_gate_pressure_wait35`를 기본 `smart`로 선택한다.
모델 경로를 생략하면 프로젝트의 검증 모델을 자동으로 찾는다. 아래의 실행은 재학습하지 않는다.

```powershell
cd "C:\Users\James\.codex\worktrees\f73e\sumo_project"
python -B -m transferable_signal run --scenario gyeongju_busy --controller smart --gui --out results/runs/smart_default_demo_01
```

비교는 같은 입력에서 Fixed/Webster/Max-Pressure/검증 Smart의4제어기를 순차 실행한다.

```powershell
python -B -m transferable_signal compare --split test --out results/runs/smart_default_compare_01
```

`--out`은 새 폴더명을 사용한다. 모델 누락/해시·설정 불일치 시 실행 전에 중단한다.
기존에 직접 학습한 다른 원형 모델을 실행하려면 `--smart-policy raw --model <model.json>`을 명시한다.
하위 Python `runner.run/compare` API의 기본은 기존 raw다. CLI 기본과 구분한다.
이번 연결의 [변경·검증·한계](smart_default_cli_20261005.md)를 참조한다.

### 기존 원형 모델의 학습/평가 순서

아래 학습/과거 결과 예시는 원형 raw 모델이다. 기본 V2는 저부하 Max-Pressure와 대기 보정을 함께 사용한다.

Python과 기존 프로젝트 의존성, SUMO가 설치된 상태에서 저장소 루트의 PowerShell로 실행한다.
다른 폴더에 있다면 먼저 이동한다.

```powershell
cd "C:\Users\James\.codex\worktrees\f73e\sumo_project"
```

이미 환경 파일이 준비되어 있으므로 재생성은 필요 없다. 상태만 확인하려면:

```powershell
python -B -m transferable_signal check --load
```

**여기부터는 차량이 실제로 움직이는 명령이며 사용자가 실행한다.**

### 1. 먼저 작은 환경에서 동작 확인

```powershell
python -B -m transferable_signal run --scenario train_x2_quiet --controller fixed --gui --out results/runs/transferable_smoke
```

SUMO가 열리고 실행된다. 완료 후 `metrics.json`, `signals.csv`, `tripinfo.xml`, `sumo-errors.log`를 확인한다.
충돌·teleport, 미진입·미완료 차량이 있으면 기록을 확인한 뒤 다음 단계로 넘어간다.
이번 개발에서는 이 운행 명령을 실행하지 않았다.

### 2. 공통 모델 학습

```powershell
python -B -m transferable_signal train --episodes 30 --seed 42 --out results/runs/transferable_train
```

`train`은 `train` 분류만 선택한다. 검증/최종 테스트를 선택하는 옵션은 제공하지 않는다.
30회는 시작용 실행 예시이며 충분한 학습 횟수나 성능 보장이 아니다. `training.csv`로 진행을 확인한다.
학습 후 `model.json`과 에피소드별 모델이 생성된다. 현재 workspace에는 검증된 seed43 모델이 있고 기본 명령이 이를 사용한다.
에피소드별 파일은 추론/평가용이며 optimizer·replay를 복구하는 학습 재개 파일은 아니다.
진행 정보는 에피소드마다 저장하며 코드 오류·사용자 중단은 `manifest.json`에 실패로 기록한다.
프로세스 강제 종료 시에는 마지막 저장된 에피소드 기록이 남는다.

### 3. 검증 세트에서 네 제어기 비교

```powershell
python -B -m transferable_signal compare --split validation --smart-policy raw --model results/runs/transferable_train/model.json --out results/runs/transferable_validation
```

동일한 출발 일정·차량 종류·SUMO seed·안전 제약으로 네 방식을 실행한다. 검증 수요6개 × 제어기4개 = 24회다.
모델 없이 `--controllers fixed webster max_pressure`로 기본 제어기만 실행할 수도 있다.
학습 횟수·모델 선택은 이 검증 세트에서 정하고, 최종 테스트 결과로 반복 튜닝하지 않는다.

### 4. 모델을 고정한 뒤 경주에서 재학습 없이 평가

```powershell
python -B -m transferable_signal compare --split test --smart-policy raw --model results/runs/transferable_train/model.json --out results/runs/transferable_gyeongju
```

경주 수요3개 × 제어기4개 = 12회다. `comparison.csv`와 각 실행의 원본 파일이 저장된다.
모델의 학습 도로망 해시에 최종 테스트 도로망이 있으면 실행을 거부한다.
다시 실행할 때는 `--out`에 새 폴더명을 쓴다. 기존 폴더는 덮어쓰지 않는다.

## 입력과 행동의 의미

신호 후보마다 다음 7개를 계산한다. 교차로마다 신호 후보 수는 달라도 특징 수는 같다.

1. 진입 차로의 대기 차량/저장 용량 평균
2. 진입 차로의 전체 차량/저장 용량 평균
3. 최근 유입률/차로당 포화교통류율 평균
4. 진출 차로의 차량/저장 용량 평균
5. 해당 신호가 녹색을 받지 못한 시간/우선 서비스 기준 시간
6. 현재 녹색 경과 시간/최대 녹색 시간
7. 현재 녹색 여부

비율은 0~2로 제한한다. 저장 용량은 차로 길이/설정 차량 간격(7.5m)으로 근사한다.
유입률은 최근60초 관측 차량 ID의 **도로 진입**으로 계산하며 같은 도로 내 차선 변경은 새 유입으로 세지 않는다.
초기60초는 실제 관측한 시간으로 나눈다. 신호5번의 미서비스 시간에는 황색·전체 적색도 포함한다.
미래 차량 출발 일정이나 SUMO 차량의 계획 경로를 정책 입력으로 읽지 않는다.

AI는 각 후보의 특징과 전체 후보 특징 평균을 받는 동일한 14→32→32→1 신경망을 사용한다.
신호 번호를 고정 출력하는 모델과 달리, 3개/4개 등 후보 수가 달라도 동일한 가중치로 계산한다.
5초마다 합법적인 후보 중 하나를 선택한다. 같은 신호를 반복 선택하면 녹색을 연장한다.
최소15초·최대60초, 황색3초·전체 적색2초를 별도 전환 장치가 강제한다.
180초 이상 녹색을 받지 못한 후보가 있으면 다음 합법적 전환에서 가장 오래 기다린 후보를 우선한다.
이는 모든 차량의 대기시간을180초 이하로 보장한다는 뜻은 아니다.

학습 보상은 각 교차로의 진입 차로 대기/용량 비율 평균의 음수이며 5초 동안 평균한다.
학습 알고리즘은 공유 Double DQN이다. 초보 모델의 행동은 성능 평가 결과가 아니며 충분한 검증이 필요하다.
이 원형 raw 설명에서는 Max-Pressure가 별도 비교기다. 현재 기본 V2는 저부하/대기 보정에 Max-Pressure를 사용한다.

## 비교의 가정과 기록

- Fixed: 모든 신호의 녹색25초, 설정된 순서로 순환.
- Webster: 실제 관측된 유입률로 매 사이클 녹색 시간을 재계산하는 제한된 근사 구현.
  포화교통류율 기본1800대/시/차로, 전환 손실은 황색+전체 적색으로 근사한다.
  과포화에서는 주기를 제한한다. 현장 보정된 Webster 또는 항상 최적이라는 주장은 하지 않는다.
- Max-Pressure: 진입 대기와 진출 대기의 차로 기반 압력. 공유 차로의 목적지 비율을 균등하게 가정한다.
  위의 최소/최대 녹색과 전환 규칙을 적용한 버전이며 원 이론의 처리량 보장을 그대로 주장하지 않는다.
- SmartSignal: 기본 CLI는 검증된 seed43/V2를 자동 선택한다. raw 실행에는 학습 모델 파일을 명시한다. 이전 단일 교차로 모델 및 무학습 초기화 파일은 거부한다.

모든 제어기는 SUMO의 차로 전체 관측을 사용한다. 현재는 실제 센서/E2 어댑터가 아니다.
실제 도입에는 동일 의미의 관측을 제공하는 센서 연결과 안전/성능 검증이 추가로 필요하다.
기본 데이터에 보행자·센서 고장·공사·학습 중 센서 잡음 증강은 포함하지 않았다.

각 실행은 코드·도로망·설정·수요·모델 해시, SUMO 버전과 seed를 기록한다.
대기행렬, 시스템 현재 대기의 합, 완료 차량 평균/95백분위 대기, 주행시간,
완료·미진입·미완료 수, 충돌·teleport, 신호 판단의 평균/95백분위 계산 시간을 저장한다.
계산 시간은 프로세스 시작과 관측 수집을 제외한 정책 판단 시간이며 신호 수로 나눈 값이다.
완료 차량 지표만으로 순위를 매기면 미완료 차량 때문에 왜곡될 수 있으므로 함께 확인한다.
CSV의 `completed`는 코드 실행 완료이며 안전 또는 성능 합격 표시가 아니다.

## 설정 변경과 재생성

`configs/transferable_signal.json`을 수정하고 새 출력 폴더에 생성한다.

```powershell
python -B -m transferable_signal prepare --out network/transferable_v2
python -B -m transferable_signal check --suite network/transferable_v2/suite.json --load
```

그 뒤 실행 명령에도 `--suite network/transferable_v2/suite.json`을 전달한다.
원래 경주 지도/모델/결과를 바꾸지 않는다. 번들은 상대 경로를 사용하므로 저장소와 함께 이동할 수 있다.
Colab에서도 SUMO 및 Python 의존성을 설치한 뒤 같은 명령을 GUI 옵션 없이 사용한다.

## 개발 검사

```powershell
python -B tests/test_transferable_signal.py
python -B tests/test_gyeongju_simple.py
python -B -m transferable_signal check --load
```

새 단위 검사는 가짜 환경·합성 배열을 사용한다. 신호 최소/최대 시간과 전환, 금지 녹색,
데이터 분리·수요 재현, 가변 후보 수, 모델 추론 일치, 가짜 환경에서의 학습 실행 연결을 확인한다.
실제 SUMO 연결은 3지·경주 환경에서 시간0의 관측과 종료·기록 API만 확인했다.
사용자의 첫 운행에서 동적 동작을 확인한 후 본 학습을 진행한다.

### 최종 확인 — 2026-10-02

| 명령/검사 | 결과 |
|---|---|
| `python -X utf8 -B tests/test_transferable_signal.py` | 14개 통과: 합성 데이터·가짜 환경, 실제 운행 없음 |
| `python -X utf8 -B tests/test_gyeongju_simple.py` | 기존 지도 정적 검사 4개 통과 |
| `python -X utf8 -B -m transferable_signal check --load` | 10개 도로망·30개 수요, SUMO 0초 로드 통과 |
| 3차로 3지·경주 TraCI 초기 관측/종료 | 시간0, 출발0대, 관측7개 특징 확인 |
| 새 Python 11개 파일 AST·`git diff --check` | 구문·공백 검사 통과 |

별도 검토 단계에서 학습/최종 테스트 분리, 허용 신호와 전환 시간, 평가 지표의 완료/미완료 구분을 확인했다.
3차로 3지 환경의 진출 차로 하나가 연결되지 않던 경고를 수정했다. 재생성 로그에 경고·오류가 없다.
학습 도로망에 최종 테스트가 포함된 모델의 평가 거부, 출력 덮어쓰기 거부, 실패 기록도 가짜 환경으로 확인했다.
마지막 실제 연결 기록은 `.codex-artifacts/transferable-final-zero-load-v2/checks.json`에 있다.
초기 점검 스크립트의 지표명 오타(`departed` → `departed_vehicles`)를 수정해 재확인했다.
실제 교통 흐름·학습 성능·전이 성능은 이번 검사의 대상이 아니며 아직 미검증이다.

### 사용자 시작 학습과 경고 점검 — 2026-10-03

사용자가 Fixed 동작 확인과 학습30회를 직접 실행했다. 동작 확인은207대 전원 도착·충돌/teleport0이며,
학습은3569업데이트 후 `results/runs/transferable_train/model.json`을 정상 저장했다.
이는 학습 실행 완료이며 성능 검증 완료를 의미하지 않는다.

- `Invalid characters for PoI ID 'Synthetic 0/1/2'`: 표시용 POI ID에 공백이 들어간 원인이다.
  빌더와 준비된 `train_chain/labels.add.xml`에서 밑줄로 바꾸고, 화면 없는 실행은 표시 파일을 읽지 않도록 수정했다.
  도로망·신호·수요·학습 모델은 바꾸지 않았다.
- 학습 전체 충돌0·teleport1건: `episode_0020`, `train_chain_directional`, 차량 `veh_00071`, 시간374초,
  `j2_E_in_1` → `link1_in`. 신호 기록에서는 해당 방향이350~380초 녹색이며 SUMO의 사유는 `jam`이다.
  따라서 적색 유지 때문에만 발생한 현상으로 설명할 수 없으며, 진출 도로 정체가 기록된 상황이다.
  SUMO는 기본300초 이상 막힌 차량을 이동시키는 기능이 있다.
  [공식 설명](https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html).
  학습 중 탐색·신호 정책·수요의 영향은 비교 검증에서 확인해야 한다. teleport를 성공 통행으로 해석하지 않는다.
- 이 학습은600초에서 끝나므로 미완료·미진입 차량이 있을 수 있다. 비교 실행은300초 정리 시간을 추가하고
  완료/미완료·teleport를 함께 기록한다. 완료 차량 평균 대기만으로 성능을 판단하지 않는다.

수정 후 `python -X utf8 -B tests/test_transferable_signal.py` 16개,
`python -X utf8 -B tests/test_gyeongju_simple.py` 4개 PASS.
수정된 표시 파일을 포함한 SUMO `--end 0` 로드에서 경고0, 기존 모델 로드와10개 도로망·수요 해시 검사 PASS.
Codex는 추가 운행·학습·평가를 실행하지 않았다. 진단 기록은
`.codex-artifacts/transferable-training-review-20261003.json`에 보존했다.
기존 모델로 위의 **검증 세트 비교** 명령을 실행해 네 방식의 결과를 확인한다.

### 첫 검증 결과와 다음 후보 — 2026-10-03

사용자가 검증24회를 완료했으며 전 실행 충돌/teleport0이다. 급제동 경고1건은
`valid_x3_directional/max_pressure`에서 시간413초, 차량 `veh_00290`에 기록됐다.
충돌은 기록되지 않았지만 급제동 경고도 관찰 항목으로 보존한다.

| 제어기 | 평균 대기행렬(대) | 도착/예정 합계 | 미완료 합계 |
|---|---|---|---|
| Fixed | 37.14 | 2529/2625 | 96 |
| Webster | 34.88 | 2556/2625 | 69 |
| Max-Pressure | 41.19 | 2475/2625 | 150 |
| SmartSignal | 38.23 | 2530/2625 | 95 |

평균 대기행렬은 같은900초의6개 시나리오 평균이다. 모든 제어기의 미진입 차량은0이며,
미완료는 실제 실행 종료 시 도착하지 못한 차량이다. 완료 차량 대기 평균만으로 순위를 매기지 않는다.
SmartSignal은 `valid_t2_directional`에서 Webster보다 낮은 대기행렬을 보였지만,
전체 평균에서는 Fixed·Webster 대비 개선을 입증하지 못했다.

후속 권장 후보는 코드·관측·보상·수요를 고정하고 학습 횟수를150회로 늘리는 한 번의 비교다.
학습 횟수 증가에 맞춰 기존 epsilon 일정도 늘어난다. 새 학습은 처음부터 시작하며 기존30회 모델의 재개가 아니다.
개선 여부는 실행 전 알 수 없다. 이 후보가 개선되지 않으면 반복 증량 대신 관측·보상·조건별 결과를 진단한다.

```powershell
python -B -m transferable_signal train --episodes 150 --seed 42 --out results/runs/transferable_train_150
python -B -m transferable_signal compare --split validation --controllers smart --smart-policy raw --model results/runs/transferable_train_150/model.json --out results/runs/transferable_validation_150
```

두 번째 명령은 새 SmartSignal만6회 검증한다. 기준선 결과는 기존
`results/runs/transferable_validation/comparison.csv`를 사용한다. 같은 동결 수요·도로망·seed·안전 제한을 유지할 때만
기존 기준선과 비교한다. 모델을 선택한 뒤 최종 경주 테스트를 실행한다.

후속150회 학습은 사용자가 완료했으며17969업데이트와 `transferable_train_150/model.json` 저장을 확인했다.
150개 에피소드 전체에서 충돌0·teleport3건(`episode_0120` jam, `episode_0135`/`episode_0145` yield)이 기록됐다.
현재 단계는 위의 두 번째 명령으로 새 모델의 검증6회를 실행하는 것이다. 학습 횟수 증가만으로 성능 개선을 판단하지 않는다.

### 150회 모델 검증 완료·경주 평가 후보 선택 — 2026-10-03

사용자가 새 모델의 검증6회를 완료했다. 원본 CSV·실행 manifest에서 동일 도로망·동결 수요·seed·신호 제한과
정확한150회 모델 사용을 확인했다. 로그 경고·충돌·teleport0이며, 모든 모델/원시 결과를 보존했다.

| 제어기 | 평균 대기행렬(대) | 미완료 합계 |
|---|---|---|
| Fixed | 37.144 | 96 |
| Webster | 34.882 | 69 |
| Max-Pressure | 41.185 | 150 |
| SmartSignal 30회 | 38.234 | 95 |
| SmartSignal 150회 | 35.396 | 71 |

150회 모델은30회보다 평균 대기행렬7.42% 감소, 도착2530→2554대를 보였다.
혼잡한4지 조건에서 개선됐지만 가벼운 수요 일부에서는 대기시간이 늘었다.
완료 차량 수로 가중한 평균 대기는150회65.839초, Fixed65.628초로 모든 지표에서 우월하지 않다.
Webster 대비 평균 대기행렬/미완료도 조금 높다. 단일 seed의6개 검증 조건 결과이며 범용 성능 보장이 아니다.

대기행렬과 미완료 개선을 기준으로 이번 후보2개 중150회 모델을 경주 평가 후보로 선택한다.
모델 해시·비교 요약은 `.codex-artifacts/transferable-validation-150-review-20261003.json`에 기록했다.
추가 튜닝 없이 경주 재학습 없는 적용 효과를 아래 명령으로 평가한다.

```powershell
python -B -m transferable_signal compare --split test --smart-policy raw --model results/runs/transferable_train_150/model.json --out results/runs/transferable_gyeongju_150
```

세 수요×네 제어기=12회다. 이 결과로 같은 최종 테스트에 반복 튜닝하지 않는다.
Codex는 검증 파일을 읽어 비교했으며 실제 경주 운행은 실행하지 않았다.

### 첫 경주 최종 평가 완료 — 2026-10-03

사용자가150회 모델을 고정해12회 평가를 완료했다. 재학습 없이 경주 세 교차로를 제어했고,
평균 대기행렬은 Fixed 대비19.05%, Webster 대비7.35% 낮았다. 도착 차량은1396/1597대로 네 방식 중 최다다.
전체 경고·충돌·teleport0이며 원시 기록과 고정 모델/수요의 일치를 확인했다.
한산한 수요에서는 Max-Pressure가 더 좋고, 종료 시201대가 미완료다.
도로망1개·가상 수요3종·수요별 단일 seed의 시작 실험 결과이며 현장/모든 환경의 최적성을 의미하지 않는다.
[전체 표·검증 증거·한계](transferable_final_evaluation_20261003.md)를 기준 기록으로 보존한다.
이번 최종 테스트로 반복 튜닝하지 않는다. Codex는 추가 운행·학습을 실행하지 않았다.
