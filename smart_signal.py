"""
SmartSignal — DQN 기반 적응형 교차로 신호 제어 (프로젝트 최종 모델).

대상 환경: 4지 × 3차선 교차로 (network/), 4-phase 보호좌회전.

현실적 적응형 신호 제어 (Adaptive Traffic Signal Control):
- Phase는 정해진 사이클 순서로만 진행 (NS직진 → NS좌 → EW직진 → EW좌 → ...)
- RL은 각 phase의 지속 시간만 조절 (Keep vs Next)
- 안전 제약: min_green 15s, max_green 60s, all-red 자동 적용

Action space:
- 0 = Keep current phase (현재 phase 연장)
- 1 = Switch to next phase (사이클 순서대로 다음 phase로 전환)

State (29차원, sumo-rl 기본):
- [0:4]  현재 green phase one-hot
- [4]    min_green 충족 여부 (1 if completed)
- [5:17] 각 lane의 density (12 lanes)
- [17:29] 각 lane의 queue length (12 lanes)
"""

import os
import sys
import csv
import random
import argparse
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np
from collections import deque

import torch
import torch.nn as nn
import torch.optim as optim


def set_sumo_home(path=None):
    if "SUMO_HOME" in os.environ:
        return os.environ["SUMO_HOME"]
    candidates = [
        path,
        "C:/Program Files (x86)/Eclipse/Sumo",
        "/usr/share/sumo",
        "/opt/homebrew/share/sumo",
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            os.environ["SUMO_HOME"] = c
            print(f"[INFO] SUMO_HOME 설정: {c}")
            return c
    print("[ERROR] SUMO_HOME을 찾을 수 없습니다.")
    sys.exit(1)


parser = argparse.ArgumentParser()
parser.add_argument("--sumo_home",  default=None)
parser.add_argument("--net",        default="network/intersection.net.xml")
parser.add_argument("--route",      default=None, help="생성 수요 경로. 기본은 이번 실행 폴더 내부")
parser.add_argument("--episodes",   type=int, default=1000)
parser.add_argument("--resume",     default=None)
parser.add_argument("--out_dir",    default=None, help="새 학습 결과 폴더")
parser.add_argument("--seconds", type=int, default=3600, help="에피소드 시뮬레이션 시간")
parser.add_argument("--traffic_min", type=int, default=300,
                    help="각 방향 최소 vehsPerHour (한산)")
parser.add_argument("--traffic_max", type=int, default=1200,
                    help="각 방향 최대 vehsPerHour (피크)")
parser.add_argument("--obs", choices=["global", "e2"], default="global",
                    help="global: sumo_rl 기본(전지적) / e2: E2 검지기 기반")
args = parser.parse_args()

set_sumo_home(args.sumo_home)
from signal_environment import SignalEnvironment, MIN_GREEN, MAX_GREEN, CONTROL_VERSION

resume_checkpoint = None
if args.resume:
    resume_checkpoint = torch.load(args.resume, map_location="cpu")
    required = {"policy_net", "target_net", "optimizer", "episode", "epsilon", "control_version",
                "state_size", "action_size", "num_green_phases"}
    if not isinstance(resume_checkpoint, dict) or not required <= resume_checkpoint.keys():
        parser.error("학습 재개에는 optimizer·episode를 포함한 체크포인트가 필요합니다.")
    if resume_checkpoint["control_version"] != CONTROL_VERSION:
        parser.error("다른 제어 규칙의 체크포인트는 학습 재개에 사용할 수 없습니다.")
    if (resume_checkpoint["state_size"] != (53 if args.obs == "e2" else 29)
            or resume_checkpoint["action_size"] != 2 or resume_checkpoint["num_green_phases"] != 4):
        parser.error("체크포인트의 관측 모드·행동·신호 규약이 이번 실행과 다릅니다.")
    if args.episodes <= resume_checkpoint["episode"]:
        parser.error("episodes는 체크포인트 이후의 마지막 에피소드 번호여야 합니다.")
if args.episodes <= 0 or args.seconds <= 0 or args.seconds % 5:
    parser.error("episodes는 양수, seconds는 양수이며 5의 배수여야 합니다.")
args.out_dir = str(Path(args.out_dir or f"results/runs/train-{datetime.now():%Y%m%d-%H%M%S-%f}").resolve())
Path(args.out_dir).mkdir(parents=True, exist_ok=False)
args.route = str(Path(args.route or str(Path(args.out_dir) / "training.rou.xml")).resolve())
if not Path(args.route).is_relative_to(Path(args.out_dir)):
    parser.error("생성 수요 --route는 이번 실행의 --out_dir 내부여야 합니다.")
if Path(args.route).exists():
    parser.error("기존 수요 파일을 덮어쓸 수 없습니다. 새 --route 경로를 지정하세요.")
args.net = str(Path(args.net).resolve())
if args.resume:
    args.resume = str(Path(args.resume).resolve())

from scenarios import random_demand, write_routes   # 트래픽 생성은 scenarios.py 공용 정의 사용

# ── 관측 소스 선택 (global=sumo_rl 기본 / e2=E2 검지기 기반) ──
ENV_KWARGS = dict(additional_sumo_cmd="--no-step-log")
MODEL_NAME = "smart_signal.pth"
if args.obs == "e2":
    from e2_observation import E2ObservationFunction
    detectors = ET.parse(Path(__file__).resolve().parent / "network/e2.add.xml")
    for detector in detectors.getroot():
        detector.set("file", "detectors.xml")
    detectors.write(Path(args.out_dir) / "detectors.add.xml", encoding="utf-8")
    ENV_KWARGS = dict(observation_class=E2ObservationFunction,
                      additional_sumo_cmd="-a detectors.add.xml --no-step-log")
    MODEL_NAME = "smart_signal_e2.pth"
print(f"[INFO] 관측 모드: {args.obs} → 모델 파일 {MODEL_NAME}")
os.chdir(args.out_dir)  # SUMO의 상대 출력도 이 실행에 격리한다.


# ── 안전 제약 (현실 도로 기준) ─────────────────────
# 시간 규칙은 signal_environment.py에서 공통 관리한다.

# ── DQN 하이퍼파라미터 ──
LR            = 1e-3
GAMMA         = 0.99
EPSILON_START = 1.0
EPSILON_MIN   = 0.05
EPSILON_DECAY = 0.995
BATCH_SIZE    = 64
TARGET_UPDATE = 10
BUFFER_SIZE   = 50000
CHECKPOINT_EVERY = 100

# ── Action: Keep / Next 2-action (현실적 신호 제어) ──
ACTION_KEEP = 0
ACTION_NEXT = 1
ACTION_SIZE = 2

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"[INFO] 사용 디바이스: {device}")
if device.type == "cuda":
    print(f"[INFO] GPU: {torch.cuda.get_device_name(0)}")


