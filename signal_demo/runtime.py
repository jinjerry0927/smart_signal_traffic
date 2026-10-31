"""Single-owner SUMO worker and presentation state. No policy changes."""
import copy
import csv
from pathlib import Path
import threading
import time
import uuid
import xml.etree.ElementTree as ET

from transferable_signal.controllers import Baseline
from transferable_signal.deployment import validated_policy
from transferable_signal.environment import SignalEnvironment
from transferable_signal.runner import _check_scenario
from transferable_signal.spec import digest, read_json, write_json
from transferable_signal.suite import load_suite

from .protocol import EdgeClient
from .release import DEFAULT_RELEASE, ROOT, load_release

NAMES = dict(gj_yonggang='용강네거리', gj_geunhwa='근화여고네거리', gj_seungsam='승삼네거리')
SCENARIOS = dict(gyeongju_quiet='한산한 교통', gyeongju_busy='혼잡한 교통',
                 gyeongju_directional='한 방향 집중')


class StopRequested(Exception):
    pass


def map_data(network, spec, names=None):
    root = ET.parse(network).getroot()
    points = lambda shape: [[float(v) for v in pair.split(',')] for pair in shape.split()]
    lanes, edges = {}, []
    for edge in root.findall('edge'):
        for lane in edge.findall('lane'):
            lanes[lane.get('id')] = dict(shape=points(lane.get('shape')), width=float(lane.get('width', 3.2)))
        if edge.get('function') != 'internal':
            edges.append(dict(id=edge.get('id'), lanes=[l.get('id') for l in edge.findall('lane')]))
    junctions = []
    junction_ids = {j['id'] for j in spec['junctions']}
    names = NAMES if names is None else names
    for j in root.findall('junction'):
        if j.get('id') in junction_ids:
            junctions.append(dict(id=j.get('id'), name=names.get(j.get('id'), j.get('id')),
                                  x=float(j.get('x')), y=float(j.get('y')), shape=points(j.get('shape'))))
    all_points = [p for e in edges for lid in e['lanes'] for p in lanes[lid]['shape']]
    xs, ys = zip(*all_points)
    return dict(edges=edges, lanes=lanes, junctions=junctions, bounds=[min(xs), min(ys), max(xs), max(ys)],
                signals={j['id']: dict(phases=j['phases'], movements=j['movements']) for j in spec['junctions']})


