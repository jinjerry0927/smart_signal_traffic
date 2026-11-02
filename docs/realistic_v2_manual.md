# Realistic V2 사용자 직접 실행 안내 (Windows / VS Code / PowerShell)

아래 SUMO·웹 실행은 **사용자가 직접 수행하는 후속 검증**이다. 구현 작업에서는 실행하지 않았다.
기존 연구 모델·입력·결과를 덮어쓰지 않는다. 결과 경로는 매번 새 이름을 쓴다.
새 체계의 AI는 재학습하지 않은 **미검증 실험 전이**이고 Pi 원격 추론은 Legacy V1만 지원한다.

## 1. 설치와 환경 확인

저장소 루트 `C:\Users\James\Documents\GitHub\sumo_project`를 VS Code로 연다.
Python 3.11, [SUMO 1.26.0](https://eclipse.dev/sumo/)의 Windows 설치본과 bin PATH가 필요하다.
현재 저장소 `requirements-demo.txt`의 NumPy 2.4.4 / sumolib 1.26.0 / traci 1.26.0을 사용한다.
Torch는 V2 웹·추론·정적 검사에 필요 없다. 신규망 `--unseen`의 봉인 Python 3.12 환경과는 분리한다.

```powershell
Set-Location 'C:\Users\James\Documents\GitHub\sumo_project'
py -3.11 -m venv .venv-realistic
.\.venv-realistic\Scripts\python.exe -m pip install -r requirements-demo.txt
$signalPython = (Resolve-Path '.\.venv-realistic\Scripts\python.exe').Path
& $signalPython -c "import sys,numpy,sumolib,traci; print(sys.version); print(numpy.__version__)"
Get-Command sumo,sumo-gui,netconvert
& $signalPython -X utf8 -B -m realistic_signal check
& $signalPython -X utf8 -B -m unittest discover -s tests -p test_realistic_signal.py
```

기존 환경에 위 라이브러리가 있으면 venv 생성/설치는 생략하고 `$signalPython`을 그 Python의 절대 경로로 지정한다.
`check`는 망·차로·그룹·경로·해시·동결 모델을 파일에서만 검사한다. `--load` 옵션이 없다.
체크 실패 시 모델·봉인 해시를 변경하지 말고 오류를 제공한다.

## 2. 기존 Legacy V1 웹 실행

```powershell
& $signalPython -X utf8 -B -m signal_demo web --signal-system legacy-v1 --port 8000 --out results/runs/legacy_v1_manual_web
```

[로컬 관제 화면](http://127.0.0.1:8000)을 직접 연다. 경주/기존 T형/X형과 수요·제어기를 선택하고 시작한다.
Legacy V1의 원격/Pi 연결 기능은 기존 [Pi 안내](prototype_web_pi_20261007.md)를 따른다.
웹 서버 종료는 PowerShell에서 Ctrl+C다. V2를 시작하려면 별도 터미널이나 다른 포트를 사용한다.

## 3. Realistic V2 웹 실행과 체계 선택

```powershell
& $signalPython -X utf8 -B -m signal_demo web --signal-system realistic-v2 --port 8002 --out results/runs/realistic_v2_manual_web
```

[V2 관제 화면](http://127.0.0.1:8002)을 직접 연다. 도로망 기본값은 `Realistic V2 · 경주 3교차로 (실험)`이다.
같은 서버의 도로망 선택에서 경주 Legacy V1/T형/X형으로 바꿀 수도 있다. 실행 중에는 정지 후 변경한다.
`선택: Realistic V2 · 적용 대기`는 준비 상태이고, 실제 V2 환경의 첫 프레임을 받아야 `적용: Realistic V2`가 된다.
V2는 PC 추론만 허용한다. `--unseen`과 함께 지정할 수 없다.

## 4. 현시 관찰

배속 1배로 실행하고 교차로 이름을 눌러 확대한다. 이동별 직진·좌회전·우회전 화살표와 신호 색을 확인한다.
카드의 현시 ID, 종료 이동 황색/소거, 다음 현시를 함께 본다. 현시 글자에 마우스를 올리면 우회전 제한이 표시된다.
용강 `N_combined → NS_through → S_combined`에서 N직진 유지/N좌회전 종료,
이후 S직진 유지/N직진 종료/S좌회전 시작을 확인한다. 승삼은 S/N 순서가 반대다.
AI/Max-Pressure는 안전 후보를 자유롭게 선택하므로 관찰 첫 실행은 **Fixed**를 권장한다.
실제 직진이 끊겨 보이면 `signals.csv`의 같은 연결 인덱스와 비교한다.

## 5. 일반/혼잡 수요와 SUMO GUI 직접 실행

UI의 `한산한 교통`(`quiet`)은 일반 부하 확인용이며 실측 정상 교통량이 아니다.
`혼잡한 교통`은 `busy`, `한 방향 집중`은 `directional`이다. V2 수요는 Legacy 경주의 출발 일정과 경로를 그대로 복사했다.

웹 없이 SUMO GUI에서 같은 V2 제어기를 실행하려면:

```powershell
$signalRunStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$signalRun = "results/runs/realistic_v2_busy_fixed_$signalRunStamp"
& $signalPython -X utf8 -B -m realistic_signal run --scenario busy --controller fixed --horizon 900 --gui --out $signalRun
& $signalPython -X utf8 -B -m realistic_signal audit --run $signalRun
```

`--scenario quiet` 또는 `directional`로 바꾼다. `--controller webster`, `max_pressure`, `smart`도 실제 구현되어 있다.
`smart`는 동결 가중치를 사용하는 실험 전이이며 검증된 V2 모델이 아니다. 매 실행 전에 새 `$signalRunStamp`와 출력 이름을 지정한다.
GUI 없이 실행하려면 `--gui`만 뺀다. 신호등을 우클릭해 현시를 확인하고 TLS link index 표시로 CSV 인덱스와 대조할 수 있다.
직접 망만 여는 정적 프로그램(15초 녹색)은 위 TraCI Fixed(25초 목표)와 시간 계획이 다르므로 위 명령을 사용한다.

Legacy V1의 CLI 실행도 기존 구현을 사용한다:

```powershell
$signalRunStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
& $signalPython -X utf8 -B -m transferable_signal run --scenario gyeongju_busy --controller fixed --gui --out "results/runs/legacy_v1_busy_fixed_$signalRunStamp"
```

## 6. 결과 위치

- 웹: `--out` 아래의 시간+UUID별 새 하위 폴더. 화면의 저장 경로를 복사한다.
- V2 CLI: `$signalRun`에 지정한 새 폴더. 이미 존재하면 실행을 거부한다.
- `manifest.json`: 체계·네트워크/spec/수요·가중치·소스 해시·상태·실험 전이 여부.
- `signals.csv`: V2는 **1초마다** 3교차로의 실제 적용 상태와 요청/전환/강제전환을 저장한다. 각 행은 `[time_s,time_s+1)` 구간이다.
- `decisions.csv`, 웹의 `explanations.jsonl`: 선택·추론/보정 기록.
- `metrics.json`, `tripinfo.xml`, `sumo.log`, `sumo-errors.log`: 회계·운행 원본·오류.

원래 `results/runs/final_signal_wave10_20261007/`나 모델 디렉터리를 출력 경로로 쓰지 않는다.

## 7. 저장 결과 검증

```powershell
# 웹 실행이면 화면에 나온 개별 실행 폴더로 바꾼다.
& $signalPython -X utf8 -B -m realistic_signal audit --run $signalRun
Get-Content "$signalRun/manifest.json" -Encoding utf8
Get-Content "$signalRun/metrics.json" -Encoding utf8
Get-Content "$signalRun/sumo-errors.log" -Encoding utf8 -Tail 40
```

`audit`는 SUMO를 실행하지 않는다. 저장된 모든 초의 요청을 전환기에 다시 넣고 상태·소거·최소/최대녹색·마스크를 비교한다.
완료 실행의 길이/교차로 누락, 충돌/teleport, 예정·진입·도착·잔류·미진입 회계와 tripinfo 도착 수도 검사한다.
망/spec/수요 해시가 다르면 거부한다. 정지/실패 실행은 완료로 통과시키지 않는다.
이 검사는 **기록된 제어 상태와 SUMO 지표의 일치**를 확인하며 현장·보행 안전 인증이나 모델 성능 검증이 아니다.
사용자 정지/실패 실행에는 파일이 일부 없을 수 있으므로 오류와 함께 원본을 제공한다.
V1의 `signals.csv` 형식에는 이 V2 감사를 적용하지 않는다.

## 8. 전달할 로그와 다음 검증

실행 명령 전체, Python 버전, 선택 수요/제어기/포트와 함께 개별 실행 폴더의 위 파일들을 제공한다.
특히 `manifest.json`, `signals.csv`, `decisions.csv`, `metrics.json`, `sumo-errors.log`와 audit 출력을 전달한다.
화면과 신호가 다르면 교차로 ID·시각·이동 인덱스, 스크린샷도 함께 전달한다.

먼저 quiet/busy/directional에서 차로변경 실패·정체·미진입·충돌·teleport·서비스 누락을 점검한다.
그다음 동일 일정에서 Fixed/Webster/Max-Pressure/Smart를 별도 폴더에 실행하여 차량 회계와 지체를 비교한다.
이번 구현의 검사 통과를 성능 향상이나 재학습 불필요의 근거로 사용하지 않는다.
