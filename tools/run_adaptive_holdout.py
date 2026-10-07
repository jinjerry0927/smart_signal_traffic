"""Freeze a validation-selected policy and sequentially test 12 unseen cases.

The test results never choose a different model or alter the policy. Existing
training, validation, and first/second-wave artifacts remain read only.
"""
import argparse
import csv
import math
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import run_adaptive_wave as common
from tools import run_adaptive_wave2 as wave2
from transferable_signal import runner
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json
from transferable_signal.suite import load_suite

DEFAULT_SELECTION = ROOT / 'results/runs/adaptive_wave2_20261004'
DEFAULT_SUITE = ROOT / 'network/transferable_holdout_v2_20261004/suite.json'


def same_structure(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(same_structure(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(same_structure(a, b) for a, b in zip(actual, expected))
    return wave2._equal(actual, expected)


def freeze_selection(selection_wave=DEFAULT_SELECTION):
    """Audit successful validation and choose solely by validation mean queue."""
    from transferable_signal.adaptive_v2 import VARIANTS_V2
    selection_wave = Path(selection_wave).resolve()
    metadata = read_json(selection_wave / 'manifest.json')
    if metadata.get('status') != 'completed' or metadata.get('target_met') is not True:
        raise ValueError('Independent testing requires a completed second wave with target_met=true')
    saved = metadata['fingerprint']
    model_paths = [item['path'] for item in saved['models']]
    suite, cases, models, baselines, known, fingerprint, originals, first_wave = wave2._preflight(
        common.DEFAULT_SUITE, common.DEFAULT_CACHE, wave2.DEFAULT_WAVE1, model_paths)
    variants = list(saved['variants'])
    if any(variant not in VARIANTS_V2 for variant in variants):
        raise ValueError('Validation-selected variant is unknown')
    fingerprint.update(variants={variant: dict(VARIANTS_V2[variant]) for variant in variants}, criteria=dict(wave2.CRITERIA),
                       adaptive_v2_sha256=digest(ROOT / 'transferable_signal/adaptive_v2.py'),
                       wave2_driver_sha256=digest(ROOT / 'tools/run_adaptive_wave2.py'))
    if fingerprint != saved:
        raise ValueError('Validation code, policy, models, or source results changed')
    records = metadata['records']
    expected = len(variants) * 6 * len(models) + len(suite['scenarios']) * len(models) - 6 * len(models)
    if metadata.get('actual_new_runs') != len(records) or len(records) != expected:
        raise ValueError('Successful validation wave has inconsistent run counts')
    scenarios = {scenario['id']: scenario for scenario in suite['scenarios']}
    indexed = {}
    for record in records:
        key = wave2._record_audit(record, selection_wave, scenarios, models, variants, cases, fingerprint, known)
        if key in indexed:
            raise ValueError('Duplicate validation result')
        indexed[key] = record['row']
    with (selection_wave / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    csv_indexed = {(row['scenario'], row['variant'], int(row['model_seed'])): row for row in csv_rows}
    if len(csv_rows) != len(records) or set(csv_indexed) != set(indexed):
        raise ValueError('Validation comparison CSV contains missing/duplicate rows')
    if any(not wave2._equal(csv_indexed[key].get(name), value) for key, row in indexed.items() for name, value in row.items()):
        raise ValueError('Validation CSV differs from audited raw results')
    pilots = [scenario for scenario in suite['scenarios'] if scenario['sample'] == 0]
    pilot_summaries = {}
    for variant in variants:
        rows, per_seed = [], []
        for model in models:
            selected = [indexed[(scenario['id'], variant, model['seed'])] for scenario in pilots]
            rows.extend(selected)
            per_seed.append(dict(model_seed=model['seed'], **wave2._summary(selected, baselines, originals, first_wave)))
        pilot_summaries[variant] = dict(wave2._summary(rows, baselines, originals, first_wave), seed_summaries=per_seed)
    selected_variant = wave2._select_candidate(pilot_summaries)
    if selected_variant is None or selected_variant != metadata.get('selected_variant') or not same_structure(metadata['pilot_summaries'], pilot_summaries):
        raise ValueError('Validation pilot selection or summaries differ from raw records')
    full = []
    for model in models:
        rows = [indexed[(scenario['id'], selected_variant, model['seed'])] for scenario in suite['scenarios']]
        summary = dict(model_seed=model['seed'], **wave2._summary(rows, baselines, originals, first_wave))
        summary['gate_failure_reasons'] = wave2._seed_gate_reasons(summary)
        summary['gate_met'] = not summary['gate_failure_reasons']
        full.append(summary)
    if not all(item['gate_met'] for item in full) or not same_structure(metadata.get('full_summaries'), full):
        raise ValueError('Validation full comparison does not meet its frozen gates')
    chosen = min(full, key=lambda item: (item['overall']['mean_queue_vehicles'], -item['overall']['arrived_vehicles'],
                                        item['worst_profile_change'], item['model_seed']))
    model = next(item for item in models if item['seed'] == chosen['model_seed'])
    return dict(selection_wave=str(selection_wave), selection_manifest_sha256=digest(selection_wave / 'manifest.json'),
                selection_comparison_sha256=digest(selection_wave / 'comparison.csv'),
                selection_driver_sha256=fingerprint['wave2_driver_sha256'],
                variant=selected_variant, policy_config=fingerprint['variants'][selected_variant],
                adaptive_v2_sha256=fingerprint['adaptive_v2_sha256'],
                adaptive_v1_sha256=fingerprint['first_wave']['old_policy_sha256'],
                model=model, source_models=models, core_sha256=fingerprint['core_sha256'],
                python=fingerprint['python'], sumo=fingerprint['sumo'],
                selection_rule='Lowest held-out validation mean queue; ties use arrivals, worst profile, then seed; test results never reselect',
                validation_mean_queue_vehicles=chosen['overall']['mean_queue_vehicles'],
                validation_arrived_vehicles=chosen['overall']['arrived_vehicles'])


def _preflight(suite_path, selection_wave):
    from tools.build_adaptive_holdout import excluded_hashes, GEOMETRIES
    freeze = freeze_selection(selection_wave)
    suite_path = Path(suite_path).resolve()
    suite, cases = load_suite(suite_path)
    if suite.get('holdout_status') != 'completed' or suite.get('selection_freeze') != freeze:
        raise ValueError('Holdout preparation or pre-test policy/model freeze differs')
    if len(suite['scenarios']) != 12 or set(cases) != {case['id'] for case in GEOMETRIES}:
        raise ValueError('Independent testing requires two new networks and twelve scenarios')
    if suite.get('geometry') != list(GEOMETRIES) or suite.get('limits') != DEFAULT_LIMITS:
        raise ValueError('Independent geometry plan or common control limits changed')
    if suite.get('preparation_code_sha256') != digest(ROOT / 'tools/build_adaptive_holdout.py'):
        raise ValueError('Holdout preparation code changed after generation')
    if len({scenario['id'] for scenario in suite['scenarios']}) != 12:
        raise ValueError('Duplicate independent scenario identifier')
    networks, routes, seeds = excluded_hashes([model['path'] for model in freeze['source_models']])
    if suite.get('exclusion') != dict(network_sha256=sorted(networks), routes_sha256=sorted(routes), demand_seeds=sorted(seeds)):
        raise ValueError('Holdout exclusion provenance differs')
    net_seen, route_seen, seed_seen = set(), set(), set()
    for spec, _, case in cases.values():
        network_hash = spec['network_sha256']
        if case['split'] != 'test' or network_hash in networks or network_hash in net_seen:
            raise ValueError('Holdout network occurred in training/previous evaluation or is duplicated')
        net_seen.add(network_hash)
        if spec['limits'] != DEFAULT_LIMITS:
            raise ValueError('A holdout controller has different signal safety limits')
    for scenario in suite['scenarios']:
        if (scenario['split'] != 'test' or scenario['routes_sha256'] in routes or scenario['routes_sha256'] in route_seen or
                scenario['seed'] in seeds or scenario['seed'] in seed_seen):
            raise ValueError('Holdout demand occurred in a previous suite or is duplicated')
        route_seen.add(scenario['routes_sha256'])
        seed_seen.add(scenario['seed'])
        trips = ET.parse(suite_path.parent / scenario['routes']).getroot().findall('vehicle')
        if len(trips) != scenario['scheduled'] or scenario['duration_s'] != 600 or scenario['drain_s'] != 300:
            raise ValueError('Independent scheduled count or common evaluation horizon changed')
        if len({trip.get('id') for trip in trips}) != len(trips) or any(not 0 <= float(trip.get('depart')) < 600 for trip in trips):
            raise ValueError('Independent departure schedule is malformed')
    for case in cases:
        for profile in ('quiet', 'busy', 'directional'):
            group = [scenario for scenario in suite['scenarios'] if scenario['case'] == case and scenario['profile'] == profile]
            if len(group) != 2 or {scenario['sample'] for scenario in group} != {0, 1}:
                raise ValueError('Holdout case/profile samples are incomplete')
    return suite, cases, freeze


def _labels(scenario, controller, freeze):
    return dict(model_label=controller if controller != 'smart' else f"smart_v2_{freeze['variant']}_seed{freeze['model']['seed']}",
                model_seed=freeze['model']['seed'] if controller == 'smart' else None,
                variant=freeze['variant'] if controller == 'smart' else None,
                case=scenario['case'], profile=scenario['profile'], sample=scenario['sample'])


def _audit(directory, scenario, controller, spec, fingerprint):
    freeze = fingerprint['selection_freeze']
    hashes = dict(freeze['core_sha256'], **{'adaptive.py': freeze['adaptive_v1_sha256'], 'adaptive_v2.py': freeze['adaptive_v2_sha256']})
    policy = dict(version='adaptive-v2', variant=freeze['variant'], config=freeze['policy_config']) if controller == 'smart' else None
    return common.audit_run(directory, scenario, spec, fingerprint['suite_sha256'], controller,
                            freeze['model']['sha256'] if controller == 'smart' else None, hashes,
                            {name: freeze[name] for name in ('sumo', 'python')}, policy)


def run_holdout(output, suite_path=DEFAULT_SUITE, selection_wave=DEFAULT_SELECTION, resume=False,
                max_wall_seconds=2700, policy_factory=None):
    from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2
    if not isinstance(max_wall_seconds, (int, float)) or isinstance(max_wall_seconds, bool) or not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise ValueError('max_wall_seconds must be finite and positive')
    started = time.monotonic()
    policy_factory = AdaptiveSmartSignalV2 if policy_factory is None else policy_factory
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    suite, cases, freeze = _preflight(suite_path, selection_wave)
    fingerprint = dict(suite_sha256=digest(suite_path), selection_freeze=freeze,
                       holdout_driver_sha256=digest(Path(__file__)),
                       holdout_builder_sha256=digest(ROOT / 'tools/build_adaptive_holdout.py'),
                       audit_helper_sha256=digest(ROOT / 'tools/run_adaptive_wave.py'))
    manifest_path = output / 'manifest.json'
    if output.exists():
        if not resume:
            raise FileExistsError('Holdout output exists; use --resume with unchanged inputs and policy')
        metadata = read_json(manifest_path)
        if metadata.get('fingerprint') != fingerprint:
            raise ValueError('Holdout resume code or frozen policy/model changed')
        if metadata.get('status') in ('safety_hold', 'failed'):
            raise ValueError('Inspect failed holdout before any separate re-test')
    else:
        if resume:
            raise FileNotFoundError('No holdout output to resume')
        output.mkdir(parents=True)
        metadata = dict(status='started', fingerprint=fingerprint, records=[], actual_new_runs=0,
                        started_run_attempts=0, expected_runs=len(suite['scenarios']) * 4,
                        controllers=list(common.BASELINES) + ['smart'])
    records = metadata['records']
    if metadata['actual_new_runs'] != len(records):
        metadata['counter_recovery'] = dict(previous=metadata['actual_new_runs'], committed_records=len(records))
        metadata['actual_new_runs'] = len(records)
    scenarios = {scenario['id']: scenario for scenario in suite['scenarios']}
    completed = {}
    for record in records:
        row = record['row']
        scenario, controller = scenarios[row['scenario']], row['controller']
        if controller not in metadata['controllers']:
            raise ValueError('Unknown saved holdout controller')
        directory = (output / record['directory']).resolve()
        if not directory.is_relative_to(output):
            raise ValueError('Saved holdout path escaped output directory')
        metrics, warnings = _audit(directory, scenario, controller, cases[scenario['case']][0], fingerprint)
        if any(not wave2._equal(row.get(key), value) for key, value in dict(metrics, **_labels(scenario, controller, freeze)).items()):
            raise ValueError('Saved holdout result differs from audited raw records')
        if record.get('warning_details') != warnings or row.get('emergency_braking_warnings') != len(warnings):
            raise ValueError('Saved holdout warning records differ from raw logs')
        key = (scenario['id'], controller)
        if key in completed:
            raise ValueError('Duplicate saved holdout result')
        completed[key] = record
    try:
        for scenario in suite['scenarios']:
            for controller in metadata['controllers']:
                key = (scenario['id'], controller)
                if key in completed:
                    continue
                if time.monotonic() - started >= max_wall_seconds:
                    raise common.WallTimeCheckpoint('Holdout wall-time checkpoint after completed simulations')
                relative = Path('runs') / scenario['id'] / _labels(scenario, controller, freeze)['model_label']
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
                metrics, warnings = _audit(directory, scenario, controller, cases[scenario['case']][0], fingerprint)
                row = dict(metrics, **_labels(scenario, controller, freeze), emergency_braking_warnings=len(warnings))
                record = dict(directory=str(directory.relative_to(output)), row=row, warning_details=warnings)
                records.append(record)
                completed[key] = record
                metadata['actual_new_runs'] = len(records)
                metadata.pop('active_run', None)
                common._write_comparison(output, records)
                common.atomic_json(manifest_path, metadata)
                print(f"Holdout {len(records)}/{metadata['expected_runs']}: {scenario['id']} / {row['model_label']} | queue={metrics['mean_queue_vehicles']:.3f}, arrived={metrics['arrived_vehicles']}/{metrics['scheduled_vehicles']}", flush=True)
        baseline = {(record['row']['scenario'], record['row']['controller']): record['row'] for record in records if record['row']['controller'] in common.BASELINES}
        ai_rows = [record['row'] for record in records if record['row']['controller'] == 'smart']
        summary = common._summary(ai_rows, baseline)
        summary['case_profile_summaries'] = {
            f'{case}/{profile}': common._summary([row for row in ai_rows if row['case'] == case and row['profile'] == profile], baseline)
            for case, profile in sorted({(row['case'], row['profile']) for row in ai_rows})}
        # Report the frozen hypothesis; it never triggers another model selection.
        metadata.update(status='completed', summary=summary, independent_target_met=(summary['queue_change']['fixed'] <= -.05 and
                        summary['overall']['arrived_vehicles'] >= summary['baselines']['fixed']['arrived_vehicles'] and
                        summary['worst_profile_change'] <= .10),
                        next_action='Human checkpoint: report this unseen-network test, preserve the frozen policy; no test-based model reselection')
        common.atomic_json(manifest_path, metadata)
        common.atomic_json(output / 'summary.json', {name: metadata[name] for name in
                           ('status', 'actual_new_runs', 'expected_runs', 'summary', 'independent_target_met', 'next_action')})
        return metadata
    except common.WallTimeCheckpoint as exc:
        metadata.update(status='checkpoint_walltime', error=str(exc), next_action='Review bounded interval; --resume preserves the original frozen policy')
        common.atomic_json(manifest_path, metadata)
        return metadata
    except BaseException as exc:
        metadata.update(status='safety_hold' if isinstance(exc, common.SafetyHold) else
                        'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                        error=f'{type(exc).__name__}: {exc}')
        common.atomic_json(manifest_path, metadata)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--selection-wave', type=Path, default=DEFAULT_SELECTION)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=float, default=2700)
    args = parser.parse_args()
    result = run_holdout(args.out, args.suite, args.selection_wave, args.resume, args.max_wall_seconds)
    print(f"Holdout {result['status']}: {result['actual_new_runs']}/{result['expected_runs']} verified simulations", flush=True)


if __name__ == '__main__':
    main()
