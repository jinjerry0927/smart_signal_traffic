# SmartSignal 최종 연구 기준 v1

검증으로 선정한 기존 seed43 / load_gate_pressure_wait35(V2)를 동결했습니다.
새30초 후보는 재검증에서 기각했습니다. 기본 smart 명령은 같은 모델/규칙을 사용합니다.

## VS Code에서 실행

프로젝트 폴더를 열고 터미널에서 다음을 실행합니다.

```powershell
python -B -m transferable_signal run --scenario gyeongju_busy --controller smart --model results/runs/final_signal_wave10_20261007/release/model.json --out results/runs/smart_final_demo_01
```

SUMO 화면은 --gui를 추가합니다. 결과 폴더가 이미 있으면 새 이름을 사용합니다.

## 확정 범위

새48검증 조건144회와 미사용24최종 조건96회, 독립 원시 감사 이후 확정했습니다.
추가 학습/최종 결과 재선정은 없습니다. model.json은 학습 가중치, policy.json은 공통 규칙/안전 설정입니다.
release.json에 정확한 모델/정책/소스/근거 SHA와 성능·경고·한계를 기록했습니다.
최종 성능 기준 통과 여부: True.
가상 수요 연구 기준이며 현장 성능이나 모든 교차로의 최적성을 보장하지 않습니다.
