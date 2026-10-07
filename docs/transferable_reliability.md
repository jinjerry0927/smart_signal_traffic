# 공통 SmartSignal 재현성 확인 배치

2026-10-03 사용자가 후속 진행을 요청했다. 기존150회 seed42 모델과 결과를 보존하고,
동일한 공통 모델을 서로 다른 시작 난수(seed43·44)로 두 번 더 학습한다.
교차로마다 별도 모델을 만드는 단계가 아니다. 실제 학습·운행은 사용자가 VS Code에서 실행한다.

## 목표와 한도

- 질문: 초기값·탐색이 달라도 효과가 반복되고, 새로운 출발/경로 패턴에서도 유지되는가?
- 후보: 기존150회 seed42 모델 + 새150회 seed43·44 모델, 총3개. 추가 학습 예산300에피소드.
- 입력·보상·행동·신호 제한·학습 도로망은 현재 규격을 유지한다.
- 검증: 기존 검증 도로망2개 × 교통 종류3개 × 출발/경로 표본5개 =30조건.
  표본0은 기존 파일과 seed를 그대로 보존하고 표본1~4는 새 동결 수요다.
- 평가: 조건마다 기본 제어기3개와 AI모델3개를 실행한다. 총180회이며 기본 제어기는 중복 실행하지 않는다.
- 기존 경주 최종 평가 결과는 보존하며 이번 학습·모델 선택에 사용하지 않는다.
  후속 최종 전이는 새로운 미사용 도로망/수요를 따로 정한 뒤 진행한다.
- 무제한 학습이나 자동 성능 개선 루프는 실행하지 않는다. 이 배치 후 결과를 검토한다.

## 사전 판단 기준

주 지표는30조건 평균 대기행렬이며 도착/미완료를 함께 확인한다.
독립 학습 모델들의 성능 편차와 교통 종류별 악화도 별도로 보고한다.
이 배치의 개선 목표는 각 모델의 평균 대기행렬이 Fixed보다5% 이상 낮고,
도착 수는 Fixed보다 적지 않으며, 충돌·teleport가0인 것이다.
한 교통 종류의 평균 대기행렬이 Fixed보다10% 넘게 악화하면 원인 진단 대상으로 남긴다.
Webster·Max-Pressure 결과도 함께 비교하며 위 기준을 전체 최적성/현장 안전 합격으로 해석하지 않는다.
숫자는 이번 연구 배치의 목표이며 보편적인 AI 정확도 기준이 아니다.

## VS Code 실행

이 프로젝트 폴더를 열고 **터미널 → 새 터미널**에서 다음 명령을 실행한다.

```powershell
& .\tools\run_transferable_reliability.ps1
```

스크립트는 프로젝트 폴더로 이동하고 seed43 학습 → seed44 학습 →30조건 비교를 순서대로 수행한다.
seed42의 기존 모델을 재사용한다. 이미 완료한 새 학습은 결과를 검증해 건너뛰고,
중단된 새 학습은 같은 폴더의 유효한 `checkpoint.pt`에서 재개한다.
완료 모델의 훈련 설정·도로망/수요 해시·실행 버전은 기존 seed42와 대조한다.
학습 입력·신호 제어 코드가 변경된 경우에도 서로 다른 실험을 섞지 않도록 중단한다.
기존 평가 출력 폴더는 덮어쓰지 않는다. 평가를 다시 실행하려면 `-ValidationOut`에 새 폴더를 지정한다.
학습만 할 때는 `-Stage train`, 비교만 할 때는 `-Stage validate`를 전달한다.

```powershell
& .\tools\run_transferable_reliability.ps1 -Stage train
& .\tools\run_transferable_reliability.ps1 -Stage validate
```

PowerShell 실행 정책이 스크립트를 막으면 아래 Python 명령을 순서대로 실행한다.
정책을 전역 변경할 필요는 없다.