# ── Random 트래픽 생성 (매 episode 다른 패턴, scenarios.py 정의 사용) ──
def generate_random_routes(out_path, t_min, t_max):
    """각 방향 수요를 [t_min, t_max]에서 랜덤 샘플링하여 .rou.xml 작성, 수요 dict 반환."""
    return write_routes(out_path, random_demand(t_min, t_max))


# ── 환경 검증 (probe — random route 한 번 생성해서 차원 확인) ──
print("[INFO] 환경 검증 중...")
generate_random_routes(args.route, args.traffic_min, args.traffic_max)
probe_env = SignalEnvironment(
    net_file=args.net,
    route_file=args.route,
    use_gui=False,
    num_seconds=args.seconds,
    min_green=MIN_GREEN,
    max_green=MAX_GREEN,
    single_agent=True,
    time_to_teleport=300,
    **ENV_KWARGS,
)
probe_obs, _ = probe_env.reset()
STATE_SIZE = int(np.array(probe_obs).shape[0])
NUM_GREEN_PHASES = int(probe_env.action_space.n)
probe_env.close()
print(f"[INFO] STATE_SIZE = {STATE_SIZE}, GREEN_PHASES = {NUM_GREEN_PHASES}, "
      f"DQN_ACTION = {ACTION_SIZE} (Keep/Next)")
