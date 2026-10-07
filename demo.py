"""SUMO 관제 데모. Fixed/Webster/AI에 같은 신호 전환 규칙을 적용한다.

같은 시나리오와 seed로 제어기를 시연할 수 있다. 공정한 수치 비교와 완료 차량
통계는 출발 일정을 동결하는 evaluate.py를 사용한다. --headless는 GUI 없는 검사다.
"""
import os
import sys
import time
import argparse
import numpy as np
import torch
import torch.nn as nn


def set_sumo_home():
    if "SUMO_HOME" in os.environ:
        return
    for c in ["C:/Program Files (x86)/Eclipse/Sumo", "/usr/share/sumo", "/opt/homebrew/share/sumo"]:
        if c and os.path.isdir(c):
            os.environ["SUMO_HOME"] = c
            return
    print("[ERROR] SUMO_HOME을 찾을 수 없습니다.")
    sys.exit(1)


set_sumo_home()
from signal_environment import SignalEnvironment, MIN_GREEN, MAX_GREEN, DELTA_TIME
from scenarios import EVAL_SCENARIOS
from baselines import webster_timing, green_phase_action

parser = argparse.ArgumentParser()
parser.add_argument("--mode",       choices=["fixed", "webster", "rl"], default="rl",
                    help="fixed: 균등 고정신호 / webster: 최적 고정신호 / rl: SmartSignal")
parser.add_argument("--scenario",   choices=list(EVAL_SCENARIOS), default=None,
                    help="평가 시나리오 5종 중 하나 (생략 시 --route 사용)")
parser.add_argument("--model",      default="results/smart_signal.pth")
parser.add_argument("--net",        default="network/intersection.net.xml")
parser.add_argument("--route",      default="scenarios/eval/medium.rou.xml",
                    help="--scenario 미지정 시 사용할 route 파일")
parser.add_argument("--seed",       type=int, default=1000,
                    help="트래픽 seed (fixed/webster/rl 동일값 → 같은 트래픽으로 대조)")
parser.add_argument("--duration",   type=int, default=3600, help="시뮬레이션 시간(초)")
parser.add_argument("--delay",      type=float, default=0.0, help="스텝 간 딜레이(초, GUI 슬라이더 권장)")
parser.add_argument("--teleport",   type=int, default=300,
                    help="교착차량 순간이동 임계(초). -1=끔(고정신호 영구교착 연출용)")
parser.add_argument("--fixed_hold", type=int, default=6,
                    help="고정신호: 각 green phase 유지 결정스텝 수")
parser.add_argument("--zoom",       type=float, default=600.0,
                    help="GUI 줌 레벨(클수록 확대). 3개 모드 동일값 → 같은 화면으로 대조. 0=자동맞춤")
parser.add_argument("--hil", action="store_true", help="엣지서버(소켓)로 추론 위임")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=9999)
parser.add_argument("--dashboard", action="store_true", help="실시간 대시보드(웹) 동시 기동")
parser.add_argument("--dash_port", type=int, default=8000, help="대시보드 포트")
parser.add_argument("--headless", action="store_true", help="GUI 없이 연결 검사")
args = parser.parse_args()

if args.duration <= 0 or args.duration % DELTA_TIME:
    parser.error("duration은 양수이며 결정 간격의 배수여야 합니다.")
if not MIN_GREEN <= args.fixed_hold * DELTA_TIME <= MAX_GREEN:
    parser.error("fixed_hold × 결정 간격은 최소/최대 녹색 범위여야 합니다.")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ── 시나리오 → route 파일 / 수요 결정 ──
demand = None
if args.scenario:
    route_file = os.path.join("scenarios/eval", f"{args.scenario}.rou.xml")
    if not os.path.isfile(route_file):
        parser.error(f"저장된 시나리오가 없습니다: {route_file}")
    demand = EVAL_SCENARIOS[args.scenario]
else:
    route_file = args.route
    if args.mode == "webster":
        print("[ERROR] webster 모드는 수요가 필요합니다 → --scenario 를 지정하세요.")
        sys.exit(1)


class DQN(nn.Module):
    def __init__(self, state_size, action_size):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_size, 128), nn.ReLU(),
            nn.Linear(128, 128), nn.ReLU(),
            nn.Linear(128, action_size),
        )

    def forward(self, x):
        return self.net(x)


# ── HIL: 엣지서버(라즈베리파이)에 추론을 위임하는 소켓 클라이언트 ──
import socket
import json


class EdgeClient:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.f = self.sock.makefile("rwb")
        self.last_q = None
        print(f"[hil] 엣지서버 연결: {host}:{port}")

    def act(self, obs):
        self.f.write((json.dumps({"obs": list(map(float, obs))}) + "\n").encode())
        self.f.flush()
        resp = json.loads(self.f.readline().decode())
        self.last_q = resp.get("q")
        return int(resp["action"])

    def close(self):
        try:
            self.f.close()
            self.sock.close()
        except Exception:
            pass


