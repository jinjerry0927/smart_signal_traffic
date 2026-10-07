"""E2 관측이 정상 차원(53D)·범위(0~1)로 나오는지 스모크 검증. 직접 실행."""
import os, sys
os.environ.setdefault("SUMO_HOME", r"C:/Program Files (x86)/Eclipse/Sumo")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from signal_environment import SignalEnvironment
from e2_observation import E2ObservationFunction

def main():
    env = SignalEnvironment(
        net_file="network/intersection.net.xml",
        route_file="scenarios/eval/medium.rou.xml",
        use_gui=False, num_seconds=300, min_green=15, max_green=60,
        single_agent=True, sumo_warnings=False,
        observation_class=E2ObservationFunction,
        additional_sumo_cmd="-a network/e2.add.xml --no-step-log")
    obs, _ = env.reset()
    for _ in range(20):
        current = int(np.asarray(obs[:4]).argmax())
        obs, *_ = env.step((current + int(env.action_space.sample() % 2)) % 4)
    env.close()
    obs = np.asarray(obs)
    assert obs.shape == (53,), f"기대 53D, 실제 {obs.shape}"
    assert np.all(np.isfinite(obs)), "비유한값 존재"
    assert obs.min() >= 0.0 and obs.max() <= 1.0001, f"범위 위반 [{obs.min()},{obs.max()}]"
    print("[PASS] E2 관측 53D, 0~1 범위 OK")

if __name__ == "__main__":
    main()
