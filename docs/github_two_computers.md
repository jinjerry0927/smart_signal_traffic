# 노트북·데스크톱에서 같은 프로젝트 사용하기

GitHub는 커밋한 파일을 공유한다. 각 컴퓨터의 실행 프로그램, 설치 라이브러리, 작업 중인 파일은 별도다.

## 다른 컴퓨터에서 시작

1. GitHub Desktop에서 `jinjerry0927/smart_signal_traffic`을 Clone하거나 기존 프로젝트에서 Pull한다.
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
