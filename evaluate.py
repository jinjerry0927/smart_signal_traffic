"""동일한 출발 일정·seed·공통 신호 규칙으로 Fixed/Webster/기존 AI를 비교한다.

새 실행 폴더에 입력 해시, 매초 집계, 완료 차량 tripinfo와 CSV를 기록한다.
mean_system_total_waiting_time_s는 각 시점의 시스템 전체 현재 대기시간 합의 평균이다.
mean_completed_waiting_time_s는 도착 완료 차량의 여정 전체 대기시간 평균이다.
완료 차량 평균은 미도착/미진입 수와 함께 해석한다. 기존 모델의 성능 보장은 없다.
"""
import os
import sys
import csv
import time
import argparse
from contextlib import contextmanager
from datetime import datetime
import json
import importlib.metadata
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import numpy as np
import torch
import torch.nn as nn
import matplotlib.pyplot as plt

from scenarios import EVAL_SCENARIOS
from baselines import webster_timing, green_phase_action
from evaluation_support import EpisodeMetrics, freeze_departures, file_hash


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
from signal_environment import (SignalEnvironment, MIN_GREEN, MAX_GREEN, DELTA_TIME,
                                YELLOW_TIME, ALL_RED_TIME, CONTROL_VERSION)

parser = argparse.ArgumentParser()
parser.add_argument("--model",         default="results/smart_signal.pth")
parser.add_argument("--net",           default="network/intersection.net.xml")
parser.add_argument("--scenarios_dir", default="scenarios/eval")
parser.add_argument("--episodes",      type=int, default=10, help="시나리오당 반복 수")
parser.add_argument("--seconds",       type=int, default=3600)
parser.add_argument("--seed_base",     type=int, default=1000)
parser.add_argument("--fixed_hold",    type=int, default=6, help="고정신호: green phase 유지 결정스텝")
parser.add_argument("--model_e2", default="results/smart_signal_e2.pth")
parser.add_argument("--scenarios", nargs="+", choices=list(EVAL_SCENARIOS), default=list(EVAL_SCENARIOS))
parser.add_argument("--modes", nargs="+", choices=["fixed", "webster", "rl_global", "rl_e2"],
                    default=["fixed", "webster", "rl_global", "rl_e2"])
parser.add_argument("--out-dir", default=None, help="존재하지 않는 새 결과 폴더")
parser.add_argument("--skip-plots", action="store_true")
args = parser.parse_args()

if args.episodes <= 0 or args.seconds <= 0 or args.seconds % DELTA_TIME:
    parser.error("episodes는 양수, seconds는 양수이며 결정 간격의 배수여야 합니다.")
if not MIN_GREEN <= args.fixed_hold * DELTA_TIME <= MAX_GREEN:
    parser.error("fixed_hold × 결정 간격은 최소/최대 녹색 범위여야 합니다.")
if "fixed" not in args.modes:
    parser.error("비교표의 기준선 fixed를 --modes에 포함하세요.")
args.net = str(Path(args.net).resolve())
args.scenarios_dir = str(Path(args.scenarios_dir).resolve())
args.model, args.model_e2 = str(Path(args.model).resolve()), str(Path(args.model_e2).resolve())
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")


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


# ── 모델 로드 (global / e2 각각, 있으면 모드 추가) ──
def _load_dqn(path):
    if not os.path.isfile(path):
        return None, None
    ckpt = torch.load(path, map_location=device)
    weights = ckpt["policy_net"] if isinstance(ckpt, dict) and "policy_net" in ckpt else ckpt
    ss = ckpt.get("state_size", weights["net.0.weight"].shape[1]) if isinstance(ckpt, dict) else weights["net.0.weight"].shape[1]
    a_s = ckpt.get("action_size", weights[max(k for k in weights if k.endswith(".weight"))].shape[0]) if isinstance(ckpt, dict) else weights[max(k for k in weights if k.endswith(".weight"))].shape[0]
    ng = ckpt.get("num_green_phases", 4) if isinstance(ckpt, dict) else 4
    net = DQN(ss, a_s).to(device); net.load_state_dict(weights); net.eval()
    return net, ng

NETS = {}
for mode, path, size in [("rl_global", args.model, 29), ("rl_e2", args.model_e2, 53)]:
    if mode not in args.modes:
        continue
    net, ng = _load_dqn(path)
    if net is None:
        parser.error(f"선택한 모드의 모델이 없습니다: {path}")
    if ng != 4 or net.net[0].in_features != size or net.net[-1].out_features != 2:
        parser.error(f"모델 규약 불일치: {mode}는 {size}D/2 actions/4 green이 필요합니다.")
    NETS[mode] = net