class LiveEnvironment(SignalEnvironment):
    def __init__(self, *args, on_frame, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_frame, self.last_frame = on_frame, -1

    def observe(self):
        observation = super().observe()
        now = self.connection.simulation.getTime()
        if now != self.last_frame:
            self.last_frame = now
            self.on_frame(self, observation)
        return observation


class Demo:
    def __init__(self, output=ROOT / 'results/runs/web_pi_prototype', release=DEFAULT_RELEASE):
        self.output, self.release = Path(output).resolve(), Path(release).resolve()
        self.policy, self.identity = load_release(self.release)
        # PC validates the full original deployment contract as well as the portable one.
        validated_policy(self.release / 'model.json')
        self.suite_path = ROOT / 'network/transferable_v1/suite.json'
        self.suite, self.cases = load_suite(self.suite_path)
        self.scenarios, self.junction_names = SCENARIOS.copy(), NAMES.copy()
        spec, network, _ = self.cases['gyeongju']
        self.map = map_data(network, spec)
        summary_path = ROOT / 'results/runs/final_signal_wave10_20261007/final_summary.json'
        meta = read_json(self.release / 'release.json')
        expected = meta['evidence_sha256']['results\\runs\\final_signal_wave10_20261007\\final_summary.json']
        if digest(summary_path) != expected:
            raise ValueError('Verified comparison evidence changed')
        self.comparison = read_json(summary_path)
        self.lock = threading.RLock()
        self.stop_event, self.pause_event = threading.Event(), threading.Event()
        self.worker = None
        self.state = dict(status='ready', time_s=0, horizon_s=900, vehicles=[], junctions=[],
                          queue=0, running=0, arrived=0, mean_queue=0, link='local',
                          link_message='PC 추론 준비', decision_ms=None, error=None, result=None)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def boot(self):
        return dict(map=self.map, identity=self.identity, scenarios=self.scenarios,
                    comparison=self.comparison, title='Smart Signal · 경주 3교차로')

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def start(self, options):
        if not isinstance(options, dict):
            raise ValueError('Options object required')
        scenario_id = options.get('scenario', 'gyeongju_busy')
        controller = options.get('controller', 'smart')
        mode = options.get('mode', 'local')
        if scenario_id not in self.scenarios or controller not in ('smart', 'fixed', 'webster', 'max_pressure') or mode not in ('local', 'edge'):
            raise ValueError('Unknown scenario, controller or inference mode')
        speed, horizon = options.get('speed', 30), options.get('horizon_s', 900)
        if (type(speed) not in (int, float) or not 1 <= speed <= 60
                or type(horizon) is not int or not 30 <= horizon <= 900 or horizon % 5):
            raise ValueError('Speed 1..60; horizon 30..900 in multiples of 5')
        edge_url = options.get('edge_url', 'http://127.0.0.1:9998')
        if not isinstance(edge_url, str):
            raise ValueError('Invalid edge URL')
        if mode == 'edge' and controller != 'smart':
            raise ValueError('Remote inference is available for SmartSignal')
        if mode == 'edge':
            EdgeClient(edge_url, self.identity)
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError('An experiment is already running')
            self.stop_event.clear()
            self.pause_event.clear()
            self.state.update(status='starting', error=None, result=None, time_s=0, horizon_s=horizon,
                              controller=controller, scenario=scenario_id, speed=speed, vehicles=[], junctions=[],
                              queue=0, running=0, arrived=0, mean_queue=0, decision_ms=None,
                              link='connecting' if mode == 'edge' else 'local',
                              link_message='원격 연결 확인 중' if mode == 'edge' else 'PC 추론')
            self.state.update(edge_health=None, link_error=None)
            self.worker = threading.Thread(target=self._run, args=(scenario_id, controller, mode, edge_url, speed, horizon), daemon=True)
            self.worker.start()
        return self.snapshot()

    def command(self, action):
        with self.lock:
            active = self.worker is not None and self.worker.is_alive()
            if action == 'stop' and active:
                self.stop_event.set()
                self.state['status'] = 'stopping'
            elif action == 'pause' and active and self.state['status'] == 'running':
                self.pause_event.set()
                self.state['status'] = 'pausing'
            elif action == 'resume' and active and self.state['status'] in ('paused', 'pausing'):
                self.pause_event.clear()
                self.state['status'] = 'running'
            else:
                raise ValueError('Command unavailable in the current state')
        return self.snapshot()

    def close(self):
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=10)

    def set_speed(self, speed):
        if type(speed) not in (int, float) or not 1 <= speed <= 60:
            raise ValueError('Speed must be 1..60')
        with self.lock:
            if self.state['status'] not in ('running', 'pausing', 'paused'):
                raise ValueError('Speed can change only during an active run')
            self.state['speed'] = speed
        return self.snapshot()

    def _frame(self, env, observation, speed):
        # The worker alone queries TraCI; request handlers only read locked snapshots.
        connection = env.connection
        vehicles = []
        for vid in connection.vehicle.getIDList():
            x, y = connection.vehicle.getPosition(vid)
            vehicles.append(dict(id=vid, x=x, y=y, angle=connection.vehicle.getAngle(vid),
                                 speed=connection.vehicle.getSpeed(vid)))
        junctions = []
        for j in env.spec['junctions']:
            jid, clock = j['id'], env.clocks[j['id']]
            incoming = {m['incoming'] for m in j['movements']}
            state = clock.state()
            lane_signals = {lid: 'G' if any(state[m['index']] == 'G' for m in j['movements'] if m['incoming'] == lid)
                            else 'y' if any(state[m['index']] == 'y' for m in j['movements'] if m['incoming'] == lid)
                            else 'r' for lid in incoming}
            junctions.append(dict(id=jid, name=self.junction_names.get(jid, jid), phase=clock.current, phase_count=len(clock.phases),
                                  stage=clock.stage, elapsed_s=clock.elapsed, remaining_s=clock.remaining,
                                  state=state, lane_signals=lane_signals,
                                  next_phase=clock.pending, red_age_s=clock.red_age.tolist(),
                                  queue=sum(env.observer.samples[lid]['queue'] for lid in incoming)))
        self.update(time_s=connection.simulation.getTime(), vehicles=vehicles, junctions=junctions,
                    queue=sum(v['speed'] < .1 for v in vehicles), running=len(vehicles),
                    arrived=len(env.metrics.arrived), mean_queue=(sum(env.metrics.queues) / len(env.metrics.queues)) if env.metrics.queues else 0)
        if env.metrics.collisions or env.metrics.teleports:
            raise RuntimeError('Collision or teleport observed; run stopped')
        if self.stop_event.is_set():
            raise StopRequested()
        # Pause is acknowledged at the next 1-second frame, with no extra SUMO advancement.
        while self.pause_event.is_set():
            self.update(status='paused')
            if self.stop_event.wait(.05):
                raise StopRequested()
        if self.stop_event.is_set():
            raise StopRequested()
        self.update(status='running')
        with self.lock:
            playback_speed = self.state.get('speed', speed)
        if self.stop_event.wait(1 / playback_speed):
            raise StopRequested()

    def _run(self, scenario_id, controller, mode, edge_url, speed, horizon):
        env, stream, out, metadata = None, None, None, None
        try:
            policy, identity = load_release(self.release)
            scenario = next(s for s in self.suite['scenarios'] if s['id'] == scenario_id)
            spec, network, _ = self.cases[scenario['case']]
            routes = self.suite_path.parent / scenario['routes']
            if digest(network) != spec['network_sha256'] or digest(routes) != scenario['routes_sha256']:
                raise ValueError('Frozen network or demand changed')
            _check_scenario(policy, scenario, spec, dict(mode='validated', limits=identity['limits']), self.suite)
            if controller != 'smart':
                policy = Baseline(controller, spec['limits'])
            out = self.output / (time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:8])
            out.mkdir(parents=True, exist_ok=False)
            metadata = dict(status='started', purpose='presentation_integration', scenario=scenario,
                            horizon_s=horizon, seed=scenario['seed'], controller=controller,
                            requested_mode=mode, edge_url=edge_url if mode == 'edge' else None,
                            identity=identity, network_sha256=digest(network), routes_sha256=digest(routes),
                            bridge_sha256={p.name: digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                            fallback_events=[])
            write_json(out / 'manifest.json', metadata)
            self.update(output=str(out))
            edge = None
            if mode == 'edge':
                try:
                    edge = EdgeClient(edge_url, identity)
                    health = edge.check()
                    self.update(link='edge', link_message='원격 추론 · ' + health['hostname'], edge_health=health)
                except Exception as exc:
                    edge = None
                    message = f'{type(exc).__name__}: {exc}'
                    metadata['fallback_events'].append(dict(time_s=0, reason=message))
                    self.update(link='fallback', link_message='원격 연결 실패 → PC 추론', link_error=message)
            else:
                self.update(link='local', link_message='PC 추론' if controller == 'smart' else 'PC 기본 신호')
            env = LiveEnvironment(spec, network, routes, out, horizon, scenario['seed'],
                                  on_frame=lambda e, o: self._frame(e, o, speed))
            stream = (out / 'decisions.csv').open('w', encoding='utf-8', newline='')
            writer = csv.writer(stream)
            writer.writerow(['time_s', 'junction', 'action', 'inference', 'decision_ms'])
            observation = env.reset()
            done = False
            while not done:
                started = time.perf_counter()
                if edge is not None:
                    try:
                        actions = edge.action(observation, env.clocks)
                    except Exception as exc:
                        edge = None
                        message = f'{type(exc).__name__}: {exc}'
                        metadata['fallback_events'].append(dict(time_s=env.connection.simulation.getTime(), reason=message))
                        self.update(link='fallback', link_message='원격 응답 실패 → PC 추론', link_error=message)
                if edge is None:
                    actions = {jid: policy.action(jid, value, env.clocks[jid]) for jid, value in observation.items()}
                ms = (time.perf_counter() - started) * 1000
                self.update(decision_ms=ms / len(actions))
                for jid, action in actions.items():
                    writer.writerow([env.connection.simulation.getTime(), jid, action, 'edge' if edge else 'local', ms / len(actions)])
                stream.flush()
                observation, _, done = env.step(actions)
            result = env.finish()
            if result['collision_vehicle_events'] or result['teleport_events']:
                raise RuntimeError('Unsafe run detected')
            metadata['status'] = 'completed'
            write_json(out / 'metrics.json', result)
            self.update(status='completed', result=result)
        except StopRequested:
            if metadata is not None:
                metadata['status'] = 'stopped'
            try:
                if env is not None and env.connection is not None:
                    result = env.finish()
                    write_json(out / 'metrics.json', result)
                    self.update(result=result)
                self.update(status='stopped')
            except Exception as exc:
                message = f'{type(exc).__name__}: {exc}'
                metadata.update(status='failed', error=message)
                self.update(status='failed', error=message)
        except Exception as exc:
            message = f'{type(exc).__name__}: {exc}'
            if metadata is not None:
                metadata.update(status='failed', error=message)
            self.update(status='failed', error=message)
        finally:
            for resource in (env, stream):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        message = f'Finalization failed: {type(exc).__name__}: {exc}'
                        self.update(status='failed', error=message)
                        if metadata is not None:
                            metadata.update(status='failed', error=message)
            if metadata is not None:
                try:
                    write_json(out / 'manifest.json', metadata)
                except Exception as exc:
                    self.update(status='failed', error=f'Manifest write failed: {type(exc).__name__}: {exc}')
