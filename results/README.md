# 기존 Smart Signal 모델과 결과

이 폴더는 지난 학기 단일 교차로 모델과 원본 결과를 보존한다.
새 프로젝트의 최종 성능을 입증하는 자료는 별도 실험에서 확보한다.

| 파일 | 용도 |
|---|---|
| smart_signal.pth | 기본 관측 29D용 학습 모델 |
| smart_signal_e2.pth | E2 관측 53D용 학습 모델 |
| smart_signal_e2.npz | 같은 E2 모델의 NumPy 추론 가중치 |
| training_log.csv | 기존 학습 기록 |
| evaluation.csv, evaluation_e2.csv | 기존 평가 원본 |
| evaluation_by_scenario.png, evaluation_e2_by_scenario.png | 해당 평가의 시나리오별 차트 |

checkpoint_*.pth와 e2_monitor*.csv는 로컬 중간 산출물이며 Git에서 제외한다.
기존 평가 코드의 avg_waiting_time은 시스템 전체 대기시간을 시간축으로 평균한 값이다.
차량별 평균 대기시간으로 표기하지 않는다. 지표와 기준선 타당성은 G0 후속 작업에서 점검한다.

기존 학습·평가·export 스크립트는 기본 출력 위치를 덮어쓸 수 있다.
새 학습은 smart_signal.py의 --out_dir와 --route를 별도 경로로 지정한다.
evaluate.py의 결과 저장 경로는 아직 코드에 고정되어 있으므로 기존 결과 보존과 출력 분리를 먼저 처리한다.