NUM_GREEN_PHASES = 4
MODES = list(dict.fromkeys(args.modes))
print(f"[INFO] 평가 모드: {MODES}")

LABELS = {"fixed": "Fixed", "webster": "Webster",
          "rl_global": "SmartSignal(god-view)", "rl_e2": "SmartSignal(E2)"}

# ── 입력 보존: 원본을 재생성하지 않고 이번 실행의 명시적 출발 일정으로 동결 ──
for name in args.scenarios:
    if not Path(args.scenarios_dir, f"{name}.rou.xml").is_file():
        parser.error(f"평가 수요가 없습니다: {name}. 원본은 자동 생성/덮어쓰기하지 않습니다.")
out_dir = Path(args.out_dir or f"results/runs/eval-{datetime.now():%Y%m%d-%H%M%S-%f}").resolve()
out_dir.mkdir(parents=True, exist_ok=False)
inputs = out_dir / "inputs"
inputs.mkdir()
shutil.copy2(args.net, inputs / "intersection.net.xml")
args.net = str(inputs / "intersection.net.xml")
scenario_paths, scheduled_counts, scenario_hashes = {}, {}, {}
for name in dict.fromkeys(args.scenarios):
    source = Path(args.scenarios_dir, f"{name}.rou.xml")
    snapshot = inputs / f"{name}.rou.xml"
    scheduled_counts[name] = freeze_departures(source, snapshot, args.seconds)
    scenario_paths[name] = str(snapshot)
    scenario_hashes[name] = dict(source_sha256=file_hash(source), frozen_sha256=file_hash(snapshot))
manifest = dict(control_version=CONTROL_VERSION, seconds=args.seconds, episodes=args.episodes,
    seed_base=args.seed_base, modes=MODES, scenarios=scenario_hashes, net_sha256=file_hash(args.net),
    control=dict(min_green=MIN_GREEN, max_green=MAX_GREEN, yellow=YELLOW_TIME,
                 all_red=ALL_RED_TIME, delta_time=DELTA_TIME, teleport=300),
    models={mode: file_hash(args.model if mode == "rl_global" else args.model_e2) for mode in NETS},
    metric_version="per-second-v1", python=sys.version,
    sumo_rl=importlib.metadata.version("sumo-rl"),
    sources={filename: file_hash(Path(__file__).resolve().parent / filename)
             for filename in ("evaluate.py", "evaluation_support.py", "baselines.py", "signal_environment.py")},
    note="기존 모델의 변경된 환경 연결 검사. 성능 향상 주장이나 본 평가가 아님. Webster는 시나리오 수요를 알고 계산함.")
