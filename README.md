# Smart Signal

경주 도로를 대상으로 예측·협력형 AI 신호제어를 직접 학습하고 검증하는 졸업작품.
현재 실행 가능한 기준 구현은 **단일 교차로 Double DQN + E2 관측 + Pi 추론 + 웹 대시보드**다.
경주 교차로 3개와 지도 관제센터는 앞으로 구현할 범위다.

## 간단히 이해하기

1. **도로 만들기**: SUMO에 경주 교차로 3개를 만들고 차량을 움직인다.
2. **AI 학습하기**: 교통 상황을 보고 신호를 선택하도록 학습시킨다.
3. **관제 연결하기**: 웹 지도에서 상태를 보고, Pi에서 AI 판단을 실행한다.
4. **효과 확인하기**: 같은 교통량으로 기존 신호와 비교하고 전시한다.

처음에는 총괄, AI·시뮬레이션, 웹·연결의 세 채팅으로 시작해도 충분하다.
한 번에 작은 목표 하나를 맡기고 **구현 → 검사 → 수정 → 완료 기록**을 반복한다.
평소 구현은 Sol, 어려운 설계·오류 분석은 Astra Extra High를 권장한다.
첫 작업은 아래 G0다. 모든 계획과 기술을 미리 외울 필요는 없다.

## 다음 작업 시작

1. [STATE.md](STATE.md)에서 현재 상태와 다음 목표를 확인한다.
2. [LOOP.md](LOOP.md)의 목표·검증 반복 규칙을 읽는다.
3. [GOAL.md](GOAL.md)의 G0 범위로 기존 기능 재현과 측정 기준을 점검한다.

Codex에서 첫 목표를 시작하는 요청:

```text
/goal $loop-engineering LOOP.md와 GOAL.md를 읽고 G0를 진행해줘.
기존 Smart Signal의 실행 가능 여부와 관측·행동·신호 전환·평가 지표를 점검하고,
검증 결과와 미해결 문제를 기록한 뒤 STATE.md를 갱신해줘.
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
```

화면이 필요하면 `python test_env.py --gui --seconds 300`을 사용한다.
검사 실행은 학습 모델이나 기존 평가 결과를 덮어쓰지 않는다.

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
| e2_observation.py | E2 검지기 관측 생성 |
| network/, scenarios/ | 단일 교차로와 저장된 평가 수요 |
| baselines.py, evaluate.py | 기존 비교 신호와 평가 |
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
- 신호 전환 제약, 기준선 동작, 지표 정의는 다음 목표에서 실행 근거로 검증한다.
- 학습·평가·가중치 변환의 기본 출력은 기존 파일을 덮어쓸 수 있다. 실행 전에 출력 분리를 확인한다.
- 새 학습에는 --out_dir와 --route를 별도 위치로 지정한다. 동시 학습에서 경로 파일을 공유하지 않는다.
- 현재 evaluate.py는 결과 저장 위치가 고정되어 있다. 본 평가 전에 출력 분리부터 구현한다.

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
