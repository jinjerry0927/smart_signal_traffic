"""Run one bounded 24-run comparison on the known Gyeongju corridor/new demand.

The reviewed V2 model/policy is frozen. This evaluates new demand on an existing
three-junction map; it is not an unseen-geometry or universal-deployment claim.
"""
import argparse
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import run_adaptive_holdout as holdout
from tools import run_adaptive_wave as common
from tools import run_adaptive_wave2 as wave2
from transferable_signal import runner
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json
from transferable_signal.suite import load_suite

DEFAULT_SUITE = ROOT / 'network/adaptive_gyeongju_wave3_20261004/suite.json'
DEFAULT_OUTPUT = ROOT / 'results/runs/adaptive_gyeongju_wave3_20261004'
PREPARER = ROOT / 'tools/prepare_adaptive_gyeongju.py'
TLS_IDS = {'gj_yonggang', 'gj_geunhwa', 'gj_seungsam'}
PROFILES = ('quiet', 'busy', 'directional')
CONTROLLERS = ('fixed', 'webster', 'max_pressure', 'smart')
HELPERS = ('run_adaptive_holdout.py', 'run_adaptive_wave.py', 'run_adaptive_wave2.py')


def _preflight(suite_path=DEFAULT_SUITE, selection_wave=holdout.DEFAULT_SELECTION):
    freeze = holdout.freeze_selection(selection_wave)
    if freeze['model']['seed'] != 43 or freeze['variant'] != 'load_gate_pressure_wait35':
        raise ValueError('The reviewed Gyeongju model/policy changed')
    source_path = ROOT / 'network/transferable_v1/suite.json'
    source_suite, source_cases = load_suite(source_path)
    source_spec, source_network, source_case = source_cases['gyeongju']
    suite_path = Path(suite_path).resolve()
    suite, cases = load_suite(suite_path)
    if (suite.get('preparation_status') != 'completed' or suite.get('preparation_only') is not True or
            suite.get('known_network_new_demand') is not True or suite.get('selection_freeze') != freeze):
        raise ValueError('Gyeongju preparation or frozen policy/model differs')
    if suite.get('preparation_code_sha256') != digest(PREPARER):
        raise ValueError('Gyeongju preparation code changed')
    if suite.get('source_suite_sha256') != digest(source_path) or suite.get('source_network_sha256') != digest(source_network):
        raise ValueError('Original Gyeongju source provenance changed')
    if set(cases) != {'gyeongju'} or len(suite['cases']) != 1 or len(suite['scenarios']) != 6:
        raise ValueError('This wave requires one corridor and exactly six scenarios')
    spec, network, case = cases['gyeongju']
    if (source_case['split'] != 'test' or case['split'] != 'test' or spec != source_spec or
            case['spec_sha256'] != source_case['spec_sha256'] or network.read_bytes() != source_network.read_bytes()):
        raise ValueError('Prepared Gyeongju network/spec differs from its reviewed original')
    if (len(spec['junctions']) != 3 or {junction['id'] for junction in spec['junctions']} != TLS_IDS or
            spec['limits'] != DEFAULT_LIMITS or suite.get('limits') != DEFAULT_LIMITS):
        raise ValueError('Gyeongju junction IDs or common safety limits changed')
    seen_seeds, seen_routes, exclusions = set(), set(), {}
    for path in (source_path, ROOT / 'network/transferable_reliability/suite.json',
                 ROOT / 'network/transferable_holdout_v2_20261004_retry01/suite.json'):
        previous, _ = load_suite(path)
        seen_seeds.update(scenario['seed'] for scenario in previous['scenarios'])
        seen_routes.update(scenario['routes_sha256'] for scenario in previous['scenarios'])
        exclusions[str(path.resolve())] = digest(path)
    if suite.get('excluded_demand_seeds') != sorted(seen_seeds) or suite.get('excluded_routes_sha256') != sorted(seen_routes):
        raise ValueError('Prepared Gyeongju demand-exclusion lists differ')
    ids, fresh_routes, fresh_seeds = set(), set(), set()
    for scenario in suite['scenarios']:
        profile, sample = scenario['profile'], scenario['sample']
        if profile not in PROFILES or not isinstance(sample, int) or isinstance(sample, bool) or sample not in (0, 1):
            raise ValueError('Unknown Gyeongju profile/sample')
        expected_seed = 76000 + PROFILES.index(profile) * 10 + sample
        expected_id = f'gyeongju_{profile}_s{sample:02d}'
        if (scenario['id'] != expected_id or scenario['id'] in ids or scenario['seed'] != expected_seed or
                not isinstance(scenario['seed'], int) or isinstance(scenario['seed'], bool) or
                scenario['split'] != 'test' or scenario['case'] != 'gyeongju' or
                scenario['duration_s'] != 600 or scenario['drain_s'] != 300):
            raise ValueError('Gyeongju scenario identity, seed, split, or horizon differs')
        if (scenario['seed'] in seen_seeds or scenario['seed'] in fresh_seeds or
                scenario['routes_sha256'] in seen_routes or scenario['routes_sha256'] in fresh_routes):
            raise ValueError('Gyeongju demand repeats a previous or current schedule')
        route_path = (suite_path.parent / scenario['routes']).resolve()
        if not route_path.is_relative_to(suite_path.parent):
            raise ValueError('Gyeongju route path escaped its prepared suite')
        trips = ET.parse(route_path).getroot().findall('vehicle')
        scheduled = scenario['scheduled']
        if (not isinstance(scheduled, int) or isinstance(scheduled, bool) or scheduled <= 0 or len(trips) != scheduled or
                len({trip.get('id') for trip in trips}) != len(trips) or
                any(not 0 <= float(trip.get('depart')) < 600 for trip in trips)):
            raise ValueError('Gyeongju scheduled count or departure schedule is malformed')
        ids.add(scenario['id'])
        fresh_seeds.add(scenario['seed'])
        fresh_routes.add(scenario['routes_sha256'])
    if ids != {f'gyeongju_{profile}_s{sample:02d}' for profile in PROFILES for sample in (0, 1)}:
        raise ValueError('Gyeongju paired conditions are incomplete')
    provenance = dict(source_suite_sha256=digest(source_path), network_sha256=digest(network),
                      spec_sha256=case['spec_sha256'], exclusions=exclusions,
                      fresh_demand={scenario['id']: dict(seed=scenario['seed'], sha256=scenario['routes_sha256'],
                                                       scheduled=scenario['scheduled']) for scenario in suite['scenarios']})
    return suite, cases, freeze, provenance


