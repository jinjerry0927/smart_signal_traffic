"""Bounded, sequential validation wave. Existing frozen results are read only.

This file starts live SUMO traffic only when run_wave() or its CLI is invoked.
Every completed simulation is audited before another simulation starts.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import sumolib
from transferable_signal import VERSION
from transferable_signal import runner
from transferable_signal.model import SmartSignal
from transferable_signal.spec import digest, read_json
from transferable_signal.suite import load_suite

DEFAULT_SUITE = ROOT / 'network/transferable_reliability/suite.json'
DEFAULT_CACHE = ROOT / 'results/runs/transferable_reliability_validation'
DEFAULT_MODELS = [ROOT / 'results/runs' / name / 'model.json' for name in
                  ('transferable_train_150', 'transferable_train_150_seed43', 'transferable_train_150_seed44')]
CORE = ('__init__.py', 'control.py', 'controllers.py', 'environment.py', 'model.py', 'spec.py', 'suite.py', 'runner.py')
BASELINES = ('fixed', 'webster', 'max_pressure')


class SafetyHold(RuntimeError):
    """A new safety warning or unsafe event requires inspection."""


class WallTimeCheckpoint(RuntimeError):
    """Stop at a completed-run boundary without masking a SUMO timeout."""


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def warning_signatures(path):
    """Keep complete warning text; a changed vehicle/lane/time is a new warning."""
    text = Path(path).read_text(encoding='utf-8')
    if re.search(r'(^|\n)\s*(Error:|Fatal:|Quitting \(on error\))', text, re.I):
        raise SafetyHold(f'Fatal SUMO log: {path}')
    warnings = [line.strip() for line in text.splitlines() if 'Warning:' in line]
    if any('performs emergency braking' not in line for line in warnings):
        raise SafetyHold(f'Unexpected SUMO warning: {path}: {warnings}')
    return warnings


def audit_run(directory, scenario, spec, suite_hash, controller, model_hash=None, code_hashes=None,
              runtime=None, adaptive_policy=None):
    """Cross-check provenance, accounting, raw trip statistics, and SUMO logs."""
    directory = Path(directory)
    metadata, metrics = read_json(directory / 'manifest.json'), read_json(directory / 'metrics.json')
    expected = dict(version=VERSION, status='completed', suite_sha256=suite_hash,
                    controller=controller, model_sha256=model_hash,
                    scenario=scenario, network_sha256=spec['network_sha256'], limits=spec['limits'])
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f'Run provenance differs: {directory}: {key}')
    for key, value in (runtime or {}).items():
        if metadata.get(key) != value:
            raise ValueError(f'Run runtime differs: {directory}: {key}')
    if adaptive_policy is not None and metadata.get('adaptive_policy') != adaptive_policy:
        raise ValueError(f'Run adaptive configuration differs: {directory}')
    if code_hashes:
        for name, value in code_hashes.items():
            if metadata.get('code_sha256', {}).get(name) != value:
                raise ValueError(f'Run code differs: {directory}: {name}')
    if metrics.get('status') != 'completed' or metrics.get('scenario') != scenario['id'] or metrics.get('controller') != controller:
        raise ValueError(f'Run metrics identity differs: {directory}')
    for key, value in metrics.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool) and not math.isfinite(value):
            raise ValueError(f'Nonfinite metric: {directory}: {key}')
    count_keys = ('scheduled_vehicles', 'departed_vehicles', 'arrived_vehicles', 'not_departed_vehicles',
                  'departed_not_arrived_vehicles', 'running_vehicles_end', 'pending_vehicles_end',
                  'teleport_events', 'collision_vehicle_events', 'sample_seconds', 'max_queue_vehicles')
    if any(not isinstance(metrics.get(key), int) or isinstance(metrics.get(key), bool) or metrics[key] < 0 for key in count_keys):
        raise ValueError(f'Invalid integer counts: {directory}')
    if metrics['scheduled_vehicles'] != scenario['scheduled'] or metrics['sample_seconds'] != scenario['duration_s'] + scenario['drain_s']:
        raise ValueError(f'Run count/horizon differs: {directory}')
    if (metrics['scheduled_vehicles'] != metrics['departed_vehicles'] + metrics['not_departed_vehicles'] or
            metrics['departed_vehicles'] != metrics['arrived_vehicles'] + metrics['departed_not_arrived_vehicles'] or
            metrics['departed_not_arrived_vehicles'] != metrics['running_vehicles_end'] or
            metrics['not_departed_vehicles'] != metrics['pending_vehicles_end']):
        raise ValueError(f'Vehicle accounting differs: {directory}')
    trips = ET.parse(directory / 'tripinfo.xml').getroot().findall('tripinfo')
    completed = [item for item in trips if float(item.get('arrival')) >= 0]
    if len(trips) != metrics['departed_vehicles'] or len(completed) != metrics['arrived_vehicles']:
        raise ValueError(f'Raw trip counts differ: {directory}')
    for metric, attribute in (('mean_completed_waiting_time_s', 'waitingTime'),
                              ('mean_completed_travel_time_s', 'duration'),
                              ('mean_completed_time_loss_s', 'timeLoss')):
        values = [float(item.get(attribute)) for item in completed]
        actual = sum(values) / len(values) if values else None
        if (actual is None) != (metrics.get(metric) is None) or actual is not None and not math.isclose(actual, metrics[metric], rel_tol=1e-9, abs_tol=1e-8):
            raise ValueError(f'Raw trip statistic differs: {directory}: {metric}')
    waiting = [float(item.get('waitingTime')) for item in completed]
    p95 = float(np.percentile(waiting, 95)) if waiting else None
    if (p95 is None) != (metrics.get('p95_completed_waiting_time_s') is None) or p95 is not None and not math.isclose(p95, metrics['p95_completed_waiting_time_s'], rel_tol=1e-9, abs_tol=1e-8):
        raise ValueError(f'Raw percentile differs: {directory}')
    warnings = warning_signatures(directory / 'sumo-errors.log')
    warning_signatures(directory / 'sumo.log')
    if metrics['collision_vehicle_events'] or metrics['teleport_events']:
        raise SafetyHold(f'Collision or teleport: {directory}')
    return metrics, warnings


def _preflight(suite_path, cache_path, model_paths):
    suite_path, cache_path = Path(suite_path).resolve(), Path(cache_path).resolve()
    suite, cases = load_suite(suite_path)
    scenarios = suite['scenarios']
    if len(scenarios) != 30 or len(cases) != 2 or any(s['split'] != 'validation' for s in scenarios):
        raise ValueError('This wave requires the frozen 30-case, two-network validation suite')
    if len({scenario['id'] for scenario in scenarios}) != 30 or len({scenario['seed'] for scenario in scenarios}) != 30:
        raise ValueError('Validation IDs and demand seeds must be unique')
    for case in cases:
        for profile in ('quiet', 'busy', 'directional'):
            group = [scenario for scenario in scenarios if scenario['case'] == case and scenario['profile'] == profile]
            if len(group) != 5 or {scenario.get('sample') for scenario in group} != set(range(5)):
                raise ValueError('Validation must include samples zero through four for each case/profile')
    for scenario in scenarios:
        if len(ET.parse(suite_path.parent / scenario['routes']).findall('vehicle')) != scenario['scheduled']:
            raise ValueError('Frozen route count differs from scenario scheduled count')
    models = []
    for path in model_paths:
        path = Path(path).resolve()
        policy = SmartSignal(path)
        seed = policy.data['training']['seed']
        trained = policy.data['training'].get('training_network_sha256', [])
        if not trained or any(spec['network_sha256'] in trained for spec, _, _ in cases.values()):
            raise ValueError('Validation contamination or missing model training provenance')
        models.append(dict(seed=seed, path=str(path), sha256=digest(path)))
    if sorted(item['seed'] for item in models) != [42, 43, 44]:
        raise ValueError('This wave requires exactly model seeds 42, 43, and 44')
    hashes = {name: digest(ROOT / 'transferable_signal' / name) for name in CORE}
    hashes['evaluation_support.py'] = digest(ROOT / 'evaluation_support.py')
    sumo_version = subprocess.check_output([sumolib.checkBinary('sumo'), '--version'], text=True).splitlines()[0]
    suite_hash = digest(suite_path)
    cached = read_json(cache_path / 'manifest.json')
    if cached.get('status') != 'completed' or cached.get('completed_runs') != 180 or cached.get('expected_runs') != 180 or cached.get('suite_sha256') != suite_hash:
        raise ValueError('The source comparison is incomplete or uses a different suite')
    if {(item['seed'], item['sha256']) for item in cached.get('models', [])} != {(item['seed'], item['sha256']) for item in models}:
        raise ValueError('Cached model hashes differ')
    with (cache_path / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    indexed = {(row['scenario'], row['model_label']): row for row in rows}
    if len(rows) != 180 or len(indexed) != 180:
        raise ValueError('Source CSV contains missing or duplicate results')
    baseline, known = {}, {}
    for scenario in scenarios:
        spec = cases[scenario['case']][0]
        jobs = [(controller, controller, None) for controller in BASELINES]
        jobs.extend((f"smart_seed{model['seed']}", 'smart', model['sha256']) for model in models)
        for label, controller, model_hash in jobs:
            directory = cache_path / scenario['id'] / label
            metrics, warnings = audit_run(directory, scenario, spec, suite_hash, controller, model_hash, hashes)
            metadata = read_json(directory / 'manifest.json')
            if metadata.get('sumo') != sumo_version or metadata.get('python') != platform.python_version():
                raise ValueError(f'Cached runtime differs: {directory}')
            row = indexed.get((scenario['id'], label))
            if row is None:
                raise ValueError(f'Cached CSV result missing: {directory}')
            for key, value in metrics.items():
                recorded = row.get(key)
                if value is None:
                    matches = recorded == ''
                elif isinstance(value, bool):
                    matches = recorded.lower() == str(value).lower()
                elif isinstance(value, (int, float)):
                    matches = math.isclose(float(recorded), value, rel_tol=1e-9, abs_tol=1e-8)
                else:
                    matches = recorded == str(value)
                if not matches:
                    raise ValueError(f'Cached CSV metric differs: {directory}: {key}')
            known.setdefault(scenario['id'], set()).update(warnings)
            if controller in BASELINES:
                baseline[(scenario['id'], controller)] = metrics
    fingerprint = dict(suite_sha256=suite_hash, source_comparison_sha256=digest(cache_path / 'comparison.csv'),
                       models=models, core_sha256=hashes, sumo=sumo_version, python=platform.python_version())
    return suite, cases, models, baseline, known, fingerprint


def _summary(rows, baseline):
    scenarios = [row['scenario'] for row in rows]
    def aggregate(values):
        return dict(mean_queue_vehicles=float(np.mean([item['mean_queue_vehicles'] for item in values])),
                    arrived_vehicles=sum(item['arrived_vehicles'] for item in values),
                    max_queue_vehicles=max(item['max_queue_vehicles'] for item in values),
                    incomplete_vehicles=sum(item['scheduled_vehicles'] - item['arrived_vehicles'] for item in values))
    overall = aggregate(rows)
    references = {controller: aggregate([baseline[(scenario, controller)] for scenario in scenarios]) for controller in BASELINES}
    ratios = {controller: overall['mean_queue_vehicles'] / reference['mean_queue_vehicles'] - 1 for controller, reference in references.items()}
    profiles = {}
    for profile in sorted({row['profile'] for row in rows}):
        selected = [row for row in rows if row['profile'] == profile]
        result = aggregate(selected)
        fixed = aggregate([baseline[(row['scenario'], 'fixed')] for row in selected])
        result['queue_change_vs_fixed'] = result['mean_queue_vehicles'] / fixed['mean_queue_vehicles'] - 1
        profiles[profile] = result
    return dict(overall=overall, baselines=references, queue_change=ratios, profiles=profiles,
                worst_profile_change=max(item['queue_change_vs_fixed'] for item in profiles.values()))


def _select_candidate(pilot_summaries):
    eligible = []
    for variant, summary in pilot_summaries.items():
        overall, fixed = summary['overall'], summary['baselines']['fixed']
        summary['pilot_eligible'] = (overall['arrived_vehicles'] >= fixed['arrived_vehicles'] and
                                     summary['worst_profile_change'] <= .10)
        if summary['pilot_eligible']:
            eligible.append((overall['mean_queue_vehicles'], -overall['arrived_vehicles'],
                             summary['worst_profile_change'], overall['max_queue_vehicles'], variant))
    if eligible:
        return min(eligible)[-1]
    # Complete the diagnostic wave even when every candidate misses pilot gates.
    # This is recorded as ineligible and never called a successful policy.
    return min(pilot_summaries, key=lambda variant: (
        pilot_summaries[variant]['overall']['mean_queue_vehicles'],
        -pilot_summaries[variant]['overall']['arrived_vehicles'],
        pilot_summaries[variant]['worst_profile_change'],
        pilot_summaries[variant]['overall']['max_queue_vehicles'], variant))


def _write_comparison(output, records):
    if not records:
        return
    rows = [record['row'] for record in records]
    target = output / 'comparison.csv'
    temporary = target.with_suffix('.csv.tmp')
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with temporary.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(target)


def run_wave(output, suite_path=DEFAULT_SUITE, cache_path=DEFAULT_CACHE, model_paths=DEFAULT_MODELS,
             variants=None, resume=False, policy_factory=None, max_wall_seconds=2700):
    from transferable_signal.adaptive import AdaptiveSmartSignal, VARIANTS
    variants = list(VARIANTS) if variants is None else list(variants)
    if not variants or len(set(variants)) != len(variants) or any(variant not in VARIANTS for variant in variants):
        raise ValueError('Unknown or duplicate adaptive variant')
    if not isinstance(max_wall_seconds, (int, float)) or isinstance(max_wall_seconds, bool) or not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise ValueError('max_wall_seconds must be finite and positive')
    started = time.monotonic()
    policy_factory = AdaptiveSmartSignal if policy_factory is None else policy_factory
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    suite, cases, models, baseline, known, fingerprint = _preflight(suite_path, cache_path, model_paths)
    fingerprint.update(variants={variant: dict(VARIANTS[variant]) for variant in variants},
                       adaptive_sha256=digest(ROOT / 'transferable_signal/adaptive.py'),
                       wave_runner_sha256=digest(Path(__file__)))
    manifest_path = output / 'manifest.json'
    if output.exists():
        if not resume:
            raise FileExistsError('Wave output exists; use --resume for exactly unchanged inputs/code')
        metadata = read_json(manifest_path)
        if metadata.get('fingerprint') != fingerprint:
            raise ValueError('Wave resume inputs or code changed; create a new output directory')
        if metadata.get('status') in ('safety_hold', 'failed'):
            raise ValueError('Inspect failed/safety-held wave and start a new output after fixes')
    else:
        if resume:
            raise FileNotFoundError('No existing wave to resume')
        output.mkdir(parents=True)
        metadata = dict(status='started', phase='pilot', fingerprint=fingerprint, records=[],
                        baseline_reused_runs=len(suite['scenarios']) * len(BASELINES),
                        expected_max_new_runs=len(variants) * 6 + len(suite['scenarios']) * len(models) - 6,
                        actual_new_runs=0, pilot_summaries={}, selected_variant=None,
                        criteria=dict(pilot='Prefer arrivals >= Fixed and worst profile <= +10%, then smallest mean queue; otherwise diagnostic best queue',
                                      target='Each seed queue <= -5% Fixed, arrivals >= Fixed, worst profile <= +10%'))
    records = metadata['records']
    if metadata.get('actual_new_runs') != len(records):
        metadata['counter_recovery'] = dict(previous=metadata.get('actual_new_runs'), committed_records=len(records))
        metadata['actual_new_runs'] = len(records)
    metadata.setdefault('started_run_attempts', len(records))
    run_hashes = dict(fingerprint['core_sha256'], **{'adaptive.py': fingerprint['adaptive_sha256']})
    runtime = {key: fingerprint[key] for key in ('sumo', 'python')}
    completed = {}
    by_id = {scenario['id']: scenario for scenario in suite['scenarios']}
    for record in records:
        scenario = by_id[record['row']['scenario']]
        model = next(item for item in models if item['seed'] == record['row']['model_seed'])
        variant = record['row']['variant']
        if variant not in variants:
            raise ValueError('Saved variant differs from this wave')
        policy_metadata = dict(variant=variant, config=dict(VARIANTS[variant]))
        raw_metrics, _ = audit_run(output / record['directory'], scenario, cases[scenario['case']][0], fingerprint['suite_sha256'],
                                   'smart', model['sha256'], run_hashes, runtime, policy_metadata)
        if any(record['row'].get(key) != value for key, value in raw_metrics.items()):
            raise ValueError('Saved wave summary differs from raw run metrics')
        labels = dict(model_label=f"smart_{variant}_seed{model['seed']}", model_seed=model['seed'],
                      variant=variant, case=scenario['case'], profile=scenario['profile'], sample=scenario.get('sample', 0))
        if any(record['row'].get(key) != value for key, value in labels.items()):
            raise ValueError('Saved wave labels differ from scenario/model')
        key = (scenario['id'], variant, model['seed'])
        if key in completed:
            raise ValueError('Duplicate saved wave result')
        completed[key] = record

    def execute(scenario, variant, model, phase):
        key = (scenario['id'], variant, model['seed'])
        if key in completed:
            return completed[key]['row']
        if time.monotonic() - started >= max_wall_seconds:
            raise WallTimeCheckpoint('Bounded wave wall-time checkpoint reached after completed simulations')
        relative = Path('runs') / scenario['id'] / f"smart_{variant}_seed{model['seed']}"
        directory = output / relative
        retry = 0
        while directory.exists() and not (directory / 'metrics.json').exists():
            retry += 1
            directory = output / relative.with_name(relative.name + f'_retry{retry:02d}')
        metadata.update(status='running', phase=phase,
                        active_run=dict(scenario=scenario['id'], variant=variant, model_seed=model['seed'], directory=str(directory.relative_to(output))))
        atomic_json(manifest_path, metadata)
        policy_metadata = dict(variant=variant, config=dict(VARIANTS[variant]))
        if not directory.exists():
            metadata['started_run_attempts'] += 1
            atomic_json(manifest_path, metadata)
            original_provenance = runner.provenance
            def annotated_provenance(*args, **kwargs):
                return dict(original_provenance(*args, **kwargs), adaptive_policy=policy_metadata)
            with patch.object(runner, 'SmartSignal', lambda path: policy_factory(path, variant)), \
                    patch.object(runner, 'provenance', side_effect=annotated_provenance):
                runner.run(suite_path, scenario['id'], 'smart', directory, model_path=model['path'])
        metrics, warnings = audit_run(directory, scenario, cases[scenario['case']][0], fingerprint['suite_sha256'],
                                      'smart', model['sha256'], run_hashes, runtime, policy_metadata)
        new_warnings = set(warnings) - known.get(scenario['id'], set())
        row = dict(metrics, model_label=f"smart_{variant}_seed{model['seed']}", model_seed=model['seed'],
                   variant=variant, case=scenario['case'], profile=scenario['profile'], sample=scenario.get('sample', 0),
                   known_emergency_braking=len(warnings) - len(new_warnings), new_emergency_braking=len(new_warnings))
        record = dict(directory=str(directory.relative_to(output)), row=row,
                      known_warning_details=sorted(set(warnings) & known.get(scenario['id'], set())),
                      new_warning_details=sorted(new_warnings))
        records.append(record)
        metadata['actual_new_runs'] = len(records)
        completed[key] = record
        metadata.pop('active_run', None)
        _write_comparison(output, records)
        atomic_json(manifest_path, metadata)
        print(f"{phase} {metadata['actual_new_runs']}/{metadata['expected_max_new_runs']}: {scenario['id']} / {variant} / seed{model['seed']} | queue={metrics['mean_queue_vehicles']:.3f}, arrived={metrics['arrived_vehicles']}/{metrics['scheduled_vehicles']}", flush=True)
        return row

    try:
        pilot = [scenario for scenario in suite['scenarios'] if scenario.get('sample') == 0]
        pilot_model = next(model for model in models if model['seed'] == 43)
        for variant in variants:
            rows = [execute(scenario, variant, pilot_model, 'pilot') for scenario in pilot]
            metadata['pilot_summaries'][variant] = _summary(rows, baseline)
            atomic_json(manifest_path, metadata)
        selected = _select_candidate(metadata['pilot_summaries'])
        metadata['selected_variant'] = selected
        metadata['selected_pilot_eligible'] = metadata['pilot_summaries'][selected]['pilot_eligible']
        full = []
        for model in models:
            rows = [execute(scenario, selected, model, 'full') for scenario in suite['scenarios']]
            full.append(dict(model_seed=model['seed'], **_summary(rows, baseline)))
            metadata['full_summaries'] = full
            atomic_json(manifest_path, metadata)
        target_met = all(summary['queue_change']['fixed'] <= -.05 and
                         summary['overall']['arrived_vehicles'] >= summary['baselines']['fixed']['arrived_vehicles'] and
                         summary['worst_profile_change'] <= .10 for summary in full)
        metadata.update(status='completed', phase='review', target_met=target_met,
                        full_comparison_count=len(suite['scenarios']) * len(models),
                        next_action='Human checkpoint after this bounded wave; this validation does not establish unseen-network generalization')
        atomic_json(manifest_path, metadata)
        atomic_json(output / 'summary.json', {key: metadata[key] for key in
                                             ('status', 'selected_variant', 'target_met', 'pilot_summaries', 'full_summaries', 'actual_new_runs', 'baseline_reused_runs', 'next_action')})
        return metadata
    except WallTimeCheckpoint as exc:
        metadata.update(status='checkpoint_walltime', error=str(exc),
                        next_action='Review this bounded interval; --resume continues unchanged code and inputs')
        atomic_json(manifest_path, metadata)
        return metadata
    except BaseException as exc:
        metadata.update(status='safety_hold' if isinstance(exc, SafetyHold) else 'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                        error=f'{type(exc).__name__}: {exc}')
        atomic_json(manifest_path, metadata)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--cache', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--models', type=Path, nargs='+', default=DEFAULT_MODELS)
    parser.add_argument('--variants', nargs='+')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=float, default=2700)
    args = parser.parse_args()
    result = run_wave(args.out, args.suite, args.cache, args.models, args.variants, args.resume, max_wall_seconds=args.max_wall_seconds)
    print(f"Wave {result['status']}: {result['actual_new_runs']} new simulations, selected={result.get('selected_variant')}", flush=True)


if __name__ == '__main__':
    main()
