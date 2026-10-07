# Smart Signal

경주 도로를 대상으로 예측·협력형 AI 신호제어를 직접 학습하고 검증하는 졸업작품.
현재 실행 기반은 **기존 단일 교차로 Double DQN/E2와 범용 SmartSignal V2**다. [발표용 3교차로 웹 관제·Pi 추론 연결](docs/prototype_web_pi_20261007.md)을 준비했다. 물리 Pi 검증은 접속 주소 확인 후 진행한다.
현재 실험 구간은 **산업로 용강네거리 → 근화여고네거리 → 승삼네거리**다.
2026-10-02 사용자 선택을 반영한 [지도 정리형 환경](network/gyeongju_simple/README.md)을 기본 진입점으로 사용한다.
정적 신호와 가상 수요를 사용하며, 기존 금성로 구간도 비교 자료로 보존한다.
범용 모델의 가상 수요 비교·재학습 없는 적용을 검증했다. 실측 교통량 확보가 어려워 현장 검증은 보류했으며, 물리 Pi 연결 확인은 남아 있다.

**발표용 관제 실행:** `python -X utf8 -B demo_web_pi.py` → 브라우저 `http://127.0.0.1:8000`. 세 교차로의 차량·신호·대기열, 실행/일시정지/정지, 저장된 최종 비교를 보여준다. 기존 단일 교차로 대시보드는 보존했다.

**노트북·데스크톱 공유:** [GitHub에서 받고 실행하기](docs/github_two_computers.md). 실행용 동결 모델과 비교 요약을 포함하며, 대규모 실험 로그는 로컬 보존한다.

**범용 SmartSignal:** [실행 순서와 설정 안내](docs/transferable_signal.md), [검증 모델의 기본 명령 연결](docs/smart_default_cli_20261005.md).
공통 관측·신호 전환, 분리된 학습/검증/테스트, 비교·CSV 저장을 구성하고 검증된 seed43/V2를 기본 `smart` 명령에 연결한다.
2026-10-07 새 검증144회·미사용 최종 비교96회와 독립 감사를 마쳐 **seed43/V2를 최종 연구 기준v1로 확정**했다. [확정 모델·신호 규칙·실행 명령](results/runs/final_signal_wave10_20261007/release/README.md), [성능·검증·남은 한계](docs/final_signal_wave10_20261007.md). 기본 명령은 그대로 사용한다.
2026-10-04부터 사용자가 Codex의 순차 시뮬레이션 실행·검토를 허가했다. 모델·정책/기존 결과를 보존한다.

## 간단히 이해하기

1. **도로 만들기**: SUMO에 경주 교차로 3개를 만들고 차량을 움직인다.
2. **AI 학습하기**: 교통 상황을 보고 신호를 선택하도록 학습시킨다.
3. **관제 연결하기**: 웹 지도에서 상태를 보고, Pi에서 AI 판단을 실행한다.
4. **효과 확인하기**: 같은 교통량으로 기존 신호와 비교하고 전시한다.

처음에는 총괄, AI·시뮬레이션, 웹·연결의 세 채팅으로 시작해도 충분하다.
한 번에 작은 목표 하나를 맡기고 **구현 → 검사 → 수정 → 완료 기록**을 반복한다.
평소 구현은 Sol, 어려운 설계·오류 분석은 Astra Extra High를 권장한다.
1세트 기존 기반 정비 결과는 [완료 기록](docs/set1_completion.md)에 정리했다.
2세트 도로망·수요·통행 검증은 [완료 기록](docs/set2_completion.md)에 정리했다.
산업로 구간 교체·검증 상태는 [제작 기록](docs/industrial_rebuild.md)을 참조한다.

## 다음 작업 시작

1. [STATE.md](STATE.md)에서 현재 상태와 다음 목표를 확인한다.
2. [LOOP.md](LOOP.md)의 목표·검증 반복 규칙을 읽는다.
3. [GOAL.md](GOAL.md)의 완료 조건과 다음 세트의 선행 조건을 확인한다.

Codex에서 다음 작은 목표를 정리하는 요청:

```text
$loop-engineering STATE.md와 LOOP.md를 읽고 다음 세트의 첫 작은 목표를 정리해줘.
범위·완료 조건·담당·검증 방법을 간단히 알려줘.
```

문서 작성만으로 지속 목표나 예약 작업이 실행되지는 않는다.
최신 범위와 6주 계획은 [프로젝트 차터](docs/project_charter.md)를 따른다.

## 빠른 실행

