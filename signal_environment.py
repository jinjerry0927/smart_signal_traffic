"""Smart Signal 공통 신호 제어 환경.

이 모듈은 sumo-rl 1.4 계열에서 적용되지 않는 ``max_green``과 지원되지
않는 전적색 단계를 보완한다. 액션 값은 녹색 단계 인덱스이며 현재 단계
(Keep) 또는 바로 다음 순환 단계(Next)만 허용한다.

``TrafficSignal.green_phase``는 녹색 중에는 현재 활성 단계다. 황색과
전적색 중에는 전환 출발 단계이며, 새 녹색이 켜지는 순간 다음 단계로
바뀐다. 실제 녹색 경과 시간은 ``env.green_elapsed`` 또는
``ts.green_elapsed``로 읽는다. 전환 중에는 0이다.

이 규칙은 SUMO 비교 실험용이며 실도로의 공학적 안전을 보증하지 않는다.
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple, Union

import gymnasium as gym
from gymnasium import spaces
from sumo_rl.environment.env import SumoEnvironment
from sumo_rl.environment.observations import DefaultObservationFunction, ObservationFunction
from sumo_rl.environment.traffic_signal import TrafficSignal


DELTA_TIME = 5
MIN_GREEN = 15
MAX_GREEN = 60
YELLOW_TIME = 3
ALL_RED_TIME = 2
CONTROL_VERSION = "smart-signal-cyclic-v1"


class _CyclicTrafficSignal(TrafficSignal):
    """현재/다음 순환 액션과 녹색-황색-전적색 전환을 적용한다."""

    def _build_phases(self) -> None:
        source_phases = self.sumo.trafficlight.getAllProgramLogics(self.id)[0].phases
        green_states = [
            phase.state
            for phase in source_phases
            if "y" not in phase.state.lower()
            and any(signal in phase.state for signal in ("g", "G"))
        ]
        if len(green_states) != 4:
            raise ValueError(
                f"SignalEnvironment requires exactly 4 green phases for {self.id!r}; "
                f"found {len(green_states)}"
            )
        if len({len(state) for state in green_states}) != 1:
            raise ValueError(f"Traffic-light states for {self.id!r} have inconsistent lengths")

        self.green_phases = [self.sumo.trafficlight.Phase(MAX_GREEN, state) for state in green_states]
        self.yellow_phases = [
            self.sumo.trafficlight.Phase(
                YELLOW_TIME,
                "".join("y" if signal in "gG" else signal for signal in state),
            )
            for state in green_states
        ]
        self.all_red_phase = self.sumo.trafficlight.Phase(ALL_RED_TIME, "r" * len(green_states[0]))
        self.num_green_phases = len(self.green_phases)

        # SUMO에도 완전한 순환 프로그램을 등록해 모든 녹색 종료가 황색을
        # 거치도록 한다. 실행 중 전환 시각은 update()가 직접 제어한다.
        self.all_phases = []
        for green, yellow in zip(self.green_phases, self.yellow_phases):
            self.all_phases.extend((green, yellow, self.all_red_phase))
        logic = self.sumo.trafficlight.getAllProgramLogics(self.id)[0]
        logic.type = 0
        logic.phases = self.all_phases
        self.sumo.trafficlight.setProgramLogic(self.id, logic)
        self.sumo.trafficlight.setRedYellowGreenState(self.id, self.green_phases[0].state)

        self.green_phase = 0
        self.green_elapsed = 0
        self._stage = "green"
        self._stage_elapsed = 0
        self._pending_green: Optional[int] = None
        self.is_yellow = False
        self.is_all_red = False
        # sumo-rl 기본/E2 관측은 이 값에서 yellow_time을 빼는 식으로
        # 최소 녹색 여부를 판단한다. 녹색 중 실제 green_elapsed와 맞춘다.
        self.time_since_last_phase_change = self.yellow_time

    def update(self) -> None:
        if self._stage == "green":
            self.green_elapsed += 1
            self.time_since_last_phase_change = self.green_elapsed + self.yellow_time
            return

        self._stage_elapsed += 1
        self.time_since_last_phase_change = 0
        if self._stage == "yellow" and self._stage_elapsed == self.yellow_time:
            self.sumo.trafficlight.setRedYellowGreenState(self.id, self.all_red_phase.state)
            self._stage = "all_red"
            self._stage_elapsed = 0
            self.is_yellow = False
            self.is_all_red = True
        elif self._stage == "all_red" and self._stage_elapsed == ALL_RED_TIME:
            if self._pending_green is None:  # 방어적 검사: 정상 경로에서는 발생하지 않는다.
                raise RuntimeError(f"Missing pending green phase for {self.id!r}")
            self.green_phase = self._pending_green
            self._pending_green = None
            self.sumo.trafficlight.setRedYellowGreenState(
                self.id, self.green_phases[self.green_phase].state
            )
            self._stage = "green"
            self._stage_elapsed = 0
            self.is_all_red = False
            self.green_elapsed = 0
            self.time_since_last_phase_change = self.yellow_time

    def set_next_phase(self, new_phase: int) -> None:
        try:
            requested = int(new_phase)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Phase action must be an integer, got {new_phase!r}") from exc

        current = self.green_phase
        next_cyclic = (current + 1) % self.num_green_phases
        if requested not in (current, next_cyclic):
            raise ValueError(
                f"Unsupported phase jump {current}->{requested}; "
                f"use {current} (Keep) or {next_cyclic} (Next)"
            )
        if self._stage != "green":
            raise RuntimeError(f"Cannot request a phase while {self.id!r} is in {self._stage}")

        force_next = self.green_elapsed >= self.max_green
        can_change = self.green_elapsed >= self.min_green
        if (requested == next_cyclic and can_change) or force_next:
            self._pending_green = next_cyclic
            self.sumo.trafficlight.setRedYellowGreenState(
                self.id, self.yellow_phases[current].state
            )
            self._stage = "yellow"
            self._stage_elapsed = 0
            self.is_yellow = True
            self.is_all_red = False
            self.green_elapsed = 0
            self.time_since_last_phase_change = 0
        else:
            self.sumo.trafficlight.setRedYellowGreenState(
                self.id, self.green_phases[current].state
            )
        self.next_action_time = self.env.sim_step + self.delta_time


class SignalEnvironment(SumoEnvironment):
    """sumo_rl.SumoEnvironment 호환 단일 교차로 공통 제어 환경."""

    def __init__(
        self,
        net_file: str,
        route_file: str,
        out_csv_name: Optional[str] = None,
        use_gui: bool = False,
        virtual_display: Tuple[int, int] = (3200, 1800),
        begin_time: int = 0,
        num_seconds: int = 20000,
        max_depart_delay: int = -1,
        waiting_time_memory: int = 1000,
        time_to_teleport: int = -1,
        delta_time: int = DELTA_TIME,
        yellow_time: int = YELLOW_TIME,
        min_green: int = MIN_GREEN,
        max_green: int = MAX_GREEN,
        single_agent: bool = True,
        reward_fn: Union[str, Callable] = "diff-waiting-time",
        observation_class: ObservationFunction = DefaultObservationFunction,
        add_system_info: bool = True,
        add_per_agent_info: bool = True,
        sumo_seed: Union[str, int] = "random",
        fixed_ts: bool = False,
        sumo_warnings: bool = True,
        additional_sumo_cmd: Optional[str] = None,
        render_mode: Optional[str] = None,
    ) -> None:
        # 유효성 검사에서 생성자가 일찍 끝나도 상위 소멸자가 안전하게 닫힌다.
        self.sumo = None
        expected = {
            "delta_time": (delta_time, DELTA_TIME),
            "yellow_time": (yellow_time, YELLOW_TIME),
            "min_green": (min_green, MIN_GREEN),
            "max_green": (max_green, MAX_GREEN),
        }
        invalid = [
            f"{name}={actual} (expected {wanted})"
            for name, (actual, wanted) in expected.items()
            if actual != wanted
        ]
        if invalid:
            raise ValueError("Unsupported signal timing: " + ", ".join(invalid))
        if not single_agent:
            raise ValueError("SignalEnvironment supports single_agent=True only")
        if fixed_ts:
            raise ValueError("SignalEnvironment does not support fixed_ts=True")
        if isinstance(reward_fn, dict):
            raise ValueError("SignalEnvironment does not support per-signal reward dictionaries")

        # super().__init__은 네트워크 메타데이터를 읽기 위해 임시 연결을 연다.
        # fixed_ts=True로 넘겨 sumo-rl의 불완전한 phase 재작성을 피한 뒤,
        # 실제 reset에서는 아래 사용자 정의 신호 객체를 설치한다.
        super().__init__(
            net_file=net_file,
            route_file=route_file,
            out_csv_name=out_csv_name,
            use_gui=use_gui,
            virtual_display=virtual_display,
            begin_time=begin_time,
            num_seconds=num_seconds,
            max_depart_delay=max_depart_delay,
            waiting_time_memory=waiting_time_memory,
            time_to_teleport=time_to_teleport,
            delta_time=delta_time,
            yellow_time=yellow_time,
            min_green=min_green,
            max_green=max_green,
            single_agent=single_agent,
            reward_fn=reward_fn,
            observation_class=observation_class,
            add_system_info=add_system_info,
            add_per_agent_info=add_per_agent_info,
            sumo_seed=sumo_seed,
            fixed_ts=True,
            sumo_warnings=sumo_warnings,
            additional_sumo_cmd=additional_sumo_cmd,
            render_mode=render_mode,
        )
        if len(self.ts_ids) != 1:
            raise ValueError(
                f"SignalEnvironment currently supports exactly one traffic signal; found {len(self.ts_ids)}"
            )
        self.fixed_ts = False

        # reset 전에도 기존 호출자가 올바른 4단계 공간을 조회할 수 있게 한다.
        prototype = self.traffic_signals[self.ts_ids[0]]
        prototype.num_green_phases = 4
        prototype.green_elapsed = 0
        prototype.observation_fn = self.observation_class(prototype)
        prototype.observation_space = prototype.observation_fn.observation_space()
        prototype.action_space = spaces.Discrete(4)

    def reset(self, seed: Optional[int] = None, **kwargs):
        gym.Env.reset(self, seed=seed, **kwargs)
        if self.episode != 0:
            self.close()
            self.save_csv(self.out_csv_name, self.episode)
        self.episode += 1
        self.metrics = []
        if seed is not None:
            self.sumo_seed = seed

        self._start_simulation()
        ts_id = self.ts_ids[0]
        self.traffic_signals = {
            ts_id: _CyclicTrafficSignal(
                self,
                ts_id,
                self.delta_time,
                self.yellow_time,
                self.min_green,
                self.max_green,
                self.begin_time,
                self.reward_fn,
                self.sumo,
            )
        }
        self.vehicles = {}
        observation = self._compute_observations()[ts_id]
        return observation, self._compute_info()

    @property
    def green_elapsed(self) -> int:
        """현재 활성 녹색의 경과 초. 황색·전적색 중에는 0."""

        return self.traffic_signals[self.ts_ids[0]].green_elapsed

    def step(self, action):
        # upstream의 None 경로는 신호 update를 건너뛰므로 진행 전에 거부한다.
        if not self.action_space.contains(action):
            raise ValueError("Action must be an integer green phase index (Keep/Next).")
        return super().step(action)