(out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def run_episode(mode, route_file, seed, demand=None, *, name, episode):
    """한 에피소드 실행 → 메트릭 dict.

    mode == 'webster' 인 경우 demand(방향별 veh/h)로 green split 스케줄을 계산해 순환한다.
    mode == 'rl_e2' 인 경우 E2 검지기 관측(53D)을 사용한다.
    """
    episode_dir = out_dir / f"{name}-{mode}-{episode}"
    episode_dir.mkdir()
    extra = "--no-step-log --tripinfo-output tripinfo.xml"
    obs_class = None
    if mode == "rl_e2":
        from e2_observation import E2ObservationFunction
        obs_class = E2ObservationFunction
        detectors = ET.parse(Path(__file__).resolve().parent / "network/e2.add.xml")
        for detector in detectors.getroot():
            detector.set("file", "detectors.xml")
        detectors.write(episode_dir / "detectors.add.xml", encoding="utf-8")
        extra += " -a detectors.add.xml"
    # SUMO의 상대 출력은 에피소드 폴더에 한정한다. 공백 있는 상위 경로도 지원한다.
    with working_directory(episode_dir):
        env = SignalEnvironment(
            net_file=args.net, route_file=route_file, use_gui=False,
            num_seconds=args.seconds, min_green=MIN_GREEN, max_green=MAX_GREEN,
            single_agent=True, sumo_warnings=False, sumo_seed=seed,
            # 비교 기법 모두 같은 teleport 기준을 사용하고 발생 횟수도 기록한다.
            time_to_teleport=300,
            additional_sumo_cmd=extra,   # 콘솔의 'Step #...' 스팸 억제 (+E2면 검지기 add)
            **({"observation_class": obs_class} if obs_class else {}),
        )
        obs, _ = env.reset()
    # arrived_vehicles(도착차량 누적): env.step 은 내부에서 simulationStep 을 delta_time(황색 포함)
    # 만큼 여러 번 돌리므로, env.step 뒤 getArrivedNumber()를 1회만 읽으면 대부분의 도착을
    # 놓친다. _sumo_step 을 감싸 매 시뮬레이션 스텝의 도착수를 누적한다(reset 이후 부착).
    metrics = EpisodeMetrics()
    _orig_sumo_step = env._sumo_step

    def _counting_sumo_step():
        _orig_sumo_step()
        metrics.sample(env.sumo)

    env._sumo_step = _counting_sumo_step
    # Webster: env 의 결정 주기(delta_time)에 맞춘 phase 스케줄 1회 계산
    green_durations = [args.fixed_hold * DELTA_TIME] * NUM_GREEN_PHASES
    if mode == "webster":
        timing = webster_timing(
            demand, delta_time=getattr(env, "delta_time", 5),
            min_green=MIN_GREEN, max_green=MAX_GREEN,
            lost_time=NUM_GREEN_PHASES * (YELLOW_TIME + ALL_RED_TIME))
        green_durations = timing["green"]
    total_reward, done, step = 0.0, False, 0
    try:
        while not done:
            cur_phase = int(np.array(obs[:NUM_GREEN_PHASES]).argmax())
            if mode in ("fixed", "webster"):
                action = green_phase_action(env, green_durations)
            else:  # rl_global / rl_e2
                net = NETS[mode]
                with torch.no_grad():
                    q = net(torch.FloatTensor(np.array(obs, dtype=np.float32)).to(device))
                    dqn_action = int(q.argmax().item())
                action = cur_phase if dqn_action == 0 else (cur_phase + 1) % NUM_GREEN_PHASES
            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            total_reward += reward
            step += 1
        running = len(env.sumo.vehicle.getIDList())
        pending = len(env.sumo.simulation.getPendingVehicles())
    finally:
        env.close()
    return {
        'total_reward': total_reward,
        **metrics.finish(episode_dir / "tripinfo.xml", running, pending, scheduled_counts[name]),
        'route_sha256': scenario_hashes[name]['frozen_sha256'],
        'control_version': CONTROL_VERSION,
    }


# ── CSV 저장 헬퍼 (시나리오마다 호출 → 중간 끊겨도 완료분 보존) ──
OUT_CSV = str(out_dir / "evaluation.csv")


def flush_csv(recs):
    if not recs:
        return
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=recs[0].keys())
        w.writeheader()
        w.writerows(recs)


# ── 평가 루프 (시나리오 × 모드 × 에피소드, 페어드 seed) ──
records = []   # 개별 에피소드 결과
n_scen = len(scenario_paths)
total_eps = n_scen * args.episodes * len(MODES)
t_start = time.time()
done_eps = 0
print(f"\n평가 시작: {n_scen} 시나리오 × {args.episodes}ep × {MODES}  (총 {total_eps} 에피소드)\n")
for si, name in enumerate(scenario_paths, 1):
    route = scenario_paths[name]
    demand = EVAL_SCENARIOS[name]
    t_scen = time.time()
    print(f"[{si}/{n_scen}] {name} 시작...")
    for ep in range(args.episodes):
        seed = args.seed_base + ep   # 모든 모드 동일 seed → 동일 트래픽
        for mode in MODES:
            m = run_episode(mode, route, seed, demand=demand, name=name, episode=ep)
            records.append({'scenario': name, 'mode': mode, 'episode': ep, 'seed': seed, **m})
            done_eps += 1
        # 에피소드 단위 진행률 (모든 모드 끝날 때마다)
        elapsed = time.time() - t_start
        eta = elapsed / done_eps * (total_eps - done_eps)
        print(f"    {name:11s} ep {ep+1:2d}/{args.episodes}  "
              f"({done_eps}/{total_eps}, 경과 {elapsed/60:4.1f}분, 남은 ~{eta/60:4.1f}분)")
    # 시나리오 요약 + 중간저장
    line = f"[{name:11s} 완료 {(time.time()-t_scen)/60:.1f}분]"
    for mode in MODES:
        w = [r['mean_system_total_waiting_time_s'] for r in records if r['scenario'] == name and r['mode'] == mode]
        q = [r['mean_queue_vehicles'] for r in records if r['scenario'] == name and r['mode'] == mode]
        line += f"  {LABELS[mode]}: wait={np.mean(w):7.1f} queue={np.mean(q):5.1f}"
    print(line)
    flush_csv(records)   # 시나리오 단위 중간저장 (크래시 보험)
    print(f"    [중간저장] {OUT_CSV} ({len(records)}행)\n")


