# 새 교차로 제작·평가 준비·비교 실험 실행

시험 시작: 2026-10-28. 최종 확인: 2026-10-29. 상태: 제작·평가 준비·비교 시도 정리·PC 웹 시험 모드 완료. 전체 성능 목표/학습 기여는 통과하지 않았다.

## 실행 목표 카드

- 목표: 확정한 세 도로망의 신호·통행·관측을 확인하고, 평가 도구의 별도 검토 후 동결 모델 비교를 연속 수행한다.
- 권한: 사용자가 제작→평가 준비→검토→비교를 요청했고, 검토 방식은 ‘Codex가 별도 검토 기록을 남긴 뒤 연속 실행’으로 답했다.
- 기준: 기존 [protocol.json](protocol.json), [freeze.json](freeze.json). 모델·규칙·관측·신호 안전·기준선 소스 17개를 보존한다.
- 변경 범위: `tools/unseen_transfer_*.py`, `tools/verify_unseen_transfer.py`, 관련 검사·새 입력·새 출력·진행 문서.
- 새 입력: `network/unseen_transfer_20261028/`. 새 출력: `results/runs/unseen_transfer_20261028/`.
- 완료 조건: 3망과 27수요 로드·신호 충돌/공유 차로/전 차로 서비스 검사, 실제 OD·차종 통행과 관측·신호 원시 재계산, 평가 도구 검사·별도 검토, 12예비·162최종 비교와 원시 감사·조건별 결과 기록.
- 별도 검토: 생산 제어기의 함수를 호출하지 않는 신호 시계·7특징·신경망 순전파·6제어 선택 재계산 도구와 소스 검토 기록을 사용한다.
- 중단: 오류·충돌·teleport·입출력 식별 불일치·원시 기록 불일치 시 다음 운행을 중단하고 원인을 확인한다. 성능 미달은 숨기지 않고 계획된 비교를 완료한다.
- 재개: 완료된 운행 경계·같은 입력/평가 소스/실행 환경에서만 허용한다. 실패한 운행은 자동 재시도하지 않는다.
- 범위 제외: 추가 학습·새 규칙 선정·웹/Pi 변경·커밋·푸시·외부 전송.

## 기능 검사와 비교 한도 구분

이번 사용자의 추가 요청인 ‘신호/통행/관측 호환성 검사’를 위해, 별도 저부하 OD·차종 명단으로 새 도로망당 Fixed 600초 기능 검사 1회씩 총 3회를 수행한다.
이는 27개 최종 수요의 성능을 확인하거나 모델·규칙을 조정하기 위한 실험이 아니다.
기존 비교 한도인 검증 환경 예비 12회 + 최종 162회 =174회는 유지한다.
따라서 이번 실행의 실제 교통 운행 계획은 기능 검사 3회 + 비교 174회 =177회다. 27개의 0초 로드는 교통 운행으로 세지 않는다.
이 구분은 첫 새 도로망 차량 운행 전에 기록했으며 기존 성능 기준과 모델을 바꾸지 않는다.

## 실제 실행 환경

- Python: 번들 런타임 3.12.14, NumPy 2.3.5.
- SUMO·netconvert·Python 도구: 로컬 설치 1.26.0.
- 기본 Python/저장소 가상환경에 패키지를 설치하지 않고 실행 도구의 로컬 SUMO 경로 탐색을 사용한다.
- 현재 frozen factory로 seed43/wait35 로드와 17개 기준 파일 동일성을 확인했다.

## 준비 검사

- 새 기능 검사 7개 PASS: 3망/전 접근 차로 서비스, 수요 재현성, 변경 입력 거부·덮어쓰기 차단, 독립 시계/6정책 재계산, 규칙 진단의 학습 순전파 차단, 검토 없는 최종 실행 차단, 주 성능과 학습 기여 판정 분리.
- 기존 이력·동결 검사 9개 PASS.
- 이력 검사에는 생성 후 새 수요를 과거 이력으로 잘못 재분류하지 않도록 이미 동결한 history를 검증·사용하는 작은 변경을 했다. 동결된 감사 도구와 history/protocol/freeze 파일은 보존했다.
- 새 실행 소스 4개 AST 확인 PASS.

