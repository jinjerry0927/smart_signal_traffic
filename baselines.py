"""
알려진 시나리오 수요로 계산하는 Webster 근사 고정주기 기준선.

방향별 수요(veh/h)로 주기와 녹색 배분을 계산하고 실험용 시간 제약을 적용한다.
전체 시나리오 수요를 아는 기준선이다. 실시간 추정 방식이나 최적 성능 보장은 아니다.

network/ 의 green phase 4종(sumo_rl 인덱스 순서)과 매핑:
    idx0 = N·S 직진 (gGrgrrgGrgrr)
    idx1 = N·S 보호좌회전 (grGgrrgrGgrr)
    idx2 = E·W 직진 (grrgGrgrrgGr)
    idx3 = E·W 보호좌회전 (grrgrGgrrgrG)

진입 방향당 3차로 = 우회전/직진/좌회전 각 1차로. 임계 교통량비 yᵢ 는 각 phase에서
가장 큰 movement(직진 0.60 또는 좌회전 0.15)로 결정한다. 우회전(0.25)은 직진 phase 중
별도 차로로 흐르므로 0.25 < 0.60 → 임계가 아니어서 무시.

공식:
    yᵢ  = qᵢ / s                       (임계 교통량비)
    Y   = Σ yᵢ
    C0  = (1.5·L + 5) / (1 − Y)        (Webster 최적 사이클)
    gᵢ  = (C − L) · yᵢ / Y             (방향별 녹색 split)
과포화(Y ≥ 0.95)는 C0 발산 → 사이클을 C_cap(180s) 으로 고정.

공정 비교를 위해 SmartSignal·Fixed 와 동일한 안전 봉투(min_green/max_green)로 green 을
clamp 한다 → "세 제어기가 녹색 분배 방식에서만 차이" 나는 비교.

직접 실행하면 평가 시나리오 5종의 Webster 타이밍 표를 출력한다:
    python baselines.py
"""
import math
from scenarios import EVAL_SCENARIOS

SAT_FLOW = 1900     # 포화교통류율 (veh/h/차로, HCM 표준)
LOST_TIME = 20      # 실험용 전환 손실 근사: 4 × (황색 3s + 전체 적색 2s)
MAX_CYCLE = 180     # 과포화 시 사이클 상한

# green phase 인덱스(sumo_rl 순서) → (임계 회전종류, 해당 진입방향 쌍)
WEBSTER_PHASES = [
    ('through', ['N', 'S']),   # idx0  N·S 직진
    ('left',    ['N', 'S']),   # idx1  N·S 좌회전
    ('through', ['E', 'W']),   # idx2  E·W 직진
    ('left',    ['E', 'W']),   # idx3  E·W 좌회전
]
RATIO = {'through': 0.60, 'left': 0.15}   # scenarios.py TURN_RATIO 와 일치


