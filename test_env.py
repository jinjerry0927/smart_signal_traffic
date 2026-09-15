"""저장된 평시 시나리오로 기본 관측·신호 공간을 검사한다. --gui로 화면 표시."""
import argparse
import os

os.environ.setdefault("SUMO_HOME", "C:/Program Files (x86)/Eclipse/Sumo")

import numpy as np
import sumo_rl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--seconds", type=int, default=300)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds는 양수여야 합니다.")

    env = sumo_rl.SumoEnvironment(
        net_file="network/intersection.net.xml",
        route_file="scenarios/eval/medium.rou.xml",
        use_gui=args.gui,
        num_seconds=args.seconds,
        min_green=15,
        max_green=60,
        single_agent=True,
        sumo_seed=0,
        sumo_warnings=False,
        additional_sumo_cmd="--no-step-log",
    )
    try:
        obs, _ = env.reset()
        assert env.action_space.n == 4, "녹색 신호 단계는 4개여야 합니다."
        env.action_space.seed(0)
        done = False
        steps = 0
        while True:
            assert np.asarray(obs).shape == (29,), "기본 관측은 29차원이어야 합니다."
            assert np.all(np.isfinite(obs)), "관측에 비유한 값이 있습니다."
            if done:
                break
            obs, _, terminated, truncated, _ = env.step(env.action_space.sample())
            done = terminated or truncated
            steps += 1
        print(f"[PASS] 기본 관측 29D / 녹색 단계 4개 / {steps} steps")
    finally:
        env.close()


if __name__ == "__main__":
    main()
