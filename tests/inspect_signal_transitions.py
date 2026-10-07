"""G0 신호 전환 진단. 기존 모델·수요를 보존하고 새 출력 폴더에 증거를 저장한다."""
import argparse
import ast
import csv
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

os.environ.setdefault("SUMO_HOME", "C:/Program Files (x86)/Eclipse/Sumo")

import numpy as np
import sumo_rl
import sumolib
import traci

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
NET = ROOT / "network/intersection.net.xml"
ROUTE = ROOT / "scenarios/eval/medium.rou.xml"


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def snapshot(connection, target=None):
    return dict(time=connection.simulation.getTime(),
                state=connection.trafficlight.getRedYellowGreenState("center"),
                program=connection.trafficlight.getProgram("center"),
                phase=connection.trafficlight.getPhase("center"), target=target)


def save_trace(out, name, rows):
    with (out / f"{name}.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    intervals = []
    for row in rows:
        if not intervals or intervals[-1]["state"] != row["state"]:
            intervals.append(dict(start=row["time"], end=row["time"] + 1,
                                  state=row["state"]))
        else:
            intervals[-1]["end"] = row["time"] + 1
    return intervals


def inspect_static(out):
    tree = ET.parse(NET)
    phases = tree.find(".//tlLogic[@id='center']").findall("phase")
    direct_red = []
    for index, phase in enumerate(phases):
        next_index = (index + 1) % len(phases)
        links = [i for i, (a, b) in enumerate(zip(phase.get("state"),
                 phases[next_index].get("state"))) if a in "gG" and b == "r"]
        if links:
            direct_red.append(dict(source=index, destination=next_index, links=links))

    command = [sumolib.checkBinary("sumo"), "-n", str(NET), "-r", str(ROUTE),
               "--seed", "0", "--no-step-log", "true", "--error-log",
               str(out / "static-warnings.log")]
    traci.start(command, label="g0_static")
    connection = traci.getConnection("g0_static")
    rows = []
    try:
        for _ in range(120):
            rows.append(snapshot(connection))  # Step 호출 직전의 TraCI 상태 샘플.
            connection.simulationStep()
    finally:
        connection.close()

    # 원본은 보존한다. 별도 복사본의 두 황색 단계만 바꿔 경고 원인을 분리한다.
    for transition in direct_red:
        phase = phases[transition["source"]]
        state = list(phase.get("state"))
        for link in transition["links"]:
            state[link] = "y"
        phase.set("state", "".join(state))
    probe_net = out / "yellow-probe.net.xml"
    tree.write(probe_net, encoding="utf-8", xml_declaration=True)
    probe_command = [command[0], "-n", str(probe_net), "-r", str(ROUTE),
                     "--seed", "0", "--end", "1", "--no-step-log", "true"]
    probe = subprocess.run(probe_command, capture_output=True, text=True, check=True)
    (out / "probe.log").write_text(probe.stdout + probe.stderr, encoding="utf-8")
    warnings = (out / "static-warnings.log").read_text(encoding="utf-8")
    return dict(direct_green_to_red=direct_red, intervals=save_trace(out, "static", rows),
                original_warnings=warnings, probe_stderr=probe.stderr,
                probe_exit_code=probe.returncode, command=command, probe_command=probe_command)


def action_functions():
    # 학습이 시작되지 않도록 모듈 전체 대신 실제 코드의 순수 함수만 읽는다.
    module = ast.parse((ROOT / "smart_signal.py").read_text(encoding="utf-8-sig"))
    names = {"current_phase_from_obs", "dqn_action_to_env_action"}
    functions = [node for node in module.body if isinstance(node, ast.FunctionDef)
                 and node.name in names]
    assert len(functions) == len(names)
    namespace = dict(np=np, ACTION_KEEP=0, NUM_GREEN_PHASES=4)
    exec(compile(ast.Module(body=functions, type_ignores=[]), "smart_signal.py", "exec"), namespace)
    return namespace


def inspect_rl(out, name, action, seconds, legacy=False):
    functions = action_functions()
    from signal_environment import SignalEnvironment
    environment = sumo_rl.SumoEnvironment if legacy else SignalEnvironment
    env = environment(net_file=str(NET), route_file=str(ROUTE),
        use_gui=False, num_seconds=seconds, min_green=15, max_green=60,
        single_agent=True, sumo_seed=0, sumo_warnings=True,
        additional_sumo_cmd="--no-step-log")
    rows, decisions = [], []
    try:
        obs, _ = env.reset()
        signal = env.traffic_signals["center"]
        original_step = env._sumo_step

        def traced_step():
            rows.append(snapshot(env.sumo, signal.green_phase))
            original_step()

        env._sumo_step = traced_step
        program = [dict(duration=p.duration, state=p.state) for p in signal.all_phases]
        while env.sim_step < seconds:
            current = functions["current_phase_from_obs"](obs)
            target = functions["dqn_action_to_env_action"](action, current)
            decisions.append(dict(time=env.sim_step, action=action, current=current, target=target))
            obs, _, terminated, truncated, _ = env.step(target)
            if terminated or truncated:
                break
        save_json(out / f"{name}-decisions.json", decisions)
        return dict(intervals=save_trace(out, name, rows), program=program,
                    delta_time=env.delta_time, yellow_time=env.yellow_time,
                    min_green=env.min_green, max_green=env.max_green,
                    observed_seconds=len(rows), all_red_seconds=sum(
                        set(row["state"]) == {"r"} for row in rows))
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="존재하지 않는 새 출력 폴더")
    parser.add_argument("--legacy", action="store_true", help="비교용 upstream sumo-rl 제어 사용")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    result = dict(python=sys.version, executable=sys.executable,
                  sumo_rl=importlib.metadata.version("sumo-rl"))
    result["static"] = inspect_static(out)
    result["next"] = inspect_rl(out, "next", 1, 105, args.legacy)
    result["keep"] = inspect_rl(out, "keep", 0, 75, args.legacy)
    save_json(out / "summary.json", result)
    print(f"[RECORDED] {out / 'summary.json'}")
    print("진단 기록 완료. 종료 코드 0은 신호 안전성 통과를 뜻하지 않습니다.")


if __name__ == "__main__":
    main()