def webster_timing(demand, sat_flow=SAT_FLOW, lost_time=LOST_TIME,
                   max_cycle=MAX_CYCLE, min_green=15, max_green=60,
                   target_x=0.85, delta_time=None):
    """수요 dict(방향별 veh/h) → Webster 타이밍.

    Webster 최적 split 에 두 가지 실무 제약을 추가한다(둘 다 표준 신호공학 관행):
    1. 최소/최대 녹색 [min_green, max_green] — env 안전 봉투와 일치.
    2. 실무 포화도 상한 target_x — 임계 movement 의 포화도(degree of saturation)가
       target_x 를 넘지 않도록 녹색을 늘린다(gᵢ ≥ yᵢ·C/target_x). 이게 없으면
       min_green 바닥이 가벼운 좌회전 phase를 과배정해 직진이 굶고(포화도 ~0.9),
       중간 수요(medium)에서 큐가 발산한다(실측 확인).
    delta_time 을 주면 결정스텝 격자에 맞춰 녹색을 '올림(ceil)' 양자화한다 — 양자화로
    capacity 가 깎이지 않도록(절벽 아래로 떨어지지 않도록).

    반환: {'cycle', 'green'[4], 'green_cont'[4](양자화 전), 'y'[4], 'Y', 'C0', 'saturated'}
    """
    y = [max(demand[a] * RATIO[mv] for a in apps) / sat_flow
         for mv, apps in WEBSTER_PHASES]
    Y = sum(y)
    saturated = Y >= 0.95
    if Y == 0:
        return dict(cycle=4 * min_green + lost_time, green=[min_green] * 4,
                    green_cont=[min_green] * 4, y=y, Y=Y, C0=0.0, saturated=False)

    if saturated:
        # 과포화: 포화도가 본질적으로 ≥1 → target_x 상한은 무의미. 사이클을 max_cycle 로
        # 고정하고 비례 split + 최소/최대 녹색 clamp 만 적용(원래 방식).
        C0 = float('inf')
        C = max_cycle
        green = [min(max((C - lost_time) * yi / Y, min_green), max_green) for yi in y]
    else:
        # 미포화: 기본 비례 split → 포화도 상한·최소·최대 녹색을 만족하도록 사이클을
        # 키우며 수렴(고정점 반복). max_cycle 에 묶임.
        C0 = (1.5 * lost_time + 5) / (1 - Y)
        C = min(C0, max_cycle)
        green = [(C - lost_time) * yi / Y for yi in y]
        for _ in range(50):
            green = [min(max(g, yi * C / target_x, min_green), max_green)
                     for yi, g in zip(y, green)]
            newC = min(sum(green) + lost_time, max_cycle)
            if abs(newC - C) < 0.05:
                C = newC
                break
            C = newC
    green_cont = list(green)

    # 결정스텝 격자로 올림 양자화 (delta_time 주어진 경우)
    if delta_time:
        green = [min(math.ceil(g / delta_time) * delta_time, max_green)
                 for g in green]

    return {
        'cycle': sum(green) + lost_time,
        'green': green,
        'green_cont': green_cont,
        'y': y,
        'Y': Y,
        'C0': C0,
        'saturated': saturated,
    }


def webster_schedule(demand, delta_time=5, **kw):
    """Webster green(초) → 결정스텝 단위 phase 스케줄.

    과거 호출 호환용 목록이다. 실행 중 제어는 green_phase_action을 사용해
    실제 녹색 경과 시간으로 전환한다(황색·전체 적색은 녹색 시간에 포함하지 않음).

    반환: (sched, timing)
        sched  : 예) [0,0,0, 1,1,1, 2,2,2,2, 3,3,3]
        timing : webster_timing() 결과 dict
    """
    t = webster_timing(demand, delta_time=delta_time, **kw)
    sched = []
    for i, g in enumerate(t['green']):
        sched += [i] * max(1, round(g / delta_time))
    return sched, t


def green_phase_action(env, durations):
    """Fixed/Webster의 녹색 목표 시간만 비교하고 보호 전환은 공통 환경에 맡긴다."""
    ts = env.traffic_signals[env.ts_ids[0]]
    current = ts.green_phase
    if ts.green_elapsed >= durations[current]:
        return (current + 1) % ts.num_green_phases
    return current


if __name__ == '__main__':
    PHASE_LABELS = ['NS직진', 'NS좌회전', 'EW직진', 'EW좌회전']
    print(f"Webster baseline - 평가 시나리오 5종 (s={SAT_FLOW}, L={LOST_TIME}s, "
          f"cap={MAX_CYCLE}s, green in [15,60])\n")
    header = (f"{'시나리오':12s} {'Y':>6s} {'C0':>8s} {'적용C':>7s}  "
              + " ".join(f"{lab:>9s}" for lab in PHASE_LABELS))
    print(header)
    print("-" * len(header))
    for name, demand in EVAL_SCENARIOS.items():
        t = webster_timing(demand, delta_time=5)   # 실제 실행되는(양자화된) 녹색
        c0 = '발산' if t['saturated'] else f"{t['C0']:.0f}s"
        greens = " ".join(f"{g:8.1f}s" for g in t['green'])
        flag = ' (과포화)' if t['saturated'] else ''
        print(f"{name:12s} {t['Y']:6.3f} {c0:>8s} {t['cycle']:6.0f}s  {greens}{flag}")
    print("\n결정스텝 스케줄 예시 (delta_time=5):")
    for name, demand in EVAL_SCENARIOS.items():
        sched, _ = webster_schedule(demand)
        print(f"  {name:12s} (len={len(sched):2d}): {sched}")