저장소 루트에서 실행한다. Python, SUMO와 requirements.txt의 패키지가 필요하다.
필요한 경우 `python -m pip install -r requirements.txt`로 Python 의존성을 설치한다.
SUMO는 별도 설치하며, Windows 기본 위치는 `C:/Program Files (x86)/Eclipse/Sumo`다.
다른 위치라면 SUMO_HOME을 실제 설치 폴더로 설정한다.

```powershell
# 설치 상태
python --version
sumo --version

# Git에 포함된 평시 시나리오로 기본 환경 확인 (GUI 없이 300초 시뮬레이션)
python test_env.py

# E2 관측, PyTorch/NumPy 추론 일치, 웹 상태 응답 확인
python tests/test_e2_observation.py
python tests/test_inference_parity.py
python tests/test_dashboard.py
python tests/test_signal_control.py
python tests/test_evaluation.py
```

화면이 필요하면 `python test_env.py --gui --seconds 300`을 사용한다.
검사 실행은 학습 모델이나 기존 평가 결과를 덮어쓰지 않는다.

### 현재 실험 구간: 산업로 지도 정리형

`network/gyeongju_simple/open_sumo.cmd`를 더블 클릭하면 실행 위치와 관계없이 열린다.

```powershell
sumo-gui -c "C:\Users\James\.codex\worktrees\f73e\sumo_project\network\gyeongju_simple\demo.sumocfg"
```

북동쪽으로 이어지는 완만한 곡선과 정돈된 교차부에 차로·좌직우회전·신호를 명시했다.
건물과 추가 갈림길 없이 핵심 교차로3개를 유지했다.
환경 생성과 로드만 확인했으며, 운행 실험과 학습은 사용자가 진행한다.
좌표·차로·신호 수정은 [지도 정리형 안내](network/gyeongju_simple/README.md)를 따른다.

### 보존 구간: OSM 기반 산업로 정밀형

```powershell
# 포함된 차량 시나리오를 바로 열고 GUI의 실행 버튼을 누른다.
sumo-gui -c network/gyeongju_industrial/demo.sumocfg

# 평시·북행 편중·남행 편중의 승용차/화물차/버스 운행 검증
# 기존 출력 폴더가 있으면 새 이름을 사용한다.
python tools/run_gyeongju_industrial.py --out .codex-artifacts/industrial-check
python tests/test_gyeongju_industrial.py

# 검증한 북행 편중 시나리오 다시 보기
sumo-gui -c .codex-artifacts/industrial-check/northbound_peak/view.sumocfg
```

[도로망 안내](network/gyeongju_industrial/README.md)에 지도 출처·차로 가정·재생성 방법을 기록했다.
신호와 교통량은 실측값이 아닌 실험 가정이며, AI와 기존 대시보드는 아직 연결하지 않았다.

### 보존 구간: 금성로 교차로 3개 (기존 SET2)

```powershell
# 회전 35개, 이동 경로 42개, 가상 수요 3종 검증
# 기존 출력 폴더가 있으면 새 이름을 사용한다.
python tools/verify_gyeongju_corridor.py --out .codex-artifacts/set2-check
python tests/test_gyeongju_corridor.py

# 생성한 가상 수요를 SUMO GUI에서 보기
sumo-gui -n network/gyeongju_corridor/corridor.net.xml -r .codex-artifacts/set2-check/balanced/demand.rou.xml
```

지도 출처·모델 가정·재생성 방법은 [도로망 안내](network/gyeongju_corridor/README.md)를 참조한다.
기존 단일 교차로 모델·대시보드와의 연결은 아직 구현하지 않았다.

### SUMO와 기존 대시보드

```powershell
python demo.py --dashboard --mode rl --scenario asymmetric --duration 600
python demo.py --mode fixed --scenario asymmetric --duration 600
python demo.py --mode webster --scenario asymmetric --duration 600
```

SUMO GUI의 실행 버튼으로 시뮬레이션을 시작한다.
대시보드 기본 주소는 `http://127.0.0.1:8000`이다.
SUMO에서 네트워크 설정을 직접 열려면 `network/intersection.sumocfg`를 사용한다.
기본 경로는 저장된 평시 시나리오이므로 선행 학습으로 임시 경로 파일을 만들 필요가 없다.

### 로컬에서 Pi 통신 구조 확인

첫 번째 터미널:

```powershell
python edge_server.py --weights results/smart_signal_e2.npz --host 127.0.0.1
```

두 번째 터미널:

```powershell
python demo.py --hil --dashboard --host 127.0.0.1 --scenario asymmetric --duration 600
```

