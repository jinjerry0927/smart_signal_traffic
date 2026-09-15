# 프로젝트 차터 — 재정립 (2026-06-02)

> RL 기반 적응형 교차로 신호제어 — **SUMO 시뮬레이션 전용** 방향으로 재설정

---

## 1. 왜 재정립했나 (배경)

기존 계획은 국토교통부/ITS CCTV 실시간 영상에서 차량을 인식(YOLO)해 신호제어로 연결하는 것이었다.
실증 과정에서 다음 한계가 확인되었다.

- 사거리에 **동·서·남·북 4방향 CCTV가 모두 존재하지 않음** → 차로별 진입량 산출 불가
- 공개 ITS API 카메라는 고속도로/국도 위주, 도심 4지 신호 사거리 커버리지 부족
- **차량 인식 자체는 가능**하나, 그 카운트를 신호제어로 연결하는 폐루프 구성이 데이터 측면에서 불가능

→ **결정**: 실증(CCTV) 줄기는 보류하고, **SUMO 안에서 다양한 교통상황에 대해 최적 신호를 학습시키고 현행 고정신호와 비교**하는 것으로 1학기 목표를 좁힌다. CCTV 코드는 삭제하지 않고 `archive/perception_track/`에 보존(발표 시 "한계 분석" 근거).

---

## 2. 목표 (재설정)

**다양한 교통상황에서 최적 신호제어 정책을 강화학습(DQN)으로 학습시키고, 현실적 고정신호 기준선(baseline)과 정량 비교하여 개선 효과를 입증한다.**

- 1학기: **추상 4지 교차로 (v2 중심: 3차선 × 4-phase 보호좌회전)** 에서 완결
- 2학기: 실제 교차로 1곳을 SUMO로 모델링하여 확장

### 비교 기준선(baseline) 전략

실제 도로의 적응형 신호 주기는 **공개 관측이 불가능**하고 교차로·시간대마다 다르다.
따라서 "현행 신호"를 **현실적 고정신호의 평균치 + 이론상 최적 고정주기**로 정의하고, RL이 이를 능가함을 보인다.

| Baseline | 정의 | 의미 |
|---|---|---|
| **Fixed-Avg** | 표준 고정주기(예: 사이클 120~160s, 균등 분할) | "평범하게 운영되는 신호" |
| **Webster** | 각 시나리오 수요로 계산한 이론 최적 고정주기 | "고정신호가 낼 수 있는 최선" — 가장 공정한 적수 |
| **Actuated**(선택) | SUMO 내장 감응식 TLS | "기존 스마트 신호"와의 비교 |

> 학술적 논리: *"실세계 적응신호 타이밍은 공개 관측이 불가능하므로, 이론상 최적 고정신호(Webster)와 비교한다."*

### "다양한 교통상황" 시나리오 5종 (평가용 동결)

| 시나리오 | 방향별 수요(veh/h) | 목적 |
|---|---|---|
| 한산 (low) | ~300 | 저부하에서 과도한 전환 방지 확인 |
| 평시 (medium) | ~600 | 일반 상황 |
| 피크 (high) | ~1100 | 고부하 처리량 |
| 비대칭 (asymmetric) | 주방향 1100 / 부방향 300 | 출퇴근 편중 대응 |
| 과포화 (saturated) | ~1400+ | 용량 초과 시 안정성 (v2 학습 실패 원인 영역) |

학습은 매 에피소드 랜덤 수요(도메인 랜덤화)로, **평가는 고정 seed 시나리오 5종**으로 수행한다.

---

## 3. 부서 구조 (PM 산하 4팀)

에이전틱 엔지니어링: 각 팀을 독립 작업 단위로 두고, **인터페이스(데이터 계약)** 로만 연결한다.
한 팀의 산출물이 다음 팀의 입력이 되도록 경계를 명확히 한다.

> **운영 방식 (확정)**: 이 규모에선 4개 상시 에이전트는 과함 → **PM(오케스트레이터)이 직접 주도**하고,
> 경계가 분명하고 무거운 단발 작업만 해당 부서 서브에이전트로 위임한다. 부서는 **책임·산출물 경계**로 활용.

> **명명 규칙 (2026-06-02 확정)**: 최종 모델 = **SmartSignal**. 환경 = `network/`, 학습 = `smart_signal.py`,
> 산출물 = `results/smart_signal.pth`. (구 v1·v2 명칭 폐기, v1은 `archive/v1/`)

```
                        ┌─────────────────────────┐
                        │   PM (총괄 / 오케스트레이터)  │
                        │  범위·마일스톤·통합·리스크    │
                        └────────────┬────────────┘
          ┌──────────────┬──────────┴──────────┬──────────────┐
          ▼              ▼                     ▼              ▼
   ① 시뮬레이션 환경팀   ② 강화학습팀          ③ 베이스라인·평가팀  ④ 시각화·보고팀
     (SimEnv)          (RL Agent)          (Baseline & Eval) (Viz & Report)
```