# ── RL 모드: 체크포인트에서 차원 자동 인식 (HIL 이면 추론은 엣지가 하므로 로컬 로드 생략) ──
policy_net = None
num_green_phases = 4
if args.mode == "rl" and not args.hil:
    ckpt = torch.load(args.model, map_location=device)
    if isinstance(ckpt, dict) and "policy_net" in ckpt:
        state_size = ckpt.get("state_size", 29)
        action_size = ckpt.get("action_size", 2)
        num_green_phases = ckpt.get("num_green_phases", 4)
        weights = ckpt["policy_net"]
    else:  # 순수 state_dict
        weights = ckpt
        first_w = weights["net.0.weight"]
        last_w = weights[max(k for k in weights if k.endswith(".weight"))]
        state_size, action_size = first_w.shape[1], last_w.shape[0]
    policy_net = DQN(state_size, action_size).to(device)
    policy_net.load_state_dict(weights)
    policy_net.eval()
    print(f"[INFO] 모델 로드: {args.model} "
          f"(state={state_size}, action={action_size}, green_phases={num_green_phases})")

LABELS = {"fixed": "고정신호(균등)", "webster": "Webster(수요기반)", "rl": "SmartSignal(RL)"}
print(f"\n[INFO] 모드: {LABELS[args.mode]}"
      + (f"  시나리오: {args.scenario}" if args.scenario else "")
      + f"  seed: {args.seed}")
if not args.headless:
    print("[INFO] SUMO GUI가 열립니다. Delay 슬라이더로 속도를 조절할 수 있습니다.\n")

# ── HIL: E2 모델 기준이므로 데모도 E2 검지기 관측으로 띄운다 ──
ENV_KWARGS = {}
extra_cmd = "--no-step-log"   # VSCode 터미널의 'Step #...' 스팸 억제
if args.hil:
    from e2_observation import E2ObservationFunction
    ENV_KWARGS = dict(observation_class=E2ObservationFunction)
    extra_cmd = "-a network/e2.add.xml --no-step-log"
elif args.dashboard:
    # 대시보드는 차로별 차량수를 traci.lanearea 로 직접 읽으므로 검지기 로드만 필요.
    # 관측 클래스는 기본 유지 → 로컬 rl 모델(smart_signal.pth) 차원과 일치.
    extra_cmd = "-a network/e2.add.xml --no-step-log"

env = SignalEnvironment(
    net_file=args.net, route_file=route_file, use_gui=not args.headless,
    num_seconds=args.duration, min_green=MIN_GREEN, max_green=MAX_GREEN,
    single_agent=True, sumo_seed=args.seed, time_to_teleport=args.teleport,
    additional_sumo_cmd=extra_cmd,
    **ENV_KWARGS,
)

# Webster: env 결정 주기에 맞춘 phase 스케줄 1회 계산
green_durations = [args.fixed_hold * DELTA_TIME] * num_green_phases
if args.mode == "webster":
    t = webster_timing(
        demand, delta_time=getattr(env, "delta_time", 5),
        min_green=MIN_GREEN, max_green=MAX_GREEN)
    print(f"[INFO] Webster green(초): {t['green']}  cycle: {t['cycle']:.0f}s")
    green_durations = t["green"]

obs, _ = env.reset()
arrived = 0
original_sumo_step = env._sumo_step


def count_arrivals():
    global arrived
    original_sumo_step()
    arrived += env.sumo.simulation.getArrivedNumber()


env._sumo_step = count_arrivals

# ── 카메라 화면 고정 ──
# 3개 모드(fixed/webster/rl)를 같은 줌·중심으로 비춰야 영상 대조가 공정하다.
# --zoom 동일값으로 셋을 찍으면 화면이 완전히 일치한다(0 이면 네트워크 전체 자동맞춤).
if not args.headless:
    try:
        _view = "View #0"
        (xmin, ymin), (xmax, ymax) = env.sumo.simulation.getNetBoundary()
        if args.zoom and args.zoom > 0:
            env.sumo.gui.setOffset(_view, (xmin + xmax) / 2, (ymin + ymax) / 2)
            env.sumo.gui.setZoom(_view, args.zoom)
        else:
            env.sumo.gui.setBoundary(_view, xmin, ymin, xmax, ymax)
    except Exception as e:
        print(f"[WARN] GUI 뷰 고정 실패(무시하고 진행): {e}")

total_reward = 0.0
done = False
step = 0
waits = []
edge = EdgeClient(args.host, args.port) if args.hil else None

