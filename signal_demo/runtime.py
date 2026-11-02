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

from .protocol import EdgeClient, json_bytes
from .explanation import capture, snapshot as decision_snapshot
from .release import DEFAULT_RELEASE, ROOT, load_release
from .visualization import VehicleFrames

NAMES = dict(gj_yonggang='용강네거리', gj_geunhwa='근화여고네거리', gj_seungsam='승삼네거리')
NETWORKS = dict(gyeongju='경주 산업로 · 3교차로', valid_t2='T형 · 기존 검증 도로망',
                valid_x3='X형 · 기존 검증 도로망')
PROFILES = dict(quiet='한산한 교통', busy='혼잡한 교통', directional='한 방향 집중')
SCENARIOS = {f'gyeongju_{profile}': label for profile, label in PROFILES.items()}
SUPPORTED_SCENARIOS = {f'{case}_{profile}': label for case in NETWORKS for profile, label in PROFILES.items()}


class StopRequested(Exception):
    pass


def map_data(network, spec, names=None):
    root = ET.parse(network).getroot()
    points = lambda shape: [[float(v) for v in pair.split(',')[:2]] for pair in (shape or '').split()]
    lanes, edges = {}, []
    for edge in root.findall('edge'):
        for lane in edge.findall('lane'):
            lanes[lane.get('id')] = dict(shape=points(lane.get('shape')), width=float(lane.get('width', 3.2)),
                                         length=float(lane.get('length')), index=int(lane.get('index')),
                                         edge=edge.get('id'), internal=edge.get('function') == 'internal')
        if edge.get('function') != 'internal':
            edges.append(dict(id=edge.get('id'), lanes=[l.get('id') for l in edge.findall('lane')]))
    junctions = []
    controlled = {j['id'] for j in spec['junctions']}
    names = dict(NAMES, center='중앙 교차로') if names is None else names
    for j in root.findall('junction'):
        if j.get('id') in controlled:
            junctions.append(dict(id=j.get('id'), name=names.get(j.get('id'), j.get('id')),
                                  x=float(j.get('x')), y=float(j.get('y')), shape=points(j.get('shape'))))
    all_points = [p for e in edges for lid in e['lanes'] for p in lanes[lid]['shape']]
    xs, ys = zip(*all_points)
    road_junctions = [dict(id=j.get('id'), shape=points(j.get('shape'))) for j in root.findall('junction')
                      if len(points(j.get('shape'))) >= 3 and j.get('type') != 'internal']
    connections = [dict(incoming=f"{c.get('from')}_{c.get('fromLane')}",
                        outgoing=f"{c.get('to')}_{c.get('toLane')}", via=c.get('via'),
                        junction=c.get('tl'), index=int(c.get('linkIndex')) if c.get('linkIndex') is not None else None,
                        turn=c.get('dir')) for c in root.findall('connection')]
    return dict(edges=edges, lanes=lanes, junctions=junctions, road_junctions=road_junctions,
                connections=connections, bounds=[min(xs), min(ys), max(xs), max(ys)],
                coordinate_reference=dict(position='front_bumper_center', angle='clockwise_from_north', units='metres'),
                signals={j['id']: dict(phases=j['phases'], movements=j['movements']) for j in spec['junctions']})