print(f"[INFO] Phase 사이클: 0 → 1 → ... → {NUM_GREEN_PHASES-1} → 0 (순서 강제)")
print(f"[INFO] 안전: min_green={MIN_GREEN}s, max_green={MAX_GREEN}s")
print(f"[INFO] 트래픽 (랜덤): 각 방향 {args.traffic_min}~{args.traffic_max} veh/h "
      f"(평균 {(args.traffic_min + args.traffic_max) // 2})")


class DQN(nn.Module):
    def __init__(self, state_size, action_size):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_size, 128),
            nn.ReLU(),
            nn.Linear(128, 128),
            nn.ReLU(),
            nn.Linear(128, action_size),
        )

    def forward(self, x):
        return self.net(x)


class ReplayBuffer:
    def __init__(self, capacity):
        self.buffer = deque(maxlen=capacity)

    def push(self, state, action, reward, next_state, done):
        self.buffer.append((state, action, reward, next_state, float(done)))

    def sample(self, batch_size):
        batch = random.sample(self.buffer, batch_size)
        s, a, r, ns, d = zip(*batch)
        return (np.array(s, dtype=np.float32),
                np.array(a, dtype=np.int64),
                np.array(r, dtype=np.float32),
                np.array(ns, dtype=np.float32),
                np.array(d, dtype=np.float32))

    def __len__(self):
        return len(self.buffer)


def current_phase_from_obs(obs):
    """observation의 앞 NUM_GREEN_PHASES 자리는 phase one-hot."""
    return int(np.array(obs[:NUM_GREEN_PHASES]).argmax())


def dqn_action_to_env_action(dqn_action, current_phase):
    """DQN의 keep/next 결정을 환경의 phase index로 변환 (cyclic)."""
    if dqn_action == ACTION_KEEP:
        return current_phase
    else:  # ACTION_NEXT
        return (current_phase + 1) % NUM_GREEN_PHASES


policy_net = DQN(STATE_SIZE, ACTION_SIZE).to(device)
target_net = DQN(STATE_SIZE, ACTION_SIZE).to(device)
target_net.load_state_dict(policy_net.state_dict())
target_net.eval()
optimizer = optim.Adam(policy_net.parameters(), lr=LR)
buffer    = ReplayBuffer(BUFFER_SIZE)

start_episode = 1
epsilon       = EPSILON_START

if args.resume:
    ckpt = resume_checkpoint
    policy_net.load_state_dict(ckpt["policy_net"])
    target_net.load_state_dict(ckpt["target_net"])
    optimizer.load_state_dict(ckpt["optimizer"])
    start_episode = ckpt["episode"] + 1
    epsilon       = ckpt["epsilon"]
    print(f"[INFO] 체크포인트 재개: episode {start_episode}, epsilon {epsilon:.3f}")


log_path = os.path.join(args.out_dir, "training_log.csv")
csv_file = open(log_path, "a", newline="")
csv_writer = csv.writer(csv_file)
if csv_file.tell() == 0:
    csv_writer.writerow(["episode", "total_reward", "epsilon", "buffer_size",
                          "keep_ratio", "next_ratio",
                          "traffic_N", "traffic_S", "traffic_E", "traffic_W"])


def train_step():
    if len(buffer) < BATCH_SIZE:
        return
    s, a, r, ns, d = buffer.sample(BATCH_SIZE)

    s  = torch.from_numpy(s).to(device)
    a  = torch.from_numpy(a).to(device)
    r  = torch.from_numpy(r).to(device)
    ns = torch.from_numpy(ns).to(device)
    d  = torch.from_numpy(d).to(device)

    q_values = policy_net(s).gather(1, a.unsqueeze(1)).squeeze(1)
    with torch.no_grad():
        next_actions = policy_net(ns).argmax(1)
        target_q     = r + GAMMA * target_net(ns).gather(1, next_actions.unsqueeze(1)).squeeze(1) * (1 - d)

    loss = nn.SmoothL1Loss()(q_values, target_q)
    optimizer.zero_grad()
    loss.backward()
    torch.nn.utils.clip_grad_norm_(policy_net.parameters(), 10)
    optimizer.step()