# ── 종합 표 ──
def agg(scenario, mode, key):
    vals = [r[key] for r in records if r['scenario'] == scenario and r['mode'] == mode]
    return (np.mean(vals), np.std(vals)) if vals else (0.0, 0.0)


# fixed 를 기준선으로, 나머지 모드는 mean±std + (fixed 대비 개선율) 표시
other_modes = [m for m in MODES if m != 'fixed']
colw = 24
header_w = 78 + colw * len(other_modes)
print("\n" + "=" * header_w)
hdr = f"{'시나리오':12s} {'메트릭':12s} {'Fixed':>16s}"
for m in other_modes:
    hdr += f" {LABELS[m] + ' (개선)':>{colw}s}"
print(hdr)
print("=" * header_w)
# (key, 표시명, 방향): 'lower'=낮을수록 좋음, 'higher'=높을수록 좋음(arrived_vehicles)
TABLE_METRICS = [('mean_system_total_waiting_time_s', 'System Wait(s)', 'lower'),
                 ('mean_queue_vehicles', 'Avg Queue', 'lower'),
                 ('max_queue_vehicles', 'Max Queue', 'lower'),
                 ('arrived_vehicles', 'Throughput', 'higher')]
for name in scenario_paths:
    for key, label, direction in TABLE_METRICS:
        fm, fs = agg(name, 'fixed', key)
        row = f"{name:12s} {label:12s} {fm:9.2f}±{fs:5.2f}"
        for m in other_modes:
            mm, ms = agg(name, m, key)
            # 개선율: lower 메트릭은 (fixed-mode), higher(arrived_vehicles)는 (mode-fixed)
            change = ((fm - mm) if direction == 'lower' else (mm - fm)) / fm * 100 if fm else None
            comparison = f"{change:+5.1f}%" if change is not None else "n/a"
            row += f" {mm:9.2f}±{ms:5.2f}({comparison})"
        print(row)
    print("-" * header_w)


# ── CSV 최종 저장 (시나리오마다 중간저장했지만 마지막으로 한 번 더 확정) ──
flush_csv(records)
print(f"[INFO] CSV 저장: {OUT_CSV}")


# ── 차트 (시나리오별 그룹 막대, N개 모드 일반화) ──
if args.skip_plots:
    sys.exit(0)
STYLE = {'fixed': ('#546E7A', 'Fixed'),
         'webster': ('#8E7CC3', 'Webster'),
         'rl_global': ('#00897B', 'SmartSignal(god-view)'),
         'rl_e2': ('#FF7043', 'SmartSignal(E2)')}
scen = list(scenario_paths)
x = np.arange(len(scen))
n = len(MODES)
width = 0.8 / n
fig, axes = plt.subplots(1, 3, figsize=(21, 6))
fig.patch.set_facecolor('#0D1B2A')
title_modes = ' vs '.join(STYLE.get(m, ('', m))[1] for m in MODES)
fig.suptitle(f'{title_modes} — Per-Scenario Comparison', color='white',
             fontsize=15, fontweight='bold')

for ax, (key, title) in zip(axes, [('mean_system_total_waiting_time_s', 'Mean system waiting sum (s)'),
                                    ('mean_queue_vehicles', 'Avg Queue Length (lower better)'),
                                    ('arrived_vehicles', 'Throughput (higher better)')]):
    ax.set_facecolor('#0D1B2A')
    for i, m in enumerate(MODES):
        means = [agg(s, m, key)[0] for s in scen]
        stds = [agg(s, m, key)[1] for s in scen]
        color, label = STYLE.get(m, ('#888888', m))
        offset = (i - (n - 1) / 2) * width
        ax.bar(x + offset, means, width, yerr=stds, label=label, color=color,
               capsize=4, edgecolor='white', linewidth=0.5)
    ax.set_title(title, color='white', fontsize=11, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(scen, color='white', rotation=15)
    ax.tick_params(colors='white')
    ax.spines[:].set_color('#00897B')
    ax.grid(axis='y', alpha=0.15, color='white')
    ax.legend(facecolor='#1a2d40', labelcolor='white')

plt.tight_layout()
out_png = str(out_dir / "evaluation_by_scenario.png")
plt.savefig(out_png, dpi=150, bbox_inches='tight', facecolor='#0D1B2A')
print(f"[INFO] 그래프 저장: {out_png}")
