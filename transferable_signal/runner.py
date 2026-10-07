"""Explicit user-run comparisons and result provenance."""
import csv
from collections import Counter
from pathlib import Path
import platform
import subprocess
import time
import xml.etree.ElementTree as ET

import numpy as np
import sumolib

from . import VERSION
from .spec import digest, write_json
from .suite import load_suite
from .controllers import Baseline
from .model import SmartSignal
from .environment import SignalEnvironment
from .deployment import validated_policy


CONTROLLERS = ('fixed', 'webster', 'max_pressure', 'smart')


def _smart_policy(model_path, smart_policy):
    if smart_policy == 'validated':
        return validated_policy(model_path)
    if model_path is None:
        raise ValueError('--model is required for --smart-policy raw')
    model_path = Path(model_path).resolve()
    return SmartSignal(model_path), model_path, dict(mode='raw', model_sha256=digest(model_path))


def _check_scenario(policy, scenario, spec, identity, suite):
    if identity['mode'] == 'validated' and (spec['limits'] != identity['limits']
            or ('limits' in suite and suite['limits'] != identity['limits'])):
        raise ValueError(f"Validated limits differ for scenario: {scenario['id']}")
    trained_on = policy.data['training'].get('training_network_sha256')
    if not isinstance(trained_on, list) or not trained_on or any(not isinstance(value, str) for value in trained_on):
        raise ValueError('Model lacks training-network provenance')
    if scenario['split'] == 'test' and spec['network_sha256'] in trained_on:
        raise ValueError('Final-test network occurred in model training')


def _check_options(controller, smart_policy, output):
    if controller not in CONTROLLERS:
        raise ValueError('Unknown controller')
    if smart_policy not in ('raw', 'validated'):
        raise ValueError('Unknown smart policy')
    if output.exists():
        raise FileExistsError(f'Output already exists: {output}')


def provenance(suite_path, suite, controller, model_path):
    package = Path(__file__).parent
    code_hashes = {p.name: digest(p) for p in sorted(package.glob('*.py'))}
    code_hashes['evaluation_support.py'] = digest(package.parent / 'evaluation_support.py')
    return dict(version=VERSION, suite_sha256=digest(suite_path), controller=controller,
                model_sha256=digest(model_path) if model_path else None,
                code_sha256=code_hashes, python=platform.python_version(),
                sumo=subprocess.check_output([sumolib.checkBinary('sumo'), '--version'], text=True).splitlines()[0],
                frozen_demand={s['id']: s['routes_sha256'] for s in suite['scenarios']})


def run(suite_path, scenario_id, controller, output, model_path=None, gui=False, *, smart_policy='raw'):
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    _check_options(controller, smart_policy, output)
    suite, cases = load_suite(suite_path)
    found = [s for s in suite['scenarios'] if s['id'] == scenario_id]
    if len(found) != 1:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    scenario = found[0]
    spec, network, _ = cases[scenario['case']]
    identity = None
    if controller == 'smart':
        policy, model_path, identity = _smart_policy(model_path, smart_policy)
        _check_scenario(policy, scenario, spec, identity, suite)
    else:
        if model_path is not None:
            raise ValueError("A model is used only by SmartSignal")
        policy = Baseline(controller, spec['limits'])
    output.mkdir(parents=True, exist_ok=False)
    metadata = provenance(suite_path, suite, controller, model_path)
    if identity is not None:
        metadata['smart_policy'] = identity
        if identity['mode'] == 'validated':
            metadata['adaptive_policy'] = {key: identity[key] for key in ('version', 'variant', 'config')}
    metadata.update(scenario=scenario, network_sha256=spec['network_sha256'], limits=spec['limits'], status='started')
    write_json(output / 'manifest.json', metadata)
    env = SignalEnvironment(spec, network, suite_path.parent / scenario['routes'], output,
                            scenario['duration_s'] + scenario['drain_s'], scenario['seed'], gui=gui)
    try:
        observation = env.reset()
        done = False
        decision_ms = []
        while not done:
            started = time.perf_counter()
            actions = {jid: policy.action(jid, value, env.clocks[jid]) for jid, value in observation.items()}
            decision_ms.append((time.perf_counter()-started)*1000/len(actions))
            observation, _, done = env.step(actions)
        result = env.finish()
        trips = [t for t in ET.parse(output / 'tripinfo.xml').getroot().findall('tripinfo') if float(t.get('arrival')) >= 0]
        result['p95_completed_waiting_time_s'] = float(np.percentile([float(t.get('waitingTime')) for t in trips], 95)) if trips else None
        result['mean_decision_ms_per_junction'] = float(np.mean(decision_ms))
        result['p95_decision_ms_per_junction'] = float(np.percentile(decision_ms,95))
        result.update(scenario=scenario_id, controller=controller, status='completed',
                      observed_collision_or_teleport=bool(result['collision_vehicle_events'] or result['teleport_events']))
        metadata['status'] = 'completed'
        write_json(output / 'metrics.json', result)
        return result
    except Exception as exc:
        metadata.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        env.close()
        write_json(output / 'manifest.json', metadata)


def compare(suite_path, split, output, model_path=None, controllers=None, *, smart_policy='raw'):
    output = Path(output).resolve()
    _check_options('smart', smart_policy, output)
    suite, cases = load_suite(suite_path)
    if split not in ('train', 'validation', 'test'):
        raise ValueError("Unknown split")
    if controllers is None:
        controllers = list(CONTROLLERS if smart_policy == 'validated' or model_path else CONTROLLERS[:-1])
    if not controllers or len(set(controllers)) != len(controllers) or set(controllers) - set(CONTROLLERS):
        raise ValueError("Unknown or duplicate controller")
    scenarios = [s for s in suite['scenarios'] if s['split'] == split]
    if not scenarios:
        raise ValueError("No scenarios in selected split")
    scenario_counts = Counter(s['id'] for s in suite['scenarios'])
    if any(scenario_counts[s['id']] != 1 for s in scenarios):
        raise ValueError('Duplicate scenario identifier')
    if 'smart' in controllers:
        policy, model_path, identity = _smart_policy(model_path, smart_policy)
        for scenario in scenarios:
            _check_scenario(policy, scenario, cases[scenario['case']][0], identity, suite)
    output.mkdir(parents=True, exist_ok=False)
    writer = None
    with (output / 'comparison.csv').open('w', newline='', encoding='utf-8-sig') as stream:
        for scenario in scenarios:
            for controller in controllers:
                result = run(suite_path, scenario['id'], controller, output / scenario['id'] / controller,
                             model_path=model_path if controller == 'smart' else None, smart_policy=smart_policy)
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(result))
                    writer.writeheader()
                writer.writerow(result)
                stream.flush()
