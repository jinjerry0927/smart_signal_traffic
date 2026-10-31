"""Instrument frozen controllers without changing their code or observations."""
import collections
import json
import math
from pathlib import Path
import platform
import subprocess
import time
import xml.etree.ElementTree as ET

from tools.unseen_transfer_build import ROOT, BUNDLE, sumolib
import numpy as np
from traci import constants as tc
from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2
from transferable_signal.controllers import Baseline
from transferable_signal.deployment import validated_policy
from transferable_signal.environment import SignalEnvironment
from transferable_signal.model import SmartSignal
from transferable_signal.spec import digest, read_json, write_json


def json_default(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    raise TypeError(type(value).__name__)


def emit(stream, value):
    stream.write(json.dumps(value, default=json_default, allow_nan=False, separators=(',', ':')) + '\n')


def clock_data(clock):
    return dict(current=int(clock.current), pending=clock.pending, stage=clock.stage, elapsed=clock.elapsed,
                remaining=clock.remaining, red_age=clock.red_age.tolist())


class RulesPressure(AdaptiveSmartSignalV2):
    """The only intervention is replacing the learned score vector by pressure."""
    def action(self, jid, observation, clock):
        self.diagnostic_pressure = np.asarray(observation['pressure'], dtype=float).copy()
        try:
            return super().action(jid, observation, clock)
        finally:
            self.diagnostic_pressure = None

    def scores(self, features):
        if self.diagnostic_pressure is None:
            raise ValueError('Pressure score requested outside a diagnostic decision')
        return self.diagnostic_pressure.copy()


def controller(name, limits):
    path = ROOT / 'results/runs/final_signal_wave10_20261007/release/model.json'
    if name == 'smart_v2':
        return validated_policy(path)[0]
    if name == 'raw_smart':
        return SmartSignal(path)
    if name == 'rules_pressure':
        return RulesPressure(path, 'load_gate_pressure_wait35')
    return Baseline(name, limits)


class TracedEnvironment(SignalEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frames = (self.output / 'frames.jsonl').open('x', encoding='utf-8')
        self.last_time = None
        self.subscribed = set()
        self.lane_ids = None

    def observe(self):
        observation = super().observe()
        now = int(self.connection.simulation.getTime())
        if now == self.last_time:
            return observation
        if self.lane_ids is None:
            self.lane_ids = sorted(self.connection.lane.getIDList())
            for lid in self.lane_ids:
                self.connection.lane.subscribe(lid, [tc.LAST_STEP_VEHICLE_HALTING_NUMBER,
                    tc.LAST_STEP_VEHICLE_NUMBER, tc.LAST_STEP_VEHICLE_ID_LIST])
        ids = set(self.connection.vehicle.getIDList())
        for vid in sorted(ids - self.subscribed):
            self.connection.vehicle.subscribe(vid, [tc.VAR_SPEED, tc.VAR_WAITING_TIME])
        self.subscribed = ids
        vehicle_results = self.connection.vehicle.getAllSubscriptionResults()
        vehicles = {vid: [vehicle_results[vid][tc.VAR_SPEED], vehicle_results[vid][tc.VAR_WAITING_TIME]] for vid in sorted(ids)}
        lane_results = self.connection.lane.getAllSubscriptionResults()
        lanes = {lid: [lane_results[lid][tc.LAST_STEP_VEHICLE_HALTING_NUMBER],
                       lane_results[lid][tc.LAST_STEP_VEHICLE_NUMBER], list(lane_results[lid][tc.LAST_STEP_VEHICLE_ID_LIST])]
                 for lid in self.lane_ids}
        for jid, value in observation.items():
            if value['features'].shape != (len(self.clocks[jid].phases), 7) or not np.isfinite(value['features']).all() or not value['mask'].any():
                raise ValueError('Invalid observed feature/mask shape at ' + jid)
        frame = dict(time_s=now, vehicles=vehicles, lanes=lanes,
            pending=list(self.connection.simulation.getPendingVehicles()) if now == self.horizon_s else None,
            departed=list(self.connection.simulation.getDepartedIDList()), arrived=list(self.connection.simulation.getArrivedIDList()),
            collisions=self.connection.simulation.getCollidingVehiclesNumber(), teleports=self.connection.simulation.getStartingTeleportNumber(),
            clocks={jid: clock_data(c) for jid, c in self.clocks.items()},
            signals={jid: self.connection.trafficlight.getRedYellowGreenState(jid) for jid in self.clocks},
            observations=observation)
        emit(self.frames, frame)
        self.last_time = now
        if now:
            queue = sum(v[0] < .1 for v in vehicles.values())
            waiting = sum(v[1] for v in vehicles.values())
            if queue != self.metrics.queues[-1] or not math.isclose(waiting, self.metrics.waits[-1], abs_tol=1e-6):
                raise ValueError('Subscription trace differs from original frozen metrics')
            if frame['collisions'] or frame['teleports']:
                raise RuntimeError('Collision or teleport; stop subsequent experiments')
        return observation

    def close(self):
        try:
            super().close()
        finally:
            if hasattr(self, 'frames') and not self.frames.closed:
                self.frames.close()


def run_one(spec, network, routes, scenario, name, output, fingerprint, kind='final'):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Run output already exists: ' + str(output))
    policy = controller(name, spec['limits'])
    output.mkdir(parents=True)
    metadata = dict(status='started', kind=kind, controller=name, scenario=scenario,
                    network_sha256=digest(network), routes_sha256=digest(routes),
                    fingerprint=fingerprint, python=platform.python_version(), numpy=np.__version__,
                    sumo=subprocess.check_output([sumolib.checkBinary('sumo'), '--version'], text=True).splitlines()[0])
    write_json(output / 'manifest.json', metadata)
    horizon = scenario['duration_s'] + scenario['drain_s']
    env = TracedEnvironment(spec, network, routes, output, horizon, scenario['seed'])
    try:
        observations = env.reset()
        done, times, modes = False, [], collections.Counter()
        with (output / 'decisions.jsonl').open('x', encoding='utf-8') as stream:
            while not done:
                decisions, actions = {}, {}
                now = int(env.connection.simulation.getTime())
                for jid, value in observations.items():
                    snapshot = clock_data(env.clocks[jid])
                    started = time.perf_counter()
                    action = policy.action(jid, value, env.clocks[jid])
                    times.append((time.perf_counter() - started) * 1000)
                    details = dict(getattr(policy, 'last_decision', {}) or {})
                    mode = details.get('mode', name)
                    modes[mode] += 1
                    scores = (value['pressure'] if name == 'rules_pressure' else policy.scores(value['features'])
                              if name in ('smart_v2', 'raw_smart') else None)
                    decisions[jid] = dict(action=int(action), clock=snapshot, features=value['features'],
                                         mask=value['mask'], pressure=value['pressure'], scores=scores, details=details)
                    actions[jid] = action
                emit(stream, dict(time_s=now, junctions=decisions))
                observations, _, done = env.step(actions)
        result = env.finish()
        trips = [t for t in ET.parse(output / 'tripinfo.xml').getroot().findall('tripinfo') if float(t.get('arrival')) >= 0]
        result.update(status='completed', controller=name, scenario=scenario['id'],
            p95_completed_waiting_time_s=float(np.percentile([float(t.get('waitingTime')) for t in trips], 95)) if trips else None,
            mean_decision_ms_per_junction=float(np.mean(times)), p95_decision_ms_per_junction=float(np.percentile(times, 95)),
            policy_mode_usage=dict(modes))
        warnings = [line for line in (output / 'sumo-errors.log').read_text(encoding='utf-8').splitlines() if 'Warning:' in line]
        result['warnings'] = warnings
        if result['collision_vehicle_events'] or result['teleport_events']:
            raise RuntimeError('Observed collision/teleport in completed metrics')
        if 'Error:' in (output / 'sumo-errors.log').read_text(encoding='utf-8'):
            raise RuntimeError('SUMO reported an error')
        write_json(output / 'metrics.json', result)
        metadata.update(status='completed', files_sha256={p.name: digest(p) for p in output.iterdir()
                        if p.is_file() and p.name != 'manifest.json'})
        return result
    except BaseException as error:
        metadata.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        env.close()
        write_json(output / 'manifest.json', metadata)