## 도로망·호환성 검사 완료

- 삼거리: 신호1개/3녹색 후보, 실제 최단 접근 차로134.8m.
- 3:1차로 사거리: 신호1개/4녹색 후보, 실제 최단 접근 차로118.4m.
- 연결2교차로: 신호2개/각4녹색 후보, 양방향 연결 차로129.6m.
- 27개 최종 수요의 SUMO0초 로드 PASS. 전 접근 차로 서비스·보호 신호·공유 차로·과거 입력 중복 제외·모든 예정 OD의 경로/차종 접근을 확인했다.
- 실제 기능 검사3회/각600초에서18/18,36/36,90/90대 도착. 모두 승용차·버스·트럭을 포함하며 총144대 전원 도착, 충돌·teleport·경고0.
- 매초 원시 관측/차량 지표·신호 시계·정책 재생 PASS, 모든 녹색 후보 실제 관찰 PASS.
- 입력은 input-freeze.json에 고정했다. 모델·규칙·원래 관측 코드는 변경하지 않았다.

## 평가 준비·별도 검토 완료

예비8번째 운행의 검증기가 Webster의 NumPy 실수 산술을 Python 목록 실수 산술로 재계산해5초 올림 경계에서 불일치했다.
후속 운행을 중단하고 원인을 확인했으며, 생산 제어기와 원시 기록을 보존한 채 검증기만 수정했다.
새 회귀 검사를 포함한9검사를 통과했고, 기존8개 완료 원시 운행을 v2검증서로 재감사했다. 차량 재운행0회다.
남은4예비를 이어 기존 검증 환경의12회 전체를 확인했다. 예비 충돌·teleport·경고0, raw모델의 x3 조건에 잔류1대가 있어 그대로 기록했다.

[별도 평가 검토](evaluation-review.md)와 evaluation-review-v2.json의 등록을 완료했고 본 비교162회를 시작했다.
정확한 원인과 보존된 이전 버전은 pilot-verifier-fault.json 및 review_versions/v1/에 있다.
수정 전후 도로망 제작·원래 추론 생산 코드·입력·모델·규칙·실행 환경은 동일하다.

## 실제 실행 명령

다음 실행기의 `-X utf8 -B` 옵션으로 실행했다:
`C:/Users/jinuk/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`.

```text
tests/test_unseen_transfer.py
tools/unseen_transfer_build.py prepare
tools/unseen_transfer_build.py zero-load --out .codex-artifacts/unseen-transfer-zero-load-20261028
tools/unseen_transfer_experiment.py compatibility
tools/unseen_transfer_experiment.py prepare-evaluation
tools/unseen_transfer_experiment.py pilot
tools/unseen_transfer_experiment.py recover-pilot
tools/unseen_transfer_experiment.py pilot --resume
tools/unseen_transfer_experiment.py record-review
tools/unseen_transfer_experiment.py final
```

최초 pilot 명령은 검증 불일치로 실패했음을 보존했다. 수정 후 prepare-evaluation은 v2 파일을 새로 만들었고 기존 v1을 덮어쓰지 않았다.
`python -X utf8 -B tests/test_unseen_transfer_audit.py`도9검사 PASS했다.
본 비교 결과·최종 감사·기존 자료 보존 결과는 완료 후 추가한다.

## 본 비교 중단과 별도 후속 수집