이 검사는 실제 Pi 성능 측정과 구분한다.
실물 연결은 [Pi 실행 가이드](docs/raspberry_pi_hil_guide.md),
화면 점검은 [대시보드 가이드](docs/dashboard_guide.md)를 참조한다.

## 코드와 자료 위치

| 위치 | 역할 |
|---|---|
| smart_signal.py | Double DQN 학습, 기본/E2 관측 선택 |
| signal_environment.py | 공통 순환 제어·최소/최대 녹색·황색·전체 적색 |
| e2_observation.py | E2 검지기 관측 생성 |
| network/, scenarios/ | 단일 교차로와 저장된 평가 수요 |
| baselines.py, evaluate.py, evaluation_support.py | 기준선, 동결 출발 일정, 매초·완료 차량 지표 |
| demo.py | SUMO 데모, 로컬/Pi 추론 연결 |
| edge_server.py, export_weights.py | NumPy 추론 서버와 가중치 변환 |
| dashboard.py, dashboard.html | 기존 단일 교차로 웹 대시보드 |
| tests/, test_env.py | 실행 검증 |
| results/ | 보존할 기준 모델과 기존 결과 |
| presentations/ | 발표 PPT와 원고; 영상은 로컬 보존 |
| archive/ | 지난 학기 코드·문서·발표 생성기 |

새 기능 때문에 필요한 부분부터 분리한다. 학습·실행 경로를 한꺼번에 재배치하지 않는다.
도구 지침은 [AGENTS.md](AGENTS.md)를 따른다.

## 모델과 평가를 다룰 때

- 기본 모델은 29D, E2 모델은 53D 관측을 사용하며 행동은 Keep/Next 두 가지다.
- 원본 모델·로그·차트는 [results 안내](results/README.md)에 정리되어 있다.
- 기존 평가의 avg_waiting_time은 시스템 전체 대기시간의 시점별 평균이다. 차량별 평균 대기시간과 다르다.
- 기존 그래프와 발표의 개선율은 지난 구현의 결과다. 최종 프로젝트의 성능으로 재사용하지 않는다.
- 공통 제어는 녹색 15~60초, 황색 3초, 전체 적색 2초, 5초마다 Keep/Next 판단이다.
- Fixed/Webster는 실제 녹색 경과 시간으로 전환한다. 새 평가는 명시적 차량 ID·출발 시각을 동결한다.
- 새 CSV는 시스템 총 대기시간의 매초 평균과 완료 차량별 평균 대기시간을 구분한다. 미도착·미진입도 함께 기록한다.
- 학습·평가는 기본적으로 `results/runs/` 아래 새 폴더에 저장한다. 명시한 출력 폴더가 이미 있으면 중단한다.
- 학습 `--route`는 해당 `--out_dir` 안에서 생성한다. `--resume`도 새 출력 폴더를 사용하고 동일 제어 버전의 체크포인트만 받는다.
- 가중치 변환은 기존 방식이므로 별도 출력 경로를 지정한다. 기존 모델은 새 규칙에서 연결 검사만 했으며 성능은 다시 평가해야 한다.

```powershell
# 짧은 동일 수요 비교. --out-dir을 생략하면 고유 폴더를 자동으로 만든다.
python evaluate.py --episodes 1 --seconds 300 --scenarios medium --skip-plots
# GUI 없이 기준선 실행
python demo.py --headless --mode webster --scenario medium --duration 300
```

학습과 평가 옵션은 `python smart_signal.py --help`, `python evaluate.py --help`로 확인할 수 있다.
장시간 재학습은 첫 실행 점검 뒤 별도 목표로 수행한다.

## 환경과 이력

2026-09-13 확인 환경: Python 3.11.0, SUMO 1.26.0, sumo-rl 1.4.5,
PyTorch 2.11.0, NumPy 2.4.4, Gymnasium 1.3.0, Matplotlib 3.10.9.
이 PC의 현재 PyTorch에서는 CUDA를 사용할 수 없었다. 학습 시간은 짧은 실행으로 측정한다.
위 목록은 확인 환경이며 새 의존성 고정 파일을 뜻하지 않는다.

[저장소 정리 기록](docs/repository_cleanup_2026-09-13.md)에 변경·검증 내용을 남긴다.
[지난 학기 자료 안내](archive/README.md)와 [발표 자료 안내](presentations/README.md)에서 과거 자료를 찾을 수 있다.

기반 도구: [Eclipse SUMO](https://eclipse.dev/sumo/), [SUMO-RL](https://github.com/LucasAlegre/sumo-rl).
