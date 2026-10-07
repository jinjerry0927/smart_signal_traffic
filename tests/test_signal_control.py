"""공통 신호 제어의 실제 TraCI 상태 전환 검사."""

from pathlib import Path
import os
import sys
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SUMO_HOME", "C:/Program Files (x86)/Eclipse/Sumo")

from e2_observation import E2ObservationFunction
from signal_environment import (
    ALL_RED_TIME,
    DELTA_TIME,
    MAX_GREEN,
    MIN_GREEN,
    YELLOW_TIME,
    SignalEnvironment,
)


ROOT = Path(__file__).resolve().parents[1]
NET = str(ROOT / "network" / "intersection.net.xml")
ROUTE = str(ROOT / "scenarios" / "eval" / "medium.rou.xml")
E2 = str(ROOT / "network" / "e2.add.xml")


def make_env(**kwargs):
    options = dict(
        net_file=NET,
        route_file=ROUTE,
        num_seconds=500,
        sumo_seed=7,
        sumo_warnings=False,
        additional_sumo_cmd="--no-step-log",
    )
    options.update(kwargs)
    return SignalEnvironment(**options)


def assert_static_program():
    phases = ET.parse(NET).findall(".//tlLogic[@id='center']/phase")
    assert len(phases) == 12
    greens = phases[0::3]
    assert [int(phase.attrib["duration"]) for phase in greens] == [30, 18, 30, 18]
    for index in range(0, len(phases), 3):
        green, yellow, all_red = phases[index : index + 3]
        assert int(yellow.attrib["duration"]) == YELLOW_TIME
        assert int(all_red.attrib["duration"]) == ALL_RED_TIME
        assert set(all_red.attrib["state"]) == {"r"}
        for before, warning in zip(green.attrib["state"], yellow.attrib["state"]):
            assert warning == ("y" if before in "gG" else before)


def advance_clearance(env, ts, expected_next):
    source_state = ts.green_phases[ts.green_phase].state
    ts.set_next_phase(expected_next)
    assert env.sumo.trafficlight.getRedYellowGreenState(ts.id) == ts.yellow_phases[ts.green_phase].state
    assert ts.green_elapsed == 0

    observed = [env.sumo.trafficlight.getRedYellowGreenState(ts.id)]
    for _ in range(YELLOW_TIME + ALL_RED_TIME):
        env._sumo_step()
        ts.update()
        observed.append(env.sumo.trafficlight.getRedYellowGreenState(ts.id))

    yellow_state = "".join("y" if signal in "gG" else signal for signal in source_state)
    assert observed[:YELLOW_TIME] == [yellow_state] * YELLOW_TIME
    assert observed[YELLOW_TIME : YELLOW_TIME + ALL_RED_TIME] == ["r" * len(source_state)] * ALL_RED_TIME
    assert observed[-1] == ts.green_phases[expected_next].state
    assert ts.green_phase == expected_next
    assert ts._stage == "green"
    assert ts.green_elapsed == 0


def assert_next_and_cycle():
    env = make_env()
    try:
        observation, _ = env.reset()
        ts = env.traffic_signals[env.ts_ids[0]]
        assert observation.shape == (29,)
        assert np.isfinite(observation).all()
        assert observation[4] == 0
        for invalid_action in (None, {}, 1.5):
            try:
                env.step(invalid_action)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid action was accepted")
            assert env.sim_step == 0 and ts.green_elapsed == 0

        # 조기 Next 요청은 최소 녹색까지 현재 단계를 유지한다.
        for expected_elapsed in (5, 10, 15):
            observation, *_ = env.step(1)
            assert ts.green_phase == 0
            assert ts._stage == "green"
            assert ts.green_elapsed == expected_elapsed
        assert observation[4] == 1

        visited = []
        for _ in range(4):
            next_phase = (ts.green_phase + 1) % ts.num_green_phases
            advance_clearance(env, ts, next_phase)
            visited.append(ts.green_phase)
            if len(visited) < 4:
                for _ in range(MIN_GREEN // DELTA_TIME):
                    env.step((ts.green_phase + 1) % ts.num_green_phases)
        assert visited == [1, 2, 3, 0]

        try:
            ts.set_next_phase(2)
        except ValueError as exc:
            assert "Unsupported phase jump" in str(exc)
        else:
            raise AssertionError("non-cyclic phase jump was accepted")

        # reset은 단계와 실제 녹색 시간을 함께 초기화한다.
        observation, _ = env.reset(seed=9)
        ts = env.traffic_signals[env.ts_ids[0]]
        assert ts.green_phase == 0 and env.green_elapsed == 0
        assert observation.shape == (29,) and np.isfinite(observation).all()
    finally:
        env.close()


def assert_max_green_forces_next():
    env = make_env()
    try:
        env.reset()
        ts = env.traffic_signals[env.ts_ids[0]]
        for _ in range(MAX_GREEN // DELTA_TIME):
            env.step(ts.green_phase)
        assert env.sim_step == MAX_GREEN
        assert ts.green_elapsed == MAX_GREEN
        ts.set_next_phase(ts.green_phase)
        assert env.sim_step == MAX_GREEN
        assert ts._stage == "yellow"
        advance_state = env.sumo.trafficlight.getRedYellowGreenState(ts.id)
        assert "y" in advance_state and "g" not in advance_state.lower()
        advance_clearance_from_active_transition(env, ts, 1)
    finally:
        env.close()


def advance_clearance_from_active_transition(env, ts, expected_next):
    observed = [env.sumo.trafficlight.getRedYellowGreenState(ts.id)]
    for _ in range(YELLOW_TIME + ALL_RED_TIME):
        env._sumo_step()
        ts.update()
        observed.append(env.sumo.trafficlight.getRedYellowGreenState(ts.id))
    assert all("y" in state and "g" not in state.lower() for state in observed[:YELLOW_TIME])
    assert all(set(state) == {"r"} for state in observed[YELLOW_TIME:-1])
    assert observed[-1] == ts.green_phases[expected_next].state
    assert ts.green_phase == expected_next and ts.green_elapsed == 0


def assert_e2_observation():
    env = make_env(
        observation_class=E2ObservationFunction,
        additional_sumo_cmd=f"-a {E2} --no-step-log",
    )
    try:
        observation, _ = env.reset()
        assert observation.shape == (53,)
        assert np.isfinite(observation).all()
        for _ in range(3):
            observation, *_ = env.step(1)
        assert observation[4] == 1
        assert np.isfinite(observation).all()
    finally:
        env.close()


def assert_unsupported_timing_rejected():
    try:
        make_env(delta_time=DELTA_TIME + 1)
    except ValueError as exc:
        assert "Unsupported signal timing" in str(exc)
    else:
        raise AssertionError("unsupported delta_time was accepted")


if __name__ == "__main__":
    assert_static_program()
    assert_next_and_cycle()
    assert_max_green_forces_next()
    assert_e2_observation()
    assert_unsupported_timing_rejected()
    print("PASS: cyclic signal, clearance, min/max green, reset, 29D/53D observations")