```powershell
python -B -m transferable_signal train --episodes 150 --seed 43 --out results/runs/transferable_train_150_seed43
python -B -m transferable_signal train --episodes 150 --seed 44 --out results/runs/transferable_train_150_seed44
python -B -m transferable_signal compare-models --suite network/transferable_reliability/suite.json --models results/runs/transferable_train_150/model.json results/runs/transferable_train_150_seed43/model.json results/runs/transferable_train_150_seed44/model.json --out results/runs/transferable_reliability_validation
```

`comparison.csv`의 `model_label`/`model_seed`로 모델을 구분한다.
각 실행의 원본 로그·차량 기록·모델/수요 해시도 저장한다. 평가 실패/중단을 완료로 기록하지 않는다.

## 학습 중단과 재개

새 학습은 완료한 에피소드마다 추론용 `model_episode_XXXX.json`과 재개용 `checkpoint.pt`를 저장한다.
재개 파일은 모델·target·optimizer·replay·난수 상태를 함께 복원한다.
Ctrl+C로 한 에피소드 중간에 멈추면 마지막 완료 에피소드부터 재개하며, 중간 원본 파일은 보존한다.
`model.json`은 추론용이므로 기존 seed42 모델을 이어 학습할 수는 없다.

```powershell
python -B -m transferable_signal train --episodes 150 --seed 43 --resume results/runs/transferable_train_150_seed43/checkpoint.pt --out results/runs/transferable_train_150_seed43
```

`--episodes`는 재개 후의 총 목표 횟수다. `--stop-after 10`은 이번 호출에서10회 완료 후 잠시 멈춘다.
목표 횟수를 늘려 재개하더라도 저장된 원래 탐색 감소 일정은 유지한다.
동일 폴더의 유효한 재개 파일·동일 수요/설정·seed·학습 옵션만 허용한다.
한 출력 폴더에서 학습을 동시에 두 번 실행하지 않는다.

## 환경 준비와 검사

확장 검증 파일은 준비된 번들을 사용한다. 다른 표본 수로 만들 때는 새 폴더에 생성한다.

```powershell
python -B -m transferable_signal prepare-validation --samples 5 --out network/transferable_reliability_v2
python -B -m transferable_signal check --suite network/transferable_reliability_v2/suite.json --load
```

학습 seed는 모델 초기값·탐색·SUMO 난수를 바꾸지만, 기존 동결 경로 파일 자체를 바꾸지는 않는다.
출발/경로 다양성은 `prepare-validation`이 만든 서로 다른 표본에서 확인한다.
검증용 표본을 학습 데이터로 사용하지 않는다.

실제 학습과 동적 평가의 결과는 사용자 실행 전까지 없다.
개발 검증은 가짜 환경의 재개 일치 검사, 수요 재현/분리 검사, 평가 연결 검사와 SUMO0초 로드까지만 수행한다.

## 준비 단계 검증 결과 — 2026-10-03

```powershell
python -X utf8 -B tests/test_transferable_signal.py
python -X utf8 -B tests/test_transferable_resume.py
python -X utf8 -B tests/test_transferable_validation.py
python -X utf8 -B tests/test_gyeongju_simple.py
python -B -m transferable_signal check --suite network/transferable_reliability/suite.json --load
```

- 검사16+9+12+4개, 총41개 통과. 학습/평가 연결은 가짜 환경을 사용했다.
- 검증2도로망·30수요의 SUMO0초 로드 통과, 경고/오류 없음.
- 격리 공간의 가짜 Python 실행기로 학습 재개→새 학습→비교 순서, 완료 모델 재사용,
  기존 평가 출력 거부, 다른 학습 설정/수요 거부를 확인했다.
- CLI 인자 연결, 변경 Python5파일 구문, PowerShell 구문, `git diff --check` 통과.
- 독립 검토에서 발견한 중단 시 manifest 손상과 다른 조건의 완료 모델 재사용 문제를 보강하고 재검증했다.
- 기존 seed42 모델 SHA256 `4d3e7fc8ca45999a71c761f19ec9a5c527fe440ccffa438c0985c3f4fef8be98` 유지.
- 남은 작업: 사용자가 추가300에피소드 학습과180회 비교를 실행한 뒤 실제 성능/안정성을 판정한다.