본 비교127회는900초 완료·독립 원시 검증을 통과했다. 첫 호출은99회 완료 경계에서45분 한도로 일시정지했고 같은 동결 조건으로 재개했다.
128번째 `zt_pair_short_busy_s00/webster`는503초에 버스의300초 대기/yield teleport를 기록해504초 관측 직후 해당 운행과 전체 배치를 중단했다.
충돌0/teleport1이며34회는 아직 실행하지 않았다. 실패 원시·manifest·batch를 그대로 보존했다.
[진단 JSON](teleport-diagnosis.json)은505시점/202결정의 신호·관측·정책 독립 재계산 일치와 버스 경로의 차로 연속성을 기록한다.
짧은 연결 차로 혼잡과 상·하류 녹색 시점 불일치에 부합하지만 미세한 양보 판정의 직접 원인은 확정하지 않았다.

[별도 중단·후속 검토](teleport-review-01.md) 후 아직 시도하지 않은34회만 `followup_round01/`에 수집한다.
원래 본 시험을 자동 재개하지 않으며 실패를 성공/완료900초 지표로 만들지 않는다. 후속 배치도 같은 중단 규칙을 적용한다.
전체 시도 한도162회, 기능/예비 포함177회, 추가 학습/파라미터 변경0을 유지한다.
원래27조건 전체 주 목표 통과는 판정하지 않고 완료 조건의 서술 통계·누락/실패·학습 기여를 각각 공개한다.

후속01은5회 완료 뒤 두 번째 혼잡 표본의 Webster teleport로 중단했다. 전체132완료/2실패/28미시도 경계에서 [검토02](teleport-review-02.md)를 남기고 미시도28회만 `followup_round02/`에 이어 수집한다.
두 번째 실패는622프레임/250결정 재생이 일치했고300초 연속 대기·충돌0/teleport1을 확인했다. 이 승용차는 연결로의1→0 차로 변경이 필요하므로 첫 버스 실패의 차로 연속성과 구분한다.
후속 실행의 보호 검사5개 PASS, 원래809파일 변경0·후속 시작814파일 중 의도한 이력 검사1개만 변경된 것을 확인했다. 사용자 웹·실행 파일은 보존했다.

실제 추가 명령은 `tools/unseen_transfer_followup.py prepare/run --round 1`, 동일한`--round 2`, 별도 검토 원문 `--note` 지정이다.
`tools/render_unseen_networks.py`로 실제 SUMO 차로 좌표의 [도로망 도면](road-layout-preview.png)을 만들고 이미지로 열어 표시와 글자·차로 색상·축척 설명을 확인했다.

## 재개·최종 수집과 웹 단계 완료

사용량 중단 뒤 확인한 실제 상태는153완료/5Webster실패/미시도4개·프로세스 없음이었다. 중단 시 이미 시작된 후속04가 더 진행된 파일 기록을 우선했다.
[검토05](teleport-review-05.md) 후 마지막4개만 수집했고,162개 전체는157완료/5실패/미시도0이다.
별도 각 중단/검토 원문과 원시를 보존했다. 오류/teleport를 자동 재시도하거나 원래 실패 배치를 성공으로 바꾸지 않았다.

`tools/unseen_transfer_followup.py report --round 5`, `tools/report_unseen_transfer.py`, 동일 도구의`--verify --round 5`로 모든162행·원시/부모/검토/소스 SHA·짝별 평균/변화율/도착 수를 재검증했다.
[최종 결과](results.md):Smart27/27,Fixed 대비 대기행렬−19.03%(27짝)·Webster−20.07%(완료22짝)·Max-Pressure+2.51%(27짝), 규칙 진단 대비+2.61%로 학습 기여 목표 미충족이다.
도착은Smart8,709대/Fixed8,356대/Max-Pressure8,387대다. 대기행렬과 처리량의 상충을 기록하고 사전 기준을 바꾸지 않는다.

사용자의 후속 범위인 [PC 웹 시험 모드](web-trial.md)를 별도 최대6회 검사 한도로 구현/검증했다. 실제5회(완료4/정지1), 충돌/teleport/경고0이며 새3망900초는 동결 지표·신호·행동·통행과 일치했다.
원래177교통시도와 웹5회를 합친182시도를 구분하고 웹 재현을 새 독립 성능 시험으로 세지 않는다. 추가 학습·정책 변경·실패 재시도0이다.