class LiveEnvironment(SignalEnvironment):
    def __init__(self, *args, on_frame, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_frame, self.last_frame = on_frame, -1
        self.vehicle_frames = VehicleFrames()

    def observe(self):
        observation = super().observe()
        now = self.connection.simulation.getTime()
        if now != self.last_frame:
            self.last_frame = now
            self.on_frame(self, observation)
        return observation


class Demo:
    def __init__(self, output=ROOT / 'results/runs/web_pi_prototype', release=DEFAULT_RELEASE, *, realistic=False):
        self.output, self.release = Path(output).resolve(), Path(release).resolve()
        self.policy, self.identity = load_release(self.release)
        # PC validates the full original deployment contract as well as the portable one.
        validated_policy(self.release / 'model.json')
        self.suite_path = ROOT / 'network/transferable_v1/suite.json'
        self.suite, self.cases = load_suite(self.suite_path)
        self.scenarios = SUPPORTED_SCENARIOS.copy()
        self.networks = NETWORKS.copy()
        if realistic:
            from realistic_signal.build import load_scenarios
            from realistic_signal.spec import DEFAULT_SPEC
            spec, network, scenarios = load_scenarios(DEFAULT_SPEC)
            self.cases['realistic_v2'] = (spec, network, dict(id='realistic_v2', split='experimental'))
            self.networks['realistic_v2'] = 'Realistic V2 · 경주 3교차로 (실험)'
            self.scenarios.update({s['id']: PROFILES[s['profile']] for s in scenarios})
            self.suite = dict(self.suite, scenarios=[*self.suite['scenarios'],
                              *(dict(s, case='realistic_v2') for s in scenarios)])
        self.junction_names = dict(NAMES, center='중앙 교차로')
        self.maps = {case: map_data(self.cases[case][1], self.cases[case][0]) for case in self.networks}
        initial = 'realistic_v2' if realistic else 'gyeongju'
        self.map = self.maps[initial]
        summary_path = ROOT / 'results/runs/final_signal_wave10_20261007/final_summary.json'
        meta = read_json(self.release / 'release.json')
        expected = meta['evidence_sha256']['results\\runs\\final_signal_wave10_20261007\\final_summary.json']
        if digest(summary_path) != expected:
            raise ValueError('Verified comparison evidence changed')
        self.comparison = read_json(summary_path)
        self.lock = threading.RLock()
        self.stop_event, self.pause_event = threading.Event(), threading.Event()
        self.worker = None
        self.session_id = uuid.uuid4().hex
        self.state = self._ready_state(initial)

    def _system(self, network):
        return 'realistic-v2' if network == 'realistic_v2' else 'legacy-v1'

    def _ready_state(self, network):
        return dict(status='ready', time_s=0, horizon_s=900, vehicles=[], junctions=[],
                          queue=0, running=0, arrived=0, mean_queue=0, link='local',
                          link_message='PC 추론 준비', decision_ms=None, error=None, result=None, decisions={},
                          network=network, signal_system=self._system(network), run_id=None, session_id=self.session_id, revision=0,
                          applied_signal_system=None,
                          output=None, link_error=None, diagnostic_error=None, edge_health=None,
                          remote_roundtrip_ms=None, remote_inference_ms=None)

    def snapshot(self):
        with self.lock:
            return dict(copy.deepcopy(self.state), worker_active=self.worker is not None and self.worker.is_alive())

    def boot(self):
        return dict(map=self.map, identity=self.identity, scenarios=self.scenarios,
                    networks={case: dict(name=name, map=self.maps[case],
                                         signal_system=self._system(case),
                                         scenarios={s: label for s, label in self.scenarios.items() if s.startswith(case + '_')})
                              for case, name in self.networks.items()},
                    comparison=self.comparison, title='Smart Signal · 경주 3교차로')

    def update(self, **values):
        with self.lock:
            self.state.update(values)
            self.state['revision'] += 1

    def select_network(self, network):
        if not isinstance(network, str) or network not in self.networks:
            raise ValueError('Unknown demonstration network')
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError('Stop the current experiment before changing networks')
            revision = self.state['revision']
            self.state = self._ready_state(network)
            self.state['revision'] = revision + 1
            self.map = self.maps[network]
        return self.snapshot()

    def set_speed(self, speed):
        if type(speed) not in (int, float) or not 1 <= speed <= 60:
            raise ValueError('Speed must be 1..60')
        with self.lock:
            if self.state['status'] not in ('running', 'pausing', 'paused'):
                raise ValueError('Speed changes require a running or paused experiment')
            self.update(speed=speed)
        return self.snapshot()

    def check_edge(self, url):
        client = EdgeClient(url, self.identity)
        started = time.perf_counter()
        health = client.check()
        return dict(hostname=health['hostname'], system=health.get('system'), machine=health.get('machine'),
                    roundtrip_ms=(time.perf_counter() - started) * 1000,
                    identity_sha256=self.identity['identity_sha256'])

    def start(self, options):
        if not isinstance(options, dict):
            raise ValueError('Options object required')
        scenario_id = options.get('scenario', self.state['network'] + '_busy')
        if isinstance(scenario_id, str) and scenario_id.startswith('realistic_v2_') and options.get('mode', 'local') != 'local':
            raise ValueError('Realistic V2 uses experimental PC inference; the Legacy Pi protocol is incompatible')
        controller = options.get('controller', 'smart')
        mode = options.get('mode', 'local')
        if scenario_id not in self.scenarios or controller not in ('smart', 'fixed', 'webster', 'max_pressure') or mode not in ('local', 'edge'):
            raise ValueError('Unknown scenario, controller or inference mode')
        speed, horizon = options.get('speed', 10), options.get('horizon_s', 900)
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
            self.update(status='starting', error=None, result=None, time_s=0, horizon_s=horizon,
                              applied_signal_system=None,
                              controller=controller, scenario=scenario_id, speed=speed, vehicles=[], junctions=[],
                              network=next(s['case'] for s in self.suite['scenarios'] if s['id'] == scenario_id),
                              signal_system='realistic-v2' if scenario_id.startswith('realistic_v2_') else 'legacy-v1',
                              run_id=uuid.uuid4().hex, mode=mode, output=None, link_error=None, diagnostic_error=None, edge_health=None,
                              remote_roundtrip_ms=None, remote_inference_ms=None,
                              queue=0, running=0, arrived=0, mean_queue=0, decision_ms=None, decisions={},
                              link='connecting' if mode == 'edge' else 'local',
                              link_message='원격 연결 확인 중' if mode == 'edge' else 'PC 추론')
            self.worker = threading.Thread(target=self._run, args=(scenario_id, controller, mode, edge_url, speed, horizon), daemon=True)
            try:
                self.worker.start()
            except Exception as exc:
                self.update(status='failed', error=f'Worker start failed: {exc}')
                raise
        return self.snapshot()

    def command(self, action):
        with self.lock:
            active = self.worker is not None and self.worker.is_alive()
            if action == 'stop' and active and self.state['status'] in ('starting', 'running', 'pausing', 'paused'):
                self.stop_event.set()
                self.update(status='stopping')
            elif action == 'pause' and active and self.state['status'] == 'running':
                self.pause_event.set()
                self.update(status='pausing')
            elif action == 'resume' and active and self.state['status'] in ('paused', 'pausing'):
                self.pause_event.clear()
                self.update(status='running')
            else:
                raise ValueError('Command unavailable in the current state')
        return self.snapshot()

    def close(self):
        self.stop_event.set()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=10)

    def _frame(self, env, observation, speed):
        # The worker alone queries TraCI; request handlers only read locked snapshots.
        connection = env.connection
        vehicles = env.vehicle_frames.snapshot(connection)
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
                                  phase_name=clock.phases[clock.current]['id'],
                                  next_phase_name=clock.phases[clock.pending]['id'] if clock.pending is not None else None,
                                  movement_signals=[dict(m, state=state[m['index']]) for m in j['movements']],
                                  right_turn_mode=env.spec.get('right_turn_mode', 'legacy_protected'),
                                  queue=sum(env.observer.samples[lid]['queue'] for lid in incoming)))
        self.update(time_s=connection.simulation.getTime(), vehicles=vehicles, junctions=junctions,
                    applied_signal_system='realistic-v2' if env.spec.get('version') == 'realistic-v2' else 'legacy-v1',
                    queue=sum(v['speed'] < .1 for v in vehicles), running=len(vehicles),
                    arrived=len(env.metrics.arrived), mean_queue=(sum(env.metrics.queues) / len(env.metrics.queues)) if env.metrics.queues else 0)
        if env.metrics.collisions or env.metrics.teleports:
            raise RuntimeError('Collision or teleport observed; run stopped')
        # Pause is acknowledged at the next 1-second frame, with no extra SUMO advancement.
        while True:
            with self.lock:
                if self.stop_event.is_set():
                    raise StopRequested()
                if not self.pause_event.is_set():
                    self.update(status='running')
                    speed = self.state['speed']
                    break
                self.update(status='paused')
            self.stop_event.wait(.05)
        if self.stop_event.wait(1 / speed):
            raise StopRequested()

    def _run(self, scenario_id, controller, mode, edge_url, speed, horizon):
        env, stream, detail_stream, out, metadata = None, None, None, None, None
        final = dict(status='failed', error='Run did not finish')
        try:
            policy, identity = load_release(self.release)
            scenario = next(s for s in self.suite['scenarios'] if s['id'] == scenario_id)
            spec, network, _ = self.cases[scenario['case']]
            realistic = spec.get('version') == 'realistic-v2'
            if realistic:
                from realistic_signal.build import load_scenarios
                from realistic_signal.spec import DEFAULT_SPEC
                checked, checked_network, _ = load_scenarios(DEFAULT_SPEC)
                if checked != spec or checked_network != network:
                    raise ValueError('Realistic V2 inputs changed since server initialization')
                routes = network.parent / scenario['routes']
            else:
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
                            signal_system='realistic-v2' if realistic else 'legacy-v1',
                            model_compatibility=('experimental_transfer_unvalidated' if realistic else 'legacy_validated') if controller == 'smart' else 'not_used',
                            suite_sha256=digest(self.suite_path),
                            junction_phase_counts={j['id']: len(j['phases']) for j in spec['junctions']},
                            bridge_sha256={p.name: digest(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                            fallback_events=[])
            if realistic:
                metadata.update(spec_sha256=digest(DEFAULT_SPEC), spec_path=str(DEFAULT_SPEC),
                                source_sha256={p.relative_to(ROOT).as_posix(): digest(p)
                                               for p in (ROOT / 'realistic_signal').glob('*.py')})
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
            environment = LiveEnvironment
            if realistic:
                from .realistic import RealisticLiveEnvironment
                environment = RealisticLiveEnvironment
            env = environment(spec, network, routes, out, horizon, scenario['seed'],
                                  on_frame=lambda e, o: self._frame(e, o, speed))
            stream = (out / 'decisions.csv').open('w', encoding='utf-8', newline='')
            writer = csv.writer(stream)
            writer.writerow(['time_s', 'junction', 'action', 'inference', 'decision_ms'])
            detail_stream = (out / 'explanations.jsonl').open('w', encoding='utf-8')
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
                    actions, diagnostics = {}, {}
                    for jid, value in observation.items():
                        actions[jid] = policy.action(jid, value, env.clocks[jid])
                        diagnostics[jid] = capture(policy, value, actions[jid])
                else:
                    diagnostics = edge.last_diagnostics
                ms = (time.perf_counter() - started) * 1000
                now = env.connection.simulation.getTime()
                details = {jid: decision_snapshot(observation[jid], env.clocks[jid], action,
                           diagnostics.get(jid), now, 'edge' if edge else 'local', env.observer.samples, controller)
                           for jid, action in actions.items()}
                if realistic:
                    from .realistic import explain_constraints
                    for jid, detail in details.items():
                        explain_constraints(detail, env.clocks[jid])
                self.update(decision_ms=ms / len(actions), decisions=details,
                            remote_roundtrip_ms=edge.last_ms if edge else None,
                            remote_inference_ms=edge.last_inference_ms if edge else None,
                            diagnostic_error=edge.diagnostic_error if edge else None)
                detail_stream.write(json_bytes(dict(time_s=now, junctions=details)).decode('utf-8') + '\n')
                detail_stream.flush()
                for jid, action in actions.items():
                    writer.writerow([env.connection.simulation.getTime(), jid, action, 'edge' if edge else 'local', ms / len(actions)])
                stream.flush()
                observation, _, done = env.step(actions)
            self.update(status='finalizing')
            result = env.finish()
            if result['collision_vehicle_events'] or result['teleport_events']:
                raise RuntimeError('Unsafe run detected')
            metadata['status'] = 'completed'
            write_json(out / 'metrics.json', result)
            final = dict(status='completed', result=result, error=None)
        except StopRequested:
            if metadata is not None:
                metadata['status'] = 'stopped'
            try:
                if env is not None and env.connection is not None:
                    result = env.finish()
                    write_json(out / 'metrics.json', result)
                    self.update(result=result)
                final = dict(status='stopped', error=None)
            except Exception as exc:
                message = f'{type(exc).__name__}: {exc}'
                metadata.update(status='failed', error=message)
                final = dict(status='failed', error=message)
        except Exception as exc:
            message = f'{type(exc).__name__}: {exc}'
            if metadata is not None:
                metadata.update(status='failed', error=message)
            final = dict(status='failed', error=message)
        finally:
            self.update(status='finalizing')
            for resource in (env, stream, detail_stream):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception as exc:
                        message = f'Finalization failed: {type(exc).__name__}: {exc}'
                        final = dict(status='failed', error=message)
                        if metadata is not None:
                            metadata.update(status='failed', error=message)
            if metadata is not None:
                try:
                    write_json(out / 'manifest.json', metadata)
                except Exception as exc:
                    final = dict(status='failed', error=f'Manifest write failed: {type(exc).__name__}: {exc}')
            self.update(**final)
