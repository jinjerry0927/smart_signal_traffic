"""Explicit user execution and offline, second-by-second trace replay."""
import csv
from pathlib import Path
import time
import xml.etree.ElementTree as ET

from transferable_signal.controllers import Baseline
from transferable_signal.deployment import validated_policy
from transferable_signal.spec import digest, read_json, write_json
from signal_demo.release import load_release
from .build import load_scenarios
from .control import MovementClock
from .spec import DEFAULT_SPEC, ROOT


def check(spec_path=DEFAULT_SPEC):
    spec, network, scenarios = load_scenarios(spec_path)
    # Retain both original hash checks; this certifies the original model only.
    load_release()
    validated_policy()
    return dict(signal_system=spec['version'], junctions={j['id']: len(j['phases']) for j in spec['junctions']},
                movements=sum(len(j['movements']) for j in spec['junctions']), scenarios=[s['id'] for s in scenarios],
                frozen_release='unchanged', model_compatibility=spec['model_compatibility'], simulation_run=False)


def run(output, profile='busy', controller='fixed', horizon=900, gui=False, spec_path=DEFAULT_SPEC):
    from .environment import RealisticEnvironment
    if controller not in ('smart', 'fixed', 'webster', 'max_pressure'):
        raise ValueError('Unknown controller')
    if type(horizon) is not int or horizon < 5 or horizon % 5:
        raise ValueError('Horizon must be a positive multiple of 5')
    spec_path, output = Path(spec_path).resolve(), Path(output).resolve()
    spec, network, scenarios = load_scenarios(spec_path)
    scenario = next(s for s in scenarios if s['profile'] == profile)
    policy, identity = load_release()
    validated_policy()
    if controller != 'smart':
        policy = Baseline(controller, spec['limits'])
    output.mkdir(parents=True, exist_ok=False)
    metadata = dict(signal_system='realistic-v2', status='started', scenario=scenario, controller=controller,
                    horizon_s=horizon, spec_path=str(spec_path), spec_sha256=digest(spec_path),
                    network_sha256=digest(network), routes_sha256=scenario['routes_sha256'], identity=identity,
                    model_compatibility='experimental_transfer_unvalidated' if controller == 'smart' else 'not_used',
                    source_sha256={p.relative_to(ROOT).as_posix(): digest(p) for p in Path(__file__).parent.glob('*.py')})
    write_json(output / 'manifest.json', metadata)
    env = RealisticEnvironment(spec, network, spec_path.parent / scenario['routes'], output, horizon, scenario['seed'], gui=gui)
    try:
        observation = env.reset()
        done = False
        with (output / 'decisions.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(['time_s', 'junction', 'action', 'decision_ms'])
            while not done:
                started = time.perf_counter()
                actions = {jid: policy.action(jid, value, env.clocks[jid]) for jid, value in observation.items()}
                ms = (time.perf_counter() - started) * 1000
                for jid, action in actions.items():
                    writer.writerow([env.connection.simulation.getTime(), jid, action, ms / len(actions)])
                observation, _, done = env.step(actions)
        result = env.finish()
        if result['collision_vehicle_events'] or result['teleport_events']:
            raise RuntimeError('Unsafe run')
        write_json(output / 'metrics.json', result)
        metadata['status'] = 'completed'
        return result
    except Exception as exc:
        metadata.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        try:
            env.close()
        except Exception as exc:
            metadata.update(status='failed', error=f'Close failed: {exc}')
            raise
        finally:
            write_json(output / 'manifest.json', metadata)


def audit(output, spec_path=DEFAULT_SPEC):
    """No SUMO. Reconstruct every recorded applied state using real safety rules."""
    output, spec_path = Path(output), Path(spec_path)
    spec, network, scenarios = load_scenarios(spec_path)
    manifest = read_json(output / 'manifest.json')
    if manifest.get('status') != 'completed':
        raise ValueError('Run is not completed; retain partial logs for diagnosis')
    if (manifest.get('signal_system') != 'realistic-v2' or manifest.get('network_sha256') != digest(network)
            or manifest.get('spec_sha256') != digest(spec_path)
            or manifest.get('routes_sha256') not in {s['routes_sha256'] for s in scenarios}):
        raise ValueError('Run provenance differs from Realistic V2')
    clocks = {j['id']: MovementClock(j, spec['limits']) for j in spec['junctions']}
    times = dict.fromkeys(clocks, 0)
    rows = 0
    with (output / 'signals.csv').open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            jid = row['junction']
            clock = clocks[jid]
            if float(row['time_s']) != times[jid]:
                raise ValueError('Missing, reordered or duplicate trace second')
            action = row['requested_action']
            if bool(action) != (times[jid] % spec['limits']['decision_s'] == 0):
                raise ValueError('Missing action or off-grid request')
            if action:
                clock.request(int(action))
            expected = (clock.current, clock.stage, clock.state(), str(clock.pending) if clock.pending is not None else '',
                        clock.remaining, clock.elapsed, clock.forced_changes)
            actual = (int(row['phase']), row['stage'], row['state'], row['pending'], int(row['remaining_s']),
                      int(row['green_elapsed_s']), int(row['forced_changes']))
            if actual != expected:
                raise ValueError(f'Signal replay mismatch: {jid} at {times[jid]}')
            clock.tick()
            times[jid] += 1
            rows += 1
    if not rows or len(set(times.values())) != 1:
        raise ValueError('Incomplete junction traces')
    if manifest['status'] == 'completed' and next(iter(times.values())) != manifest['horizon_s']:
        raise ValueError('Completed run has a truncated trace')
    metrics = read_json(output / 'metrics.json')
    if metrics['collision_vehicle_events'] or metrics['teleport_events']:
        raise ValueError('Collision or teleport in run metrics')
    fields = ('scheduled_vehicles', 'departed_vehicles', 'arrived_vehicles', 'not_departed_vehicles',
              'departed_not_arrived_vehicles', 'running_vehicles_end', 'pending_vehicles_end', 'sample_seconds')
    if any(type(metrics.get(k)) is not int or metrics[k] < 0 for k in fields):
        raise ValueError('Missing or invalid vehicle accounting')
    scenario = next(s for s in scenarios if s['routes_sha256'] == manifest['routes_sha256'])
    if (metrics['scheduled_vehicles'] != scenario['scheduled']
            or metrics['departed_vehicles'] + metrics['not_departed_vehicles'] != metrics['scheduled_vehicles']
            or metrics['arrived_vehicles'] + metrics['departed_not_arrived_vehicles'] != metrics['departed_vehicles']
            or metrics['running_vehicles_end'] != metrics['departed_not_arrived_vehicles']
            or metrics['sample_seconds'] != next(iter(times.values()))):
        raise ValueError('Vehicle accounting or sampled duration differs')
    trips = [t for t in ET.parse(output / 'tripinfo.xml').getroot().findall('tripinfo') if float(t.get('arrival')) >= 0]
    if len({t.get('id') for t in trips}) != len(trips) or len(trips) != metrics['arrived_vehicles']:
        raise ValueError('Completed tripinfo vehicles differ from arrival accounting')
    return dict(status='pass', rows=rows, seconds_per_junction=times, simulation_run=False,
                scope='recorded controller states and SUMO metrics; not field or pedestrian safety')