### 데이터 계약 (팀 간 인터페이스)

| 계약물 | 생산 | 소비 | 형식 |
|---|---|---|---|
| **시나리오 명세** | ① SimEnv | ②③ | `.rou.xml` + 메타(방향별 veh/h, seed) |
| **학습 모델** | ② RL | ③④ | `.pth` (state_size, action_size, num_green_phases 포함) |
| **평가 결과** | ③ Eval | ④ | `results/evaluation.csv` (시나리오 × {Fixed,SmartSignal} × 메트릭) |

---

### ① 시뮬레이션 환경팀 (SimEnv)

- **역할**: SUMO 네트워크·교통 시나리오·재현성(seed)·설정 관리. RL/평가가 딛고 설 "무대".
- **1학기 할일**
  - [x] `network/` 네트워크 검증 (4 green phase, state 29D — 스모크테스트 통과)
  - [x] 트래픽 생성을 `scenarios.py`로 모듈화 (학습=`random_demand`, 평가=`EVAL_SCENARIOS`)
  - [x] 평가용 시나리오 5종 동결 → `scenarios/eval/*.rou.xml` (low/medium/high/asymmetric/saturated)
  - [x] 시나리오 메타(방향별 veh/h) 스키마 = `EVAL_SCENARIOS` dict
- **산출물**: `network/`, `scenarios.py`, `scenarios/eval/` (5종) ✅

### ② 강화학습팀 (RL Agent)

- **역할**: state/action/reward 설계, DQN 학습 파이프라인, 학습 안정성, 체크포인트.
- **1학기 할일**
  - [x] `smart_signal.py` 정비 (state 29D / Keep·Next action 문서화) — 개명·경로정리 완료
  - [ ] **과포화 발산 원인 분석** (`archive/training_history/v2_failed_saturation.csv` — reward가 -1920 고착) 후 대책 (reward 정규화, 수요 상한, max_green 등)
  - [ ] **처음부터 재학습** → `results/smart_signal.pth` 확보
  - [ ] 학습 곡선 수렴 확인 (단, 판단은 evaluate.py 고정조건으로)
- **산출물**: `smart_signal.py`, `results/smart_signal.pth`, `results/training_log.csv`

### ③ 베이스라인·평가팀 (Baseline & Eval)

- **역할**: 공정한 기준선 구현 + 표준 메트릭으로 통계 비교. **비교의 설득력을 책임진다.**
- **1학기 할일**
  - [x] `evaluate.py`를 SmartSignal(29D, Keep/Next) + 고정신호 사이클 baseline으로 일반화
  - [x] **시나리오 5종 루프 + 페어드 seed**(Fixed/RL 동일 트래픽) → mean±std + 개선율 (M1 ✅)
  - [x] `baselines.py`: Webster(수요 기반 최적 고정주기) 추가 + 3자(Fixed/Webster/SmartSignal) 평가 (M2 ✅)
  - [x] throughput(도착차량) 메트릭 추가 — TraCI `getArrivedNumber` 매 시뮬스텝 누적, 표·차트에 "높을수록 좋음" 방향으로 반영 (M3 ✅)
- **산출물**: `evaluate.py`, `baselines.py`, `results/evaluation.csv`(120행: 5종×8ep×3모드), `results/evaluation_by_scenario.png`

> **M2 결과 (8ep 평균 대기시간, Fixed 대비 개선율)** — 5종 전부 Webster·SmartSignal이 Fixed 능가:
>
> | 시나리오 | Fixed | Webster | SmartSignal |
> |---|---|---|---|
> | low | 240.7 | 24.0 (+90%) | 107.7 (+55%) |
> | medium | 469.7 | 242.0 (+48%) | 191.2 (+59%) |
> | high | 3013 | 1590 (+47%) | 2367 (+21%) |
> | asymmetric | 1626 | 840 (+48%) | 356 (+78%) |
> | saturated | 3105 | 2985 (+4%) | 2475 (+20%) |
>
> **핵심 기술 결정(평가 공정성):**
> - **teleport=300** — sumo_rl 기본(off, -1)은 빡빡한 고정주기가 교차로 박스를 영구 교착(흡수상태)시키는 시뮬 아티팩트 발생. SUMO 기본값(300s) 복원해 3모드 동일 조건. (`evaluate.py` run_episode)
> - **Webster 실무 보정** — min_green=15 강제 시 가벼운 좌회전 phase 과배정 → 직진 굶음(medium 포화도~0.9 → 교착). 포화도 상한 `target_x=0.85` + 결정스텝 올림(ceil) 양자화로 직진 녹색 확보. 미적용 시 medium에서 Webster가 Fixed보다 5배 악화됨(실측). (`baselines.py webster_timing`)
> - 학습 모델(`results/smart_signal.pth`)은 teleport off로 학습됐으나, 정책 자체는 불변이라 평가 teleport on과 무관(재학습 불필요).
>
> **다음(M3)**: throughput 메트릭, 발표용 차트 정제, GUI 데모(`demo.py --scenario --seed`로 시연영상), README·보고서 갱신.