print(f"\n학습 시작: {args.episodes} 에피소드 (episode {start_episode}부터)\n")

for episode in range(start_episode, args.episodes + 1):
    # 매 episode 새로운 random 트래픽 패턴 생성
    traffic_summary = generate_random_routes(args.route, args.traffic_min, args.traffic_max)

    env = SignalEnvironment(
        net_file=args.net,
        route_file=args.route,
        use_gui=False,
        num_seconds=args.seconds,
        min_green=MIN_GREEN,
        max_green=MAX_GREEN,
        single_agent=True,
        time_to_teleport=300,
        **ENV_KWARGS,
    )
    obs, _ = env.reset()
    total_reward = 0.0
    done = False
    action_counts = [0, 0]  # [keep, next]

    while not done:
        current_phase = current_phase_from_obs(obs)

        # DQN 결정: keep vs next
        if random.random() < epsilon:
            dqn_action = random.randint(0, ACTION_SIZE - 1)
        else:
            with torch.no_grad():
                q = policy_net(torch.from_numpy(np.array(obs, dtype=np.float32)).to(device))
                dqn_action = int(q.argmax().item())

        # 환경에 보낼 phase 인덱스 (사이클 순서 강제)
        env_action = dqn_action_to_env_action(dqn_action, current_phase)

        next_obs, reward, terminated, truncated, _ = env.step(env_action)
        done = terminated or truncated

        # 학습 시에는 dqn_action(0/1)을 저장
        buffer.push(obs, dqn_action, reward, next_obs, done)
        train_step()

        obs = next_obs
        total_reward += reward
        action_counts[dqn_action] += 1

    if episode % TARGET_UPDATE == 0:
        target_net.load_state_dict(policy_net.state_dict())

    epsilon = max(EPSILON_MIN, epsilon * EPSILON_DECAY)
    env.close()

    total_actions = sum(action_counts) or 1
    keep_ratio = action_counts[0] / total_actions
    next_ratio = action_counts[1] / total_actions

    tN, tS, tE, tW = traffic_summary['N'], traffic_summary['S'], traffic_summary['E'], traffic_summary['W']
    print(f"Episode {episode:4d}/{args.episodes} | "
          f"Reward: {total_reward:8.1f} | "
          f"Epsilon: {epsilon:.4f} | "
          f"Keep/Next: {keep_ratio:.2f}/{next_ratio:.2f} | "
          f"Traffic N{tN:>4d} S{tS:>4d} E{tE:>4d} W{tW:>4d}")
    csv_writer.writerow([episode, round(total_reward, 2), round(epsilon, 4),
                          len(buffer), round(keep_ratio, 3), round(next_ratio, 3),
                          tN, tS, tE, tW])
    csv_file.flush()

    if episode % CHECKPOINT_EVERY == 0 or episode == args.episodes:
        ckpt_path = os.path.join(args.out_dir, f"checkpoint_{args.obs}_ep{episode}.pth")
        torch.save({
            "episode":         episode,
            "epsilon":         epsilon,
            "policy_net":      policy_net.state_dict(),
            "target_net":      target_net.state_dict(),
            "optimizer":       optimizer.state_dict(),
            "state_size":      STATE_SIZE,
            "action_size":     ACTION_SIZE,
            "num_green_phases": NUM_GREEN_PHASES,
            "control_version": CONTROL_VERSION,
        }, ckpt_path)
        print(f"  >> 체크포인트 저장: {ckpt_path}")


csv_file.close()
final_path = os.path.join(args.out_dir, MODEL_NAME)
torch.save({
    "policy_net":       policy_net.state_dict(),
    "state_size":       STATE_SIZE,
    "action_size":      ACTION_SIZE,
    "num_green_phases": NUM_GREEN_PHASES,
    "control_version": CONTROL_VERSION,
}, final_path)
print(f"\n학습 완료. 최종 모델: {final_path}")
print(f"학습 로그: {log_path}")