def run_wave(output=DEFAULT_OUTPUT, suite_path=DEFAULT_SUITE, selection_wave=holdout.DEFAULT_SELECTION,
             resume=False, max_wall_seconds=2700, policy_factory=None):
    from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2
    if not isinstance(max_wall_seconds, (int, float)) or isinstance(max_wall_seconds, bool) or not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise ValueError('max_wall_seconds must be finite and positive')
    started = time.monotonic()
    policy_factory = AdaptiveSmartSignalV2 if policy_factory is None else policy_factory
    output, suite_path = Path(output).resolve(), Path(suite_path).resolve()
    suite, cases, freeze, source = _preflight(suite_path, selection_wave)
    fingerprint = dict(suite_sha256=digest(suite_path), selection_freeze=freeze, prepared_source=source,
                       driver_sha256=digest(Path(__file__)), preparation_sha256=digest(PREPARER),
                       helper_sha256={name: digest(ROOT / 'tools' / name) for name in HELPERS},
                       criteria=dict(queue_improvement_fixed=.05, arrivals_nondecrease=True, worst_profile_change=.10))
    manifest_path = output / 'manifest.json'
    if output.exists():
        if not resume:
            raise FileExistsError('Gyeongju output exists; use --resume with exactly unchanged inputs/code')
        metadata = read_json(manifest_path)
        if metadata.get('fingerprint') != fingerprint:
            raise ValueError('Gyeongju resume code, source, or frozen model/policy changed')
        if metadata.get('status') in ('safety_hold', 'failed'):
            raise ValueError('Inspect failed Gyeongju wave before a separately authorized retry')
    else:
        if resume:
            raise FileNotFoundError('No Gyeongju wave to resume')
        output.mkdir(parents=True)
        metadata = dict(status='started', fingerprint=fingerprint, records=[], actual_new_runs=0,
                        started_run_attempts=0, expected_runs=24, controllers=list(CONTROLLERS),
                        evaluation_kind='known Gyeongju geometry / fresh demand; frozen validation-selected V2')
    if metadata.get('controllers') != list(CONTROLLERS) or metadata.get('expected_runs') != 24:
        raise ValueError('Gyeongju saved comparison cardinality/controller plan changed')
    records = metadata['records']
    if metadata.get('actual_new_runs') != len(records):
        metadata['counter_recovery'] = dict(previous=metadata.get('actual_new_runs'), committed_records=len(records))
        metadata['actual_new_runs'] = len(records)
    scenarios = {scenario['id']: scenario for scenario in suite['scenarios']}
    completed = {}
    try:
        for record in records:
            row = record['row']
            scenario, controller = scenarios[row['scenario']], row['controller']
            if controller not in CONTROLLERS:
                raise ValueError('Unknown saved Gyeongju controller')
            directory = (output / record['directory']).resolve()
            if not directory.is_relative_to(output):
                raise ValueError('Saved Gyeongju result path escaped output')
            metrics, warnings = holdout._audit(directory, scenario, controller, cases[scenario['case']][0], fingerprint)
            if any(not wave2._equal(row.get(key), value) for key, value in dict(metrics, **holdout._labels(scenario, controller, freeze)).items()):
                raise ValueError('Saved Gyeongju metrics/labels differ from raw records')
            if record.get('warning_details') != warnings or row.get('emergency_braking_warnings') != len(warnings):
                raise ValueError('Saved Gyeongju warning records differ from raw logs')
            key = (scenario['id'], controller)
            if key in completed:
                raise ValueError('Duplicate saved Gyeongju result')
            completed[key] = record
        for scenario in suite['scenarios']:
            for controller in CONTROLLERS:
                key = (scenario['id'], controller)
                if key in completed:
                    continue
                if time.monotonic() - started >= max_wall_seconds:
                    raise common.WallTimeCheckpoint('Gyeongju wall-time checkpoint after completed simulations')
                relative = Path('runs') / scenario['id'] / holdout._labels(scenario, controller, freeze)['model_label']
                directory = output / relative
                retry = 0
                while directory.exists() and not (directory / 'metrics.json').exists():
                    retry += 1
                    directory = output / relative.with_name(relative.name + f'_retry{retry:02d}')
                metadata.update(status='running', active_run=dict(scenario=scenario['id'], controller=controller,
                                                                directory=str(directory.relative_to(output))))
                common.atomic_json(manifest_path, metadata)
                if not directory.exists():
                    metadata['started_run_attempts'] += 1
                    common.atomic_json(manifest_path, metadata)
                    if controller == 'smart':
                        original_provenance = runner.provenance
                        policy = dict(version='adaptive-v2', variant=freeze['variant'], config=freeze['policy_config'])
                        def annotated_provenance(*args, **kwargs):
                            return dict(original_provenance(*args, **kwargs), adaptive_policy=policy)
                        with patch.object(runner, 'SmartSignal', lambda path: policy_factory(path, freeze['variant'])), \
                                patch.object(runner, 'provenance', side_effect=annotated_provenance):
                            runner.run(suite_path, scenario['id'], controller, directory, model_path=freeze['model']['path'])
                    else:
                        runner.run(suite_path, scenario['id'], controller, directory)
                metrics, warnings = holdout._audit(directory, scenario, controller, cases[scenario['case']][0], fingerprint)
                row = dict(metrics, **holdout._labels(scenario, controller, freeze), emergency_braking_warnings=len(warnings))
                record = dict(directory=str(directory.relative_to(output)), row=row, warning_details=warnings)
                records.append(record)
                completed[key] = record
                metadata['actual_new_runs'] = len(records)
                metadata.pop('active_run', None)
                common._write_comparison(output, records)
                common.atomic_json(manifest_path, metadata)
                print(f"Gyeongju {len(records)}/24: {scenario['id']} / {row['model_label']} | queue={metrics['mean_queue_vehicles']:.3f}, arrived={metrics['arrived_vehicles']}/{metrics['scheduled_vehicles']}", flush=True)
        baseline = {(record['row']['scenario'], record['row']['controller']): record['row'] for record in records
                    if record['row']['controller'] in common.BASELINES}
        ai_rows = [record['row'] for record in records if record['row']['controller'] == 'smart']
        summary = common._summary(ai_rows, baseline)
        scenario_results = {row['scenario']: common._summary([row], baseline) for row in ai_rows}
        summary['scenario_summaries'] = scenario_results
        criteria = fingerprint['criteria']
        target_met = (summary['queue_change']['fixed'] <= -criteria['queue_improvement_fixed'] and
                      summary['overall']['arrived_vehicles'] >= summary['baselines']['fixed']['arrived_vehicles'] and
                      summary['worst_profile_change'] <= criteria['worst_profile_change'])
        metadata.update(status='completed', summary=summary, target_met=target_met,
                        emergency_braking_warnings=sum(record['row']['emergency_braking_warnings'] for record in records),
                        next_action='Review the completed known-corridor/new-demand wave; preserve frozen model and policy; no retraining or test-based reselection')
        common.atomic_json(manifest_path, metadata)
        common.atomic_json(output / 'summary.json', {name: metadata[name] for name in
                           ('status', 'actual_new_runs', 'expected_runs', 'summary', 'target_met', 'emergency_braking_warnings', 'next_action')})
        return metadata
    except common.WallTimeCheckpoint as exc:
        metadata.update(status='checkpoint_walltime', error=str(exc), next_action='Review interval; --resume continues unchanged model, policy, and demand')
        common.atomic_json(manifest_path, metadata)
        return metadata
    except BaseException as exc:
        metadata.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'safety_hold',
                        error=f'{type(exc).__name__}: {exc}')
        common.atomic_json(manifest_path, metadata)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--selection-wave', type=Path, default=holdout.DEFAULT_SELECTION)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=float, default=2700)
    args = parser.parse_args()
    result = run_wave(args.out, args.suite, args.selection_wave, args.resume, args.max_wall_seconds)
    print(f"Gyeongju wave {result['status']}: {result['actual_new_runs']}/24 verified simulations", flush=True)


if __name__ == '__main__':
    main()
