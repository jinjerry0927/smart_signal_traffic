# 새 도로망 적용 업데이트 발표 인계

작성 2026-10-29. 상태: 구조·문구·대본·시각자료·ChatGPT 인계 패키지 완료.
사용자 요청: 지금까지의 기록을 저장하고, 내일 간단한 교수님 발표를 우선 준비한다. PPT 자체는 사용자가 ChatGPT로 제작한다. 이번 자료 준비에서 추가 교통 실험이나 AI 정체 취약성 검토를 시작하지 않는다.

## 산출물

- [전체 ZIP](../presentation/unseen_update_20261029.zip): 약 0.90MiB, 46파일.
- [복사할 ChatGPT 요청문](../presentation/unseen_update_20261029/00_ChatGPT_제작요청.txt): 첨부물 설명·제작 조건·수치 해석·슬라이드별 원고·대본을 모두 포함한다.
- [본문 8장·부록 2장 구성과 대본](../presentation/unseen_update_20261029/01_슬라이드구성_발표대본.md): 본문 합계 300초.
- [교수님 질문 대응](../presentation/unseen_update_20261029/02_교수님_질문대응.md), [수치 정의](../presentation/unseen_update_20261029/03_수치정의_주의사항.md), [시각자료 목록](../presentation/unseen_update_20261029/04_시각자료_목록.md).
- assets: 실제 로컬 웹에서 내보낸 지도 3종과 대기 화면, 원시 비교표로 그린 그래프 7종, 기존 SUMO 좌표도 1장. PNG 12개와 SVG 10개다. 지도는 현장 사진이 아닌 SUMO 가상망이며, 도면별 축척이 다르다.
- data: CSV 8개와 정밀 수치 JSON. 전체 27조건, 망별 9조건, 망·수요별 3조건과 Webster 22짝을 구분한다.
- evidence: 확정 보고서·검증 해시·웹/정체 기록·도로망 설정 스냅샷. 전체 대용량 운행 로그는 원래 경로에 보존한다.

실제 PPTX·영상은 만들지 않았다. 웹 사진은 실행 전 0초/기본 300초 시연 설정이며, 성능 자료는 별도 900초 실험에서 계산했다.

## 발표의 근거와 범위

동결한 기존 학습 가중치와 V2 규칙을 새 가상 도로망 3종에 추가 학습 없이 적용했다. 도로망은 3종, 제어 신호 합계 4개, 교통 조건은 27개다. T/X·1~3차로 등 지원 범위 안의 새 기하 조합에 관한 결과다.

동일 27짝의 Fixed 대비 평균 대기행렬은 39.859 → 32.273대(−19.03%), 도착은 8,356 → 8,709대(+353)다. 각 망의 9조건 전체에서도 43.85%/19.38%/17.13% 감소했다. 세 감소율의 산술 평균으로 전체 감소율을 계산하지 않았다.

Max-Pressure 대비 큐 +2.51%, 학습 기여 기준 미달, Webster 5정체 실패/완료 22짝, 예정 9,037대 중 미완료 328대를 함께 밝힌다. ‘전부 통과’·‘학습의 순수 효과 입증’·‘모든 교차로/현장 성능 보장’으로 표현하지 않는다. 실패 후 검토를 거쳐 미시도 조건을 이어 수집한 서술적 비교임을 유지한다.

## 생성·검증

실행기: C:/Users/jinuk/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe, Python 3.12.14.
차트용 matplotlib은 프로젝트 환경을 바꾸지 않고 .codex-artifacts/presentation-python-20261029/에 설치했다. 지도는 실제 웹 SVG를 내보내 sharp로 PNG 형식 변환했다. 생성기와 검증기는 새 파일이다. 기존 평가 소스·입력·모델·결과는 변경하지 않았다.

실행한 명령(위 실행기의 -X utf8 -B 옵션 사용):

1. tools/build_update_presentation_assets.py
2. tools/report_unseen_transfer.py --verify --round 5
3. tools/verify_update_presentation_assets.py --package
4. tools/verify_update_presentation_assets.py (완성 ZIP 재검증)

- 생성: 봉인된 원본 CSV/요약 SHA, 162행, 완료 157/실패 5, 전체 27짝, 각 망 9조건/각 수요 칸 3조건, Webster 22짝 집계 PASS.
- 기존 보고서 재검증: 17개 동결 모델/정책/소스와 모든 162시도·원시/부모/검토/소스 SHA·쌍 평균/도착 수·미완료 프로토콜 표기 PASS. 교통 운행 0.
- 독립 검사: 생성기 계산 함수를 재사용하지 않고 원본 CSV에서 다시 집계해 모든 편집 CSV·수치·분모를 대조했다. 8본문/2부록/300초, 파일 참조, 12PNG 로드·10SVG 파싱, 근거 스냅샷 일치 PASS.
- 시각 확인: PNG 12개를 전부 열어 한글·축·단위·범례·값·잘림을 확인했다. PPT 자체의 레이아웃 검증을 한 것은 아니다.
- ZIP: 46항목을 해제해 각 파일 SHA와 manifest를 대조, 전부 일치. 생성된 최종 ZIP을 덮어쓰지 않는다. --package 없이 원래 폴더와 ZIP 전체를 재검증할 수 있다.
- 검증 기록: .codex-artifacts/presentation-verification-20261029.json.
- ZIP SHA-256: 1fcf02ecb01739eb681f6166a39937224e8aa9e52c0ed310026745e8241e3b92.

## 보존과 다음 작업

발표 전 체크포인트는 .codex-artifacts/checkpoint-before-presentation-20261029.json에 기록했다. 실험·진단·웹 기록을 보존했고, 커밋·푸시·외부 전송은 하지 않았다.

다음은 사용자가 ZIP과 요청문으로 ChatGPT에서 PPT를 제작하고 5분 발표를 연습하는 것이다. 별도 후속 연구로 Webster 정체의 구조적 원인과 우리 AI의 같은 상황 취약성, Max-Pressure 및 규칙 진단 대비 차이를 검토한다. 이 발표 자료 준비 중에는 해당 운행이나 보완을 추가로 실행하지 않았다.
