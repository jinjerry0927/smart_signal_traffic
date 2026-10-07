"""Sequential second wave, with immutable first-wave evidence and three-seed pilot.

No traffic starts on import. The CLI executes live SUMO only after auditing all
source results. A rejected pilot stops this wave before the larger comparison.
"""
import argparse
import csv
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
from tools import run_adaptive_wave as previous
from transferable_signal import runner
from transferable_signal.spec import digest, read_json

DEFAULT_WAVE1 = ROOT / 'results/runs/adaptive_wave1_20261004'
CRITERIA = dict(pilot_seeds=[42, 43, 44], queue_ratio_fixed=.95, queue_ratio_wave1=.97,
                worst_profile_change=.10, require_arrivals_fixed=True,
                full='Every model: queue <= -5% Fixed, arrivals >= Fixed, worst profile <= +10%')


def _equal(actual, expected):
    if isinstance(expected, bool):
        return actual is expected or str(actual).lower() == str(expected).lower()
    if expected is None:
        return actual is None or actual == ''
    if isinstance(expected, (float, int)):
        try:
            return math.isclose(float(actual), expected, rel_tol=1e-9, abs_tol=1e-8)
        except (TypeError, ValueError):
            return False
    return actual == expected


def _audit_wave1(path, suite, cases, models, fingerprint, known):
    """Validate all 102 prior runs, then index the 90 selected-policy results."""
    from transferable_signal.adaptive import VARIANTS
    path = Path(path).resolve()
    metadata = read_json(path / 'manifest.json')
    old = metadata.get('fingerprint', {})
    if metadata.get('status') != 'completed' or metadata.get('selected_variant') != 'queue_guard':
        raise ValueError('First wave must be completed and select queue_guard')
    records = metadata.get('records', [])
    if len(records) != 102 or metadata.get('actual_new_runs') != len(records):
        raise ValueError('First wave run count differs')
    for key in ('suite_sha256', 'source_comparison_sha256', 'models', 'core_sha256', 'sumo', 'python'):
        if old.get(key) != fingerprint.get(key):
            raise ValueError(f'First-wave inputs differ: {key}')
    if old.get('adaptive_sha256') != digest(ROOT / 'transferable_signal/adaptive.py'):
        raise ValueError('First-wave policy code changed')
    if old.get('wave_runner_sha256') != digest(ROOT / 'tools/run_adaptive_wave.py'):
        raise ValueError('First-wave driver code changed')
    if old.get('variants') != {name: dict(config) for name, config in VARIANTS.items()}:
        raise ValueError('First-wave policy settings changed')
    with (path / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    indexed = {(row['scenario'], row['variant'], int(row['model_seed'])): row for row in csv_rows}
    if len(csv_rows) != len(records) or len(indexed) != len(records):
        raise ValueError('First-wave CSV has duplicate or missing rows')
    scenarios = {scenario['id']: scenario for scenario in suite['scenarios']}
    selected, all_keys = {}, set()
    original_known = {scenario: set(values) for scenario, values in known.items()}
    hashes = dict(fingerprint['core_sha256'], **{'adaptive.py': old['adaptive_sha256']})
    runtime = {key: fingerprint[key] for key in ('sumo', 'python')}
    for record in records:
        row = record['row']
        scenario = scenarios[row['scenario']]
        model = next(item for item in models if item['seed'] == row['model_seed'])
        variant = row['variant']
        if variant not in VARIANTS:
            raise ValueError('Unknown first-wave variant')
        policy = dict(variant=variant, config=dict(VARIANTS[variant]))
        directory = (path / record['directory']).resolve()
        if not directory.is_relative_to(path):
            raise ValueError('First-wave result path escaped its root')
        metrics, warnings = previous.audit_run(directory, scenario, cases[scenario['case']][0],
                                              fingerprint['suite_sha256'], 'smart', model['sha256'],
                                              hashes, runtime, policy)
        labels = dict(model_label=f"smart_{variant}_seed{model['seed']}", model_seed=model['seed'], variant=variant,
                      case=scenario['case'], profile=scenario['profile'], sample=scenario['sample'])
        if any(not _equal(row.get(key), value) for key, value in dict(metrics, **labels).items()):
            raise ValueError('First-wave saved result differs from raw metrics/labels')
        key = (scenario['id'], variant, model['seed'])
        if key in all_keys or key not in indexed or any(not _equal(indexed[key].get(name), value) for name, value in row.items()):
            raise ValueError('First-wave saved result differs from CSV')
        prior = original_known.get(scenario['id'], set())
        new_warnings = set(warnings) - prior
        if (record.get('known_warning_details') != sorted(set(warnings) & prior) or
                record.get('new_warning_details') != sorted(new_warnings) or
                row.get('known_emergency_braking') != len(warnings) - len(new_warnings) or
                row.get('new_emergency_braking') != len(new_warnings)):
            raise ValueError('First-wave warning records differ from raw logs')
        all_keys.add(key)
        # A first-wave emergency warning is evidence, rather than a new warning.
        known.setdefault(scenario['id'], set()).update(warnings)
        if variant == 'queue_guard':
            selected[(scenario['id'], model['seed'])] = metrics
    if len(selected) != len(scenarios) * len(models):
        raise ValueError('First-wave selected-policy comparison is incomplete')
    return selected, dict(path=str(path), manifest_sha256=digest(path / 'manifest.json'),
                          comparison_sha256=digest(path / 'comparison.csv'),
                          old_driver_sha256=old['wave_runner_sha256'], old_policy_sha256=old['adaptive_sha256'])


def _preflight(suite_path, cache_path, wave1_path, model_paths):
    suite, cases, models, baselines, known, fingerprint = previous._preflight(suite_path, cache_path, model_paths)
    wave1, wave1_fingerprint = _audit_wave1(wave1_path, suite, cases, models, fingerprint, known)
    originals = {(scenario['id'], model['seed']): read_json(Path(cache_path) / scenario['id'] /
                  f"smart_seed{model['seed']}" / 'metrics.json')
                 for scenario in suite['scenarios'] for model in models}
    fingerprint['first_wave'] = wave1_fingerprint
    return suite, cases, models, baselines, known, fingerprint, originals, wave1


def _summary_base(rows, baselines, originals, wave1):
    summary = previous._summary(rows, baselines)
    for label, reference in (('original_smart', originals), ('wave1_queue_guard', wave1)):
        values = [reference[(row['scenario'], row['model_seed'])] for row in rows]
        stats = dict(mean_queue_vehicles=float(np.mean([item['mean_queue_vehicles'] for item in values])),
                     arrived_vehicles=sum(item['arrived_vehicles'] for item in values),
                     incomplete_vehicles=sum(item['scheduled_vehicles'] - item['arrived_vehicles'] for item in values),
                     max_queue_vehicles=max(item['max_queue_vehicles'] for item in values))
        summary['baselines'][label] = stats
        summary['queue_change'][label] = summary['overall']['mean_queue_vehicles'] / stats['mean_queue_vehicles'] - 1
    return summary


def _summary(rows, baselines, originals, wave1):
    summary = _summary_base(rows, baselines, originals, wave1)
    summary['case_summaries'] = {
        case: _summary_base([row for row in rows if row['case'] == case], baselines, originals, wave1)
        for case in sorted({row['case'] for row in rows})}
    summary['case_profile_summaries'] = {
        f'{case}/{profile}': _summary_base([row for row in rows if row['case'] == case and row['profile'] == profile],
                                         baselines, originals, wave1)
        for case, profile in sorted({(row['case'], row['profile']) for row in rows})}
    return summary


def _select_candidate(summaries):
    eligible = []
    for variant, summary in summaries.items():
        reasons = []
        seeds = summary['seed_summaries']
        if sorted(item['model_seed'] for item in seeds) != CRITERIA['pilot_seeds']:
            raise ValueError('Pilot needs all three training seeds')
        for seed_summary in seeds:
            reasons.extend(_seed_gate_reasons(seed_summary))
        summary.update(pilot_eligible=not reasons, rejection_reasons=reasons)
        summary['worst_seed_queue_change'] = max(item['queue_change']['fixed'] for item in seeds)
        if not reasons:
            overall = summary['overall']
            eligible.append((summary['worst_seed_queue_change'], overall['mean_queue_vehicles'],
                             -overall['arrived_vehicles'], summary['worst_profile_change'],
                             overall['max_queue_vehicles'], variant))
    return min(eligible)[-1] if eligible else None


def _seed_gate_reasons(summary):
    seed = summary['model_seed']
    overall, fixed = summary['overall'], summary['baselines']['fixed']
    reasons = []
    if overall['mean_queue_vehicles'] > CRITERIA['queue_ratio_fixed'] * fixed['mean_queue_vehicles']:
        reasons.append(f'seed{seed}:queue_improvement_fixed_below_5pct')
    if overall['arrived_vehicles'] < fixed['arrived_vehicles']:
        reasons.append(f'seed{seed}:arrivals_below_fixed')
    if summary['worst_profile_change'] > CRITERIA['worst_profile_change']:
        reasons.append(f'seed{seed}:profile_queue_worse_than_10pct')
    if seed == 44 and overall['mean_queue_vehicles'] > CRITERIA['queue_ratio_wave1'] * summary['baselines']['wave1_queue_guard']['mean_queue_vehicles']:
        reasons.append('seed44:queue_improvement_wave1_below_3pct')
    return reasons


def _run_hashes(fingerprint):
    # The v2 scorer uses adaptive._mask, so its imported v1 helper is also frozen.
    return dict(fingerprint['core_sha256'], **{
        'adaptive.py': fingerprint['first_wave']['old_policy_sha256'],
        'adaptive_v2.py': fingerprint['adaptive_v2_sha256']})


def _labels(scenario, variant, model):
    return dict(model_label=f"smart_v2_{variant}_seed{model['seed']}", model_seed=model['seed'], variant=variant,
                case=scenario['case'], profile=scenario['profile'], sample=scenario.get('sample', 0))


def _record_audit(record, output, scenarios, models, variants, cases, fingerprint, known):
    row = record['row']
    scenario = scenarios[row['scenario']]
    model = next(item for item in models if item['seed'] == row['model_seed'])
    variant = row['variant']
    if variant not in variants:
        raise ValueError('Saved variant differs from this wave')
    directory = (output / record['directory']).resolve()
    if not directory.is_relative_to(output):
        raise ValueError('Saved result path escaped wave output')
    hashes = _run_hashes(fingerprint)
    runtime = {key: fingerprint[key] for key in ('sumo', 'python')}
    policy = dict(version='adaptive-v2', variant=variant, config=fingerprint['variants'][variant])
    metrics, warnings = previous.audit_run(directory, scenario, cases[scenario['case']][0], fingerprint['suite_sha256'],
                                          'smart', model['sha256'], hashes, runtime, policy)
    if any(not _equal(row.get(key), value) for key, value in dict(metrics, **_labels(scenario, variant, model)).items()):
        raise ValueError('Saved wave result differs from raw metrics/labels')
    prior = known.get(scenario['id'], set())
    if record['known_warning_details'] != sorted(set(warnings) & prior) or record['new_warning_details'] != sorted(set(warnings) - prior):
        raise ValueError('Saved warning details differ from raw logs')
    if row['known_emergency_braking'] != len(warnings) - len(set(warnings) - prior) or row['new_emergency_braking'] != len(set(warnings) - prior):
        raise ValueError('Saved warning counts differ from raw logs')
    return scenario['id'], variant, model['seed']


def run_wave2(output, suite_path=previous.DEFAULT_SUITE, cache_path=previous.DEFAULT_CACHE,
              wave1_path=DEFAULT_WAVE1, model_paths=previous.DEFAULT_MODELS, variants=None,
              resume=False, policy_factory=None, max_wall_seconds=2700):
    from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2, VARIANTS_V2
    variants = list(VARIANTS_V2) if variants is None else list(variants)
    if not 1 <= len(variants) <= 3 or len(set(variants)) != len(variants) or any(name not in VARIANTS_V2 for name in variants):
        raise ValueError('Provide one to three distinct known second-wave variants')
    if not isinstance(max_wall_seconds, (int, float)) or isinstance(max_wall_seconds, bool) or not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise ValueError('max_wall_seconds must be finite and positive')
    started = time.monotonic()
    policy_factory = AdaptiveSmartSignalV2 if policy_factory is None else policy_factory
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    suite, cases, models, baselines, known, fingerprint, originals, wave1 = _preflight(suite_path, cache_path, wave1_path, model_paths)
    fingerprint.update(variants={variant: dict(VARIANTS_V2[variant]) for variant in variants}, criteria=dict(CRITERIA),
                       adaptive_v2_sha256=digest(ROOT / 'transferable_signal/adaptive_v2.py'),
                       wave2_driver_sha256=digest(Path(__file__)))
    manifest_path = output / 'manifest.json'
    pilot = [scenario for scenario in suite['scenarios'] if scenario.get('sample') == 0]
    if len(pilot) != 6:
        raise ValueError('Second wave requires six sample-zero pilot conditions')
    if output.exists():
        if not resume:
            raise FileExistsError('Wave output exists; use --resume for exactly unchanged inputs/code')
        metadata = read_json(manifest_path)
        if metadata.get('fingerprint') != fingerprint:
            raise ValueError('Wave resume inputs, criteria, or code changed')
        if metadata.get('status') in ('safety_hold', 'failed'):
            raise ValueError('Inspect failed wave and start a separate output after fixes')
    else:
        if resume:
            raise FileNotFoundError('No existing second wave to resume')
        output.mkdir(parents=True)
        metadata = dict(status='started', phase='pilot', fingerprint=fingerprint, records=[],
                        baseline_reused_runs=len(suite['scenarios']) * len(previous.BASELINES),
                        first_wave_reference_runs=len(wave1), original_smart_reference_runs=len(originals),
                        expected_max_new_runs=len(variants) * len(pilot) * len(models) +
                        len(suite['scenarios']) * len(models) - len(pilot) * len(models),
                        actual_new_runs=0, started_run_attempts=0, pilot_summaries={}, selected_variant=None)
    records = metadata['records']
    if metadata.get('actual_new_runs') != len(records):
        metadata['counter_recovery'] = dict(previous=metadata.get('actual_new_runs'), committed_records=len(records))
        metadata['actual_new_runs'] = len(records)
    scenarios = {scenario['id']: scenario for scenario in suite['scenarios']}
    completed = {}
    for record in records:
        key = _record_audit(record, output, scenarios, models, variants, cases, fingerprint, known)
        if key in completed:
            raise ValueError('Duplicate saved second-wave result')
        completed[key] = record

    def execute(scenario, variant, model, phase):
        key = (scenario['id'], variant, model['seed'])
        if key in completed:
            return completed[key]['row']
        if time.monotonic() - started >= max_wall_seconds:
            raise previous.WallTimeCheckpoint('Second-wave wall-time checkpoint after completed simulations')
        relative = Path('runs') / scenario['id'] / _labels(scenario, variant, model)['model_label']
        directory = output / relative
        retry = 0
        while directory.exists() and not (directory / 'metrics.json').exists():
            retry += 1
            directory = output / relative.with_name(relative.name + f'_retry{retry:02d}')
        metadata.update(status='running', phase=phase, active_run=dict(scenario=scenario['id'], variant=variant,
                                                                     model_seed=model['seed'], directory=str(directory.relative_to(output))))
        previous.atomic_json(manifest_path, metadata)
        policy = dict(version='adaptive-v2', variant=variant, config=fingerprint['variants'][variant])
        if not directory.exists():
            metadata['started_run_attempts'] += 1
            previous.atomic_json(manifest_path, metadata)
            original_provenance = runner.provenance
            def annotated_provenance(*args, **kwargs):
                return dict(original_provenance(*args, **kwargs), adaptive_policy=policy)
            with patch.object(runner, 'SmartSignal', lambda path: policy_factory(path, variant)), \
                    patch.object(runner, 'provenance', side_effect=annotated_provenance):
                runner.run(suite_path, scenario['id'], 'smart', directory, model_path=model['path'])
        hashes = _run_hashes(fingerprint)
        runtime = {name: fingerprint[name] for name in ('sumo', 'python')}
        metrics, warnings = previous.audit_run(directory, scenario, cases[scenario['case']][0], fingerprint['suite_sha256'],
                                               'smart', model['sha256'], hashes, runtime, policy)
        new = set(warnings) - known.get(scenario['id'], set())
        row = dict(metrics, **_labels(scenario, variant, model), known_emergency_braking=len(warnings) - len(new),
                   new_emergency_braking=len(new))
        record = dict(directory=str(directory.relative_to(output)), row=row,
                      known_warning_details=sorted(set(warnings) & known.get(scenario['id'], set())),
                      new_warning_details=sorted(new))
        records.append(record)
        completed[key] = record
        metadata['actual_new_runs'] = len(records)
        metadata.pop('active_run', None)
        previous._write_comparison(output, records)
        previous.atomic_json(manifest_path, metadata)
        print(f"{phase} {len(records)}/{metadata['expected_max_new_runs']}: {scenario['id']} / {variant} / seed{model['seed']} | queue={metrics['mean_queue_vehicles']:.3f}, arrived={metrics['arrived_vehicles']}/{metrics['scheduled_vehicles']}", flush=True)
        return row

    def finish(status):
        metadata.update(status=status, phase='review', next_action='Review the bounded second wave before further policy changes or unseen-network testing')
        previous.atomic_json(manifest_path, metadata)
        previous.atomic_json(output / 'summary.json', {name: metadata.get(name) for name in
                             ('status', 'selected_variant', 'target_met', 'pilot_summaries', 'full_summaries',
                              'actual_new_runs', 'started_run_attempts', 'baseline_reused_runs', 'first_wave_reference_runs', 'next_action')})
        return metadata

    try:
        for variant in variants:
            rows, per_seed = [], []
            for model in models:
                selected_rows = [execute(scenario, variant, model, 'pilot') for scenario in pilot]
                rows.extend(selected_rows)
                per_seed.append(dict(model_seed=model['seed'], **_summary(selected_rows, baselines, originals, wave1)))
            metadata['pilot_summaries'][variant] = dict(_summary(rows, baselines, originals, wave1), seed_summaries=per_seed)
            previous.atomic_json(manifest_path, metadata)
        selected = _select_candidate(metadata['pilot_summaries'])
        metadata['selected_variant'] = selected
        if selected is None:
            metadata['target_met'] = False
            return finish('pilot_rejected')
        full = []
        for model in models:
            rows = [execute(scenario, selected, model, 'full') for scenario in suite['scenarios']]
            summary = dict(model_seed=model['seed'], **_summary(rows, baselines, originals, wave1))
            summary['gate_failure_reasons'] = _seed_gate_reasons(summary)
            summary['gate_met'] = not summary['gate_failure_reasons']
            full.append(summary)
            metadata['full_summaries'] = full
            previous.atomic_json(manifest_path, metadata)
        metadata['target_met'] = all(summary['gate_met'] for summary in full)
        metadata['full_comparison_count'] = len(suite['scenarios']) * len(models)
        return finish('completed')
    except previous.WallTimeCheckpoint as exc:
        metadata.update(status='checkpoint_walltime', error=str(exc), next_action='Review interval, then --resume with unchanged code and inputs')
        previous.atomic_json(manifest_path, metadata)
        return metadata
    except BaseException as exc:
        metadata.update(status='safety_hold' if isinstance(exc, previous.SafetyHold) else
                        'interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                        error=f'{type(exc).__name__}: {exc}')
        previous.atomic_json(manifest_path, metadata)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--suite', type=Path, default=previous.DEFAULT_SUITE)
    parser.add_argument('--cache', type=Path, default=previous.DEFAULT_CACHE)
    parser.add_argument('--wave1', type=Path, default=DEFAULT_WAVE1)
    parser.add_argument('--models', type=Path, nargs='+', default=previous.DEFAULT_MODELS)
    parser.add_argument('--variants', nargs='+')
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=float, default=2700)
    args = parser.parse_args()
    result = run_wave2(args.out, args.suite, args.cache, args.wave1, args.models, args.variants, args.resume,
                       max_wall_seconds=args.max_wall_seconds)
    print(f"Wave2 {result['status']}: {result['actual_new_runs']} verified runs, selected={result.get('selected_variant')}", flush=True)


if __name__ == '__main__':
    main()