### ④ 시각화·보고팀 (Viz & Report)

- **역할**: 그래프·GUI 데모·발표자료·문서. **결과를 설득력 있게 전달.**
- **1학기 할일**
  - [x] `plot_results.py`(학습곡선)·`demo.py`(SmartSignal/fixed 토글) SmartSignal 규약으로 재작성 완료
  - [x] 발표용 시나리오별 비교 차트 (5 시나리오 × 핵심 메트릭) → `results/evaluation_e2_by_scenario.png` + `docs/presentation_summary.md`(4자 핵심수치표·스토리컷)
  - [x] **실시간 HIL 대시보드** — `demo.py --dashboard`: SUMO GUI 옆 웹 대시보드(12차로 대기차량 + 두뇌 KEEP/SWITCH·Q값 라이브). 엣지 Q값 프로토콜 확장, 실물 Pi 시연 검증. 가이드 `docs/dashboard_guide.md`
  - [ ] `README.md` / 보고서를 새 목표로 갱신(E2+HIL 전면), 발표 슬라이드 outline
- **산출물**: 비교 차트 PNG, GUI 데모, 발표 outline, 갱신된 README

---

## 4. 마일스톤 (1학기 마무리용 스프린트)

| | 내용 | 주관 |
|---|---|---|
| **M0 — 정리·개명** ✅ | v1 → `archive/v1/`, v2 학습산출물 삭제, SmartSignal 개명, 도구 재타겟·스모크테스트 | PM |
| **M1 — 기반** ✅ | 시나리오 5종 동결(`scenarios.py`) / 평가 시나리오 루프(페어드 seed) | ①③ |
| **M2 — 학습·기준선** ✅ | SmartSignal 1000ep 학습 + Webster baseline 추가 + 3자 평가(8ep, 5종 전부 Webster·SmartSignal이 Fixed 능가) | ②③ |
| **M3 — 비교** | 시나리오별 SmartSignal vs baseline 비교 / 차트 / GUI 데모 | ③④ |
| **M4 — 산출** | 발표자료 / README·보고서 갱신 | ④ PM |

## 5. 리스크 & 대응 (PM 관리)

| 리스크 | 영향 | 대응 |
|---|---|---|
| 과포화 학습 발산 (구 v2: reward -1920 고착, `archive/training_history/v2_failed_saturation.csv`) | 학습 실패 | reward 정규화·수요 상한, 시나리오 커리큘럼 |
| 평가 재현성 부족(랜덤 수요) | 비교 신뢰도↓ | 평가는 **고정 seed 동결 시나리오**만 사용 |
| 1학기 시간 촉박 | 범위 초과 | 추상 교차로 단일로 한정, 실제 교차로는 2학기 |
| baseline이 약하면 "당연한 승리" | 설득력↓ | Webster 최적 고정주기를 주 적수로 |

---

## 6. 정리된 것 (재정립 이력)

**1차 (CCTV 줄기 보류)**
- `perception/`, `scripts/`(ITS·YOLO), `realtime_detect.py`, `network_geumjanggyo/`,
  YOLO 가중치, `captures/`·`screenshots/` → **`archive/perception_track/`** (이력 보존)
- `docs/integration_plan.md`, `docs/progress.md`(CCTV 진행기록) → 동일 archive

**2차 (v1 정리 + SmartSignal 개명)**
- v1 일체 → **`archive/v1/`** (`network_v1/`, `dqn_agent.py`, v1 `results/`, 구 archive 잔재)
- v2 학습 산출물(체크포인트) **삭제**, 로그만 `archive/training_history/`에 기록 보존
- 개명: `network_v2/`→`network/`, `dqn_agent_v2.py`→`smart_signal.py`, 모델→`results/smart_signal.pth`
- `evaluate.py`·`demo.py`·`plot_results.py`·`test_env.py` → SmartSignal/network 규약으로 재타겟 (스모크테스트 통과)

**현재 본 줄기 (루트)**: `network/`, `smart_signal.py`, `evaluate.py`, `demo.py`, `plot_results.py`, `test_env.py`, `results/`(빈 상태)
