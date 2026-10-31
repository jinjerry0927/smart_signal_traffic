# 노트북·데스크톱에서 같은 프로젝트 사용하기

GitHub는 커밋한 파일을 공유한다. 각 컴퓨터의 실행 프로그램, 설치 라이브러리, 작업 중인 파일은 별도다.

## 다른 컴퓨터에서 시작

1. GitHub Desktop에서 `jinjerry0927/smart_signal_traffic`을 Clone하거나 기존 프로젝트에서 Pull한다. 현재 저장소는 [smart_signal_traffic](https://github.com/jinjerry0927/smart_signal_traffic)이며 예전 `sumo_project` 주소는 자동 연결된다. 로컬 폴더명은 `sumo_project`로 유지해도 된다.
2. Python3.11과 SUMO1.26.0을 설치한다. 이번 실습은 SUMO1.26.0으로 검증했다.
3. VS Code에서 **그 컴퓨터의** `sumo_project` 폴더를 연다. 이름이 다른 폴더로 Clone해도 된다.
4. VS Code 터미널에서 아래 명령을 실행한다.

```powershell
python -m pip install -r requirements-demo.txt
sumo --version
python -X utf8 -B demo_web_pi.py
```

브라우저에서 `http://127.0.0.1:8000` → Smart Signal / PC → 실험 시작.
8000이 사용 중이면 `python -X utf8 -B demo_web_pi.py --port 8001`을 사용한다.
현재 프로토타입에는 학습용 Torch가 필요하지 않다. 학습을 진행할 때는 기존 `requirements.txt`를 사용한다.

## 작업 전·후

- 작업 시작 전: GitHub Desktop **Fetch origin → Pull origin**.
- 작업 후: 변경 내용 확인 → Commit → Push origin.
- 다른 컴퓨터로 이동: Pull origin 후 실행.
- 두 컴퓨터에서 같은 파일을 동시에 수정하면 충돌이 생길 수 있다. 먼저 진행한 컴퓨터에서 Commit/Push한 뒤 다음 컴퓨터에서 Pull한다.

터미널로 받을 경우:

```powershell
git pull --ff-only
```

## 2026-11-01 업데이트와 데스크톱 최적화 합치기

이번 공유에는 새 교차로 3종의 도로망·시험/진단 도구·PC 웹 시험 모드,
기존 웹의 Pi 연결 사전 확인·실행 중 배속 변경, 발표 자료를 포함한다.
동결 모델·정책·기존 결과는 변경하지 않았다.

데스크톱에서 이미 최적화한 파일이 있다면 Pull 전에 변경을 확인하고 로컬 커밋으로 보존한다.
별도의 백업 브랜치를 만들어 두어도 된다. 미커밋 파일을 원격 파일로 강제 교체하지 않는다.

```powershell
git status
# 필요한 최적화 파일을 확인해서 추가하고 커밋한 뒤:
git fetch origin
git log --oneline --left-right HEAD...origin/master
git merge origin/master
```

양쪽에 새 커밋이 있으면 `git pull --ff-only`는 중단된다. 이때 위처럼 변경을 비교한 뒤
병합한다. 서로 다른 줄은 자동으로 합쳐질 수 있지만 같은 줄은 충돌을 해결해야 한다.
충돌이 없어도 최적화의 의도와 새 기능이 함께 유지되는지 차이와 관련 검사를 확인한다.
이번 컴퓨터에서는 데스크톱의 미공유 최적화 내용을 볼 수 없으므로 병합 완료로 주장하지 않는다.

일반적인 코드 정리·실행 속도 개선이라면 병합 후 검사가 통과할 때 다시 최적화할 필요는 없다.
관측·행동·보상·모델·신호 규칙·도로망을 변경했다면 기존 성능 수치를 그대로 적용하지 않고,
해당 비교를 다시 검증한다. 새 학습·최적화가 필요한지는 그 결과로 판단한다.
이번 새 교차로 시험의 동결 원본은 보존하고, 정책 변경과 추가 실험은 별도 버전/출력 경로를 쓴다.

새 웹 시험 모드는 아래 두 개의 봉인 결과 파일도 함께 공유한다.
전체 원시 로그의 재감사는 원래 PC의 기록이 필요하며 이 두 파일만으로 재감사할 수 없다.

- `results/runs/unseen_transfer_20261028/reviewed-followup-summary.json`
- `results/runs/unseen_transfer_20261028/reviewed-followup-comparison.csv`

기존 시연은 위 Python 3.11 환경을 사용한다. `demo_web_pi.py --unseen`은 시험의 봉인 검사를
그대로 사용하므로 **Python 3.12.14 / NumPy 2.3.5 / SUMO 1.26.0** 및 동결된 SUMO 실행 파일
SHA256까지 일치해야 한다. 정확한 값은 `docs/unseen_transfer_20261028/evaluation-freeze-v2.json`에 있다.
`requirements-demo.txt`의 기존 Python 3.11 / NumPy 2.4.4 환경과는 다르다.
다른 환경에서는 시작이 거부될 수 있으므로 봉인을 임의로 고치지 말고 환경부터 대조한다.

### 이번 공유 전 검증

원본 작업 폴더와 Git 스테이지를 추출한 독립 폴더 모두에서 아래 검사를 통과했다.
기존 시연 검사는 Python 3.11.9 / NumPy 2.4.4, 새 교차로·발표 검사는
Python 3.12.14 / NumPy 2.3.5 환경을 사용했다.

```powershell
# 기존 시연 환경에서 각각 실행: 19개, 16개 통과
python -X utf8 -B -m unittest discover -s tests -p test_signal_demo.py
python -X utf8 -B tests/test_transferable_default_cli.py
# 새 교차로 환경에서 각각 실행: 28개, 발표 수치/ZIP 무결성 통과
python -X utf8 -B -m unittest discover -s tests -p 'test_unseen*.py'
python -X utf8 -B tools/verify_update_presentation_assets.py
git diff --cached --check
```

- 총 63검사 통과. 웹 2개 HTML의 인라인 JavaScript도 `node --check` 통과.
- 보호 대상 258파일의 작업본/추출본 바이트 일치. 발표 ZIP 46파일의 해시 일치.
- 추출본에서 Smart/PC/60배/30초로 경주와 새 3망을 순차 실행해 4회 완료,
  각 충돌·teleport 0을 확인했다. 짧은 기능 확인이며 성능 재평가나 전체 차량 도착 검증은 아니다.
- 초기 기본 `python`은 NumPy가 없어 검사 import에 실패했다. 설치된 위 환경으로 다시 실행해 통과했으며,
  기본 Python에 의존성을 새로 설치한 것으로 기록하지 않는다.
- 처음 스테이지 검사에서 생성 SVG의 원래 경로 공백과 PDF의 텍스트 판정이 발견됐다.
  `.gitattributes`에 생성 SVG 공백 규칙과 PDF/PPTX 바이너리 보존을 명시한 뒤 재검사했다.
  봉인 발표 파일의 내용은 바꾸지 않았다.
- 로컬 상세 기록: `.codex-artifacts/github-handoff-20261101-twg2j1xu/.codex-artifacts/`의
  `presentation-verification-20261029.json`, `handoff-smoke.json`.
- 남은 범위: 데스크톱의 최적화 내용 비교·통합 검사, 다른 컴퓨터의 새 웹 모드 환경 일치,
  물리 Pi 연결 확인. 다음 작업은 데스크톱 변경을 로컬 커밋으로 보존하고 병합하는 것이다.

## 공유한 실행 자료

- 현재 소스·교차로/차선/수요 설정·검증 도구·실행 문서.
- 기본 seed43 모델: `results/runs/transferable_train_150_seed43/model.json`.
- 최종 발표 모델·정책·메타데이터: `results/runs/final_signal_wave10_20261007/release/`.
- 관제의 저장된 비교표: `results/runs/final_signal_wave10_20261007/final_summary.json`.
- `.gitattributes`가 위 동결 자료와 소스의 바이트를 보존한다. 해시 오류를 피하려고 임의로 JSON 정렬이나 줄바꿈을 변경하지 않는다.

대규모 원시 운행 기록·학습 체크포인트·로컬 검증 산출물은 원래 PC에 보존한다.
위 최종 비교는 저장된 연구 요약이다. 과거 전체 실험의 원시 감사에는 원래 PC의 기록이 필요하다.
새 운행 결과는 각 컴퓨터의 새 `results/runs/` 폴더에 저장되며 Git으로 자동 공유되지 않는다.

## Pi 파일 만들기

새 이름으로 내보낸다. 기존 폴더를 덮어쓰지 않는다.

```powershell
python -X utf8 -B -m signal_demo bundle --out results/exports/smart_signal_pi_01
```

그 뒤 [Pi 연결 안내](prototype_web_pi_20261007.md#pi-연결-주소-확인-후)의 절차를 따른다.
Pi 주소가 확인되기 전에는 실제 Pi 연결이 완료된 것으로 설명하지 않는다.

## 공유 전 검증

원래 개발 폴더와 지정 GitHub 폴더의 파일·모델·수요 해시를 확인했다.
Git 스테이지의 트리를 별도 폴더에 추출해서 신규17검사·기본CLI16검사와 실제 경주3교차로30초 실행을 통과했다.
대상 폴더의 기존 NumPy/PyTorch 추론100개 일치와 dashboard 검사도 통과했다.
검증 명령:

```powershell
python -X utf8 -B -m unittest discover -s tests -p test_signal_demo.py
python -X utf8 -B tests/test_transferable_default_cli.py
python -X utf8 -B tests/test_inference_parity.py
python -X utf8 -B tests/test_dashboard.py
git diff --cached --check
```

기존 대상 폴더의 개인 `docs/pm_handoff.md`는 보존하며 이번 커밋에 포함하지 않는다.
공유 구성/검증은 `STATE.md`에 기록했다. 원격 반영 여부는 GitHub의 master와 완료 메시지에서 확인한다.