# ── 대시보드(웹) 기동 + 차로/현시 매핑 ──
dash = None
LANE_ORDER = LANE_LABELS = GROUP = PHASE_NAMES = PHASE_GREEN = cap_map = None
dqn_action = None
last_qvals = None
if args.dashboard:
    import webbrowser
    from dashboard import Dashboard
    DIRS = [("N", "북"), ("S", "남"), ("E", "동"), ("W", "서")]
    SUB = [("0", "우회전"), ("1", "직진"), ("2", "좌회전")]
    LANE_ORDER = [f"{d}2C_{i}" for d, _ in DIRS for i, _ in SUB]
    LANE_LABELS = {f"{d}2C_{i}": f"{dk} {sk}" for d, dk in DIRS for i, sk in SUB}
    GROUP = {f"{d}2C_{i}": sk for d, _ in DIRS for i, sk in SUB}
    PHASE_NAMES = {0: "남북직진", 1: "남북좌회전", 2: "동서직진", 3: "동서좌회전"}
    PHASE_GREEN = {0: {"S2C_1", "N2C_1"}, 1: {"S2C_2", "N2C_2"},
                   2: {"E2C_1", "W2C_1"}, 3: {"E2C_2", "W2C_2"}}
    VEH_LEN = 7.5
    cap_map = {lane: max(env.sumo.lanearea.getLength("e2_" + lane) / VEH_LEN, 1.0)
               for lane in LANE_ORDER}
    dash = Dashboard()
    url = dash.start(port=args.dash_port)
    print(f"[dashboard] {url}  <- 브라우저에서 열기")
    try:
        if not args.headless:
            webbrowser.open(url)
    except Exception:
        pass

while not done:
    cur_phase = int(np.array(obs[:num_green_phases]).argmax())
    if args.mode in ("fixed", "webster"):
        env_action = green_phase_action(env, green_durations)
    else:
        if edge is not None:
            dqn_action = edge.act(obs)          # 엣지서버(소켓) 추론
            last_qvals = edge.last_q
        else:
            with torch.no_grad():
                q = policy_net(torch.FloatTensor(np.array(obs, dtype=np.float32)).to(device))
                dqn_action = int(q.argmax().item())   # 0=keep, 1=next
                last_qvals = q.detach().cpu().numpy().tolist()
        env_action = cur_phase if dqn_action == 0 else (cur_phase + 1) % num_green_phases

    obs, reward, terminated, truncated, info = env.step(env_action)
    if args.dashboard:
        la = env.sumo.lanearea
        halting = {lane: int(la.getLastStepHaltingNumber("e2_" + lane)) for lane in LANE_ORDER}
        disp_phase = int(np.array(obs[:num_green_phases]).argmax())
        decision = ("keep" if dqn_action == 0 else "switch") if args.mode == "rl" else "fixed"
        mean_wait = info.get("system_mean_waiting_time") if isinstance(info, dict) else None
        active_green_lanes = {lane_id for links, light in zip(
            env.sumo.trafficlight.getControlledLinks(env.ts_ids[0]),
            env.sumo.trafficlight.getRedYellowGreenState(env.ts_ids[0]))
            if light in "gG" for lane_id, _, _ in links}
        dash.update({
            "step": step,
            "phase": disp_phase, "phase_name": PHASE_NAMES[disp_phase],
            "time_in_phase": env.traffic_signals[env.ts_ids[0]].green_elapsed,
            "decision": decision,
            "q": last_qvals if args.mode == "rl" else None,
            "metrics": {
                "waiting_vehicles": sum(halting.values()),
                "avg_wait": round(float(mean_wait), 1) if mean_wait is not None else 0.0,
                "throughput": int(arrived),
            },
            "lanes": [{
                "label": LANE_LABELS[lane], "group": GROUP[lane],
                "count": halting[lane], "cap": int(cap_map[lane]),
                "is_green": lane in active_green_lanes,
            } for lane in LANE_ORDER],
            "mode": args.mode,
            "edge": f"{args.host}:{args.port}" if args.hil else "local",
        })
    done = terminated or truncated
    total_reward += reward
    step += 1
    if isinstance(info, dict) and info.get('system_total_waiting_time') is not None:
        waits.append(info['system_total_waiting_time'])
    if args.delay > 0:
        time.sleep(args.delay)
    if step % 100 == 0:
        print(f"  Step {step:4d} | phase {cur_phase} | Reward {reward:7.3f} | Total {total_reward:8.1f}")

env.close()
if edge:
    edge.close()
if dash:
    dash.stop()
avg_wait = float(np.mean(waits)) if waits else 0.0
print(f"\n{'='*48}")
print(f"  모드        : {LABELS[args.mode]}")
print(f"  시나리오    : {args.scenario or route_file}  (seed {args.seed})")
print(f"  총 스텝     : {step}")
print(f"  시스템 총 대기시간의 시점 평균: {avg_wait:.1f}s (결정 간격별 표본, 차량별 평균 아님)")
print(f"  총 보상     : {total_reward:.2f}")
print(f"{'='*48}")
