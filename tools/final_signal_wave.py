"""Fresh paired validation, frozen selection, then untouched final evaluation."""
import argparse
from contextlib import ExitStack, contextmanager
import math
from pathlib import Path
import statistics
import sys
import time
from types import MappingProxyType
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import load_aware_wave as previous, common_rule_wave as helper
from tools import structure_wave, run_adaptive_wave as common
from tools.load_aware_signal_policy import VARIANTS as LOAD_VARIANTS
from transferable_signal import runner
from transferable_signal.deployment import validated_policy
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json
from transferable_signal.suite import catalog, demand, load_suite

INPUT = ROOT / 'network/final_signal_wave10_20261007'
OUTPUT = ROOT / 'results/runs/final_signal_wave10_20261007'
VARIANTS = MappingProxyType({'load_aware30': LOAD_VARIANTS['load_aware30']})


def plan():
    value = previous.BASE_PLAN()
    value.update(wave=10, candidates={k: dict(v) for k, v in VARIANTS.items()},
        validation_samples=4, seed_base=103000, max_runs=264,
        validation_conditions=48, final_conditions=24,
        validation_kind='known development geometries / fresh demand, no parameter tuning',
        fallback='v2 if the frozen candidate fails any original selection criterion',
        final_kind='previously prepared, never evaluated final inputs; never reselect',
        final_criteria=dict(queue_change_fixed=-.05, queue_change_webster=-.05,
            queue_change_max_pressure=0., arrivals_nondecrease=True, worst_group_fixed=.10))
    return value


def sources():
    return dict(previous.sources(), **{str(Path(__file__).relative_to(ROOT)): digest(Path(__file__))})


def preflight(preserved=False):
    proof = read_json(INPUT / 'proof.json')
    if proof['plan'] != plan() or proof['source_sha256'] != sources() or proof['policy_identity'] != validated_policy()[2]:
        raise ValueError('Frozen plan/model/source changed')
    protected = {**proof['input_sha256'], **proof['excluded']['suites'],
        **(proof['preserved_sha256'] if preserved else {})}
    for relative, hashed in protected.items():
        if digest(ROOT / relative) != hashed:
            raise ValueError(f'Frozen input/original result changed: {relative}')
    routes, seeds = (set(proof['excluded'][k]) for k in ('routes_sha256', 'seeds'))
    netsets = []
    base, _, identity = validated_policy()
    for stage, count in (('validation', 48), ('final', 24)):
        suite, cases = load_suite(INPUT / stage / 'suite.json')
        if suite['plan'] != plan() or suite['policy_identity'] != identity or len(suite['scenarios']) != count:
            raise ValueError('Split plan/policy/condition count differs')
        hashes = {spec['network_sha256'] for spec, _, _ in cases.values()}
        if hashes & set(base.data['training']['training_network_sha256']):
            raise ValueError('Training geometry contamination')
        netsets.append(hashes)
        seen = set()
        for s in suite['scenarios']:
            spec, _, _ = cases[s['case']]
            runner._check_scenario(base, s, spec, identity, suite)
            if (s['id'] in seen or s['seed'] in seeds or s['routes_sha256'] in routes
                    or s['duration_s'] != 600 or s['drain_s'] != 300 or s['synthetic'] is not True
                    or s['split'] != ('validation' if stage == 'validation' else 'test') or spec['limits'] != DEFAULT_LIMITS):
                raise ValueError('Split/demand/limits contamination')
            vehicles = ET.parse(s['routes']).getroot().findall('vehicle')
            if (len(vehicles) != s['scheduled'] or len({v.get('id') for v in vehicles}) != len(vehicles)
                    or any(not 0 <= float(v.get('depart')) < 600 for v in vehicles)):
                raise ValueError('Explicit demand accounting invalid')
            if stage == 'validation':
                i = [c['id'] for c in suite['cases']].index(s['case'])
                if s['sample'] not in range(4) or s['seed'] != 103000 + i * 100 + helper.PROFILES.index(s['profile']) * 10 + s['sample']:
                    raise ValueError('Validation schedule differs from frozen seed plan')
            seen.add(s['id']); seeds.add(s['seed']); routes.add(s['routes_sha256'])
    if netsets[0] & netsets[1]:
        raise ValueError('Validation/final geometries overlap')
    return proof


@contextmanager
def context():
    with ExitStack() as stack:
        for name, value in dict(INPUT=INPUT, OUTPUT=OUTPUT, VARIANTS=VARIANTS,
                sources=sources, plan=plan, policy=previous.policy, preflight=preflight).items():
            stack.enter_context(patch.object(helper, name, value))
        yield


def prepare():
    if INPUT.exists() or OUTPUT.exists():
        raise FileExistsError('Preserve all previous output')
    with previous.context():
        previous.verify_stage('validation')
        oldproof = helper.preflight(preserved=True)
    if read_json(previous.OUTPUT / 'selection.json')['chosen'] is not None or (previous.OUTPUT / 'final').exists() or (ROOT / 'results/runs/common_rule_wave8_20261007/final').exists():
        raise ValueError('Final conditions were already used')
    base, _, identity = validated_policy()
    exclusion = structure_wave.exclusions(base)
    final, _ = load_suite(previous.INPUT / 'final/suite.json')
    for key, field in (('seeds', 'seed'), ('routes_sha256', 'routes_sha256')):
        virgin = {s[field] for s in final['scenarios']}
        exclusion[key] = [v for v in exclusion[key] if v not in virgin]
    proof = dict(plan=plan(), source_sha256=sources(), policy_identity=identity, excluded=exclusion,
        previous_selection_sha256=digest(previous.OUTPUT / 'selection.json'),
        untouched_final_suite_sha256=digest(previous.INPUT / 'final/suite.json'),
        preserved_sha256={str(p.relative_to(ROOT)): digest(p) for p in (ROOT / 'results').rglob('*') if p.is_file()})
    INPUT.mkdir(parents=True)
    validation, cases = load_suite(previous.INPUT / 'validation/suite.json')
    fresh = dict(validation, plan=plan(), scenarios=[])
    folder = INPUT / 'validation'; folder.mkdir()
    for i, case in enumerate(validation['cases']):
        cid = case['id']; _, net, _ = cases[cid]
        target = folder / cid; target.mkdir()
        for pi, profile in enumerate(helper.PROFILES):
            for sample in range(4):
                seed = 103000 + i * 100 + pi * 10 + sample
                path = target / f'{profile}_s{sample:02d}.rou.xml'
                count = demand(catalog(net), profile, 600, seed, path)
                fresh['scenarios'].append(dict(id=f'confirm_{cid}_{profile}_s{sample:02d}', case=cid,
                    split='validation', profile=profile, sample=sample, seed=seed, scheduled=count,
                    synthetic=True, routes=str(path.resolve()), routes_sha256=digest(path), duration_s=600, drain_s=300))
    common.atomic_json(folder / 'suite.json', fresh)
    (INPUT / 'final').mkdir()
    common.atomic_json(INPUT / 'final/suite.json', dict(final, plan=plan()))
    proof['input_sha256'] = {**oldproof['input_sha256'], **{str(p.relative_to(ROOT)): digest(p)
        for p in INPUT.rglob('*') if p.is_file()}}
    common.atomic_json(INPUT / 'proof.json', proof)
    with context(): preflight(preserved=True)
    return proof


def selection(records):
    candidate = helper.select(records)
    return dict(candidate, candidate_chosen=candidate['chosen'], chosen=candidate['chosen'] or 'v2',
        fallback_used=candidate['chosen'] is None, frozen_before_final=True)


def final_summary(records, chosen):
    indexed = {(r['row']['scenario'], r['row']['policy_label']): r['row'] for r in records}
    ids = sorted({s for s, l in indexed})
    labels = sorted({l for s, l in indexed})
    def group(selected):
        out = {l: dict(mean_queue=statistics.mean(indexed[s, l]['mean_queue_vehicles'] for s in selected),
            arrived=sum(indexed[s, l]['arrived_vehicles'] for s in selected),
            scheduled=sum(indexed[s, l]['scheduled_vehicles'] for s in selected),
            remaining=sum(indexed[s, l]['scheduled_vehicles'] - indexed[s, l]['arrived_vehicles'] for s in selected)) for l in labels}
        return dict(controllers=out, queue_change={l: out[chosen]['mean_queue'] / out[l]['mean_queue'] - 1 for l in labels if l != chosen})
    overall = group(ids)
    cases = {c: group([s for s in ids if indexed[s, chosen]['case'] == c]) for c in sorted({indexed[s, chosen]['case'] for s in ids})}
    profiles = {p: group([s for s in ids if indexed[s, chosen]['profile'] == p]) for p in helper.PROFILES}
    crit = plan()['final_criteria']; changes = overall['queue_change']; ctrl = overall['controllers']
    worst = max(g['queue_change']['fixed'] for g in [*cases.values(), *profiles.values()])
    target = (all(changes[l] <= crit['queue_change_' + l] for l in ('fixed', 'webster', 'max_pressure'))
        and ctrl[chosen]['arrived'] >= max(ctrl[l]['arrived'] for l in ('fixed', 'webster', 'max_pressure')) and worst <= crit['worst_group_fixed'])
    candidate_target = helper.comparison(records, chosen)['target_met'] if chosen != 'v2' else None
    return dict(chosen=chosen, overall=overall, cases=cases, profiles=profiles, worst_group_change_fixed=worst,
        baseline_target_met=target, candidate_improvement_target_met=candidate_target,
        target_met=target and candidate_target is not False, final_reselection=False,
        conditions={s: group([s]) for s in ids})


def labels_for(stage):
    if stage == 'validation': return ['fixed', 'v2', *VARIANTS]
    chosen = read_json(OUTPUT / 'selection.json')['chosen']
    return ['fixed', 'webster', 'max_pressure', 'v2'] + ([chosen] if chosen != 'v2' else [])


def verify_stage(stage):
    preflight()
    suite_path = INPUT / stage / 'suite.json'; suite, cases = load_suite(suite_path)
    scenarios = {s['id']: s for s in suite['scenarios']}; labels = labels_for(stage)
    meta = read_json(OUTPUT / stage / 'manifest.json'); expected = len(scenarios) * len(labels)
    fingerprint = dict(proof_sha256=digest(INPUT / 'proof.json'), suite_sha256=digest(suite_path),
        source_sha256=sources(), selection_sha256=digest(OUTPUT / 'selection.json') if stage == 'final' else None)
    if meta['status'] != 'completed' or meta['actual_new_runs'] != expected or meta['expected_runs'] != expected or meta['labels'] != labels or meta['fingerprint'] != fingerprint:
        raise ValueError('Incomplete/changed paired wave')
    keys = set()
    for r in meta['records']:
        row = r['row']; key = row['scenario'], row['policy_label']; s = scenarios[key[0]]
        if key in keys or key[1] not in labels or helper.audit(suite_path, s, cases[s['case']][0], key[1], OUTPUT / r['directory']) != r:
            raise ValueError('Changed/duplicate record')
        keys.add(key)
    if keys != {(s, l) for s in scenarios for l in labels}: raise ValueError('Missing comparison')
    return meta


def run_stage(stage, resume=False, max_wall_seconds=2700):
    if not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0: raise ValueError('Invalid wall time')
    start = time.monotonic(); preflight(preserved=True)
    suite_path = INPUT / stage / 'suite.json'; suite, cases = load_suite(suite_path)
    if stage == 'final':
        prior = verify_stage('validation'); frozen = read_json(OUTPUT / 'selection.json')
        if frozen != selection(prior['records']): raise ValueError('Validation-only selection changed')
        if read_json(ROOT / '.codex-artifacts/final-wave10-validation-independent-20261007.json')['status'] != 'PASS':
            raise ValueError('Independent validation pass required before final')
    labels = labels_for(stage); expected = len(suite['scenarios']) * len(labels)
    out = OUTPUT / stage
    fingerprint = dict(proof_sha256=digest(INPUT / 'proof.json'), suite_sha256=digest(suite_path),
        source_sha256=sources(), selection_sha256=digest(OUTPUT / 'selection.json') if stage == 'final' else None)
    if out.exists():
        if not resume: raise FileExistsError('Preserve existing output')
        meta = read_json(out / 'manifest.json')
        if meta['fingerprint'] != fingerprint or meta['labels'] != labels or meta['status'] in ('failed', 'safety_hold'):
            raise ValueError('Changed resume/failure requires inspection')
    else:
        if resume: raise FileNotFoundError('No existing wave')
        out.mkdir(parents=True)
        meta = dict(status='started', labels=labels, fingerprint=fingerprint, expected_runs=expected,
            actual_new_runs=0, additional_training=0, records=[])
    common.atomic_json(out / 'manifest.json', meta)
    try:
        indexed = {s['id']: s for s in suite['scenarios']}; completed = set()
        for r in meta['records']:
            key = r['row']['scenario'], r['row']['policy_label']; s = indexed[key[0]]
            if key in completed or helper.audit(suite_path, s, cases[s['case']][0], key[1], OUTPUT / r['directory']) != r:
                raise ValueError('Resume result differs')
            completed.add(key)
        if len(completed) != meta['actual_new_runs']: raise ValueError('Counter differs')
        for s in suite['scenarios']:
            for label in labels:
                if (s['id'], label) in completed: continue
                if time.monotonic() - start >= max_wall_seconds:
                    meta['status'] = 'checkpoint'; common.atomic_json(out / 'manifest.json', meta); return meta
                preflight()
                directory = out / s['id'] / label
                meta.update(status='running', active_run=str(directory.relative_to(OUTPUT)))
                common.atomic_json(out / 'manifest.json', meta)
                if not directory.exists(): helper.replay(suite_path, s, label, directory)
                record = helper.audit(suite_path, s, cases[s['case']][0], label, directory)
                meta['records'].append(record); meta['actual_new_runs'] = len(meta['records']); meta.pop('active_run', None)
                common.atomic_json(out / 'manifest.json', meta)
                print(f"{stage} {meta['actual_new_runs']}/{expected} {s['id']}/{label}: queue={record['row']['mean_queue_vehicles']:.3f} arrived={record['row']['arrived_vehicles']}/{s['scheduled']} braking={len(record['warning_details'])}", flush=True)
        meta['status'] = 'completed'; common.atomic_json(out / 'manifest.json', meta)
        verify_stage(stage); preflight(preserved=True)
        result = selection(meta['records']) if stage == 'validation' else final_summary(meta['records'], read_json(OUTPUT / 'selection.json')['chosen'])
        path = OUTPUT / ('selection.json' if stage == 'validation' else 'final_summary.json')
        if path.exists() and read_json(path) != result: raise ValueError('Frozen decision differs')
        common.atomic_json(path, result)
        print(f"{stage}: chosen={result['chosen']} target_met={result.get('target_met')}", flush=True)
        return meta
    except BaseException as error:
        meta.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else 'safety_hold' if isinstance(error, common.SafetyHold) else 'failed', error=f'{type(error).__name__}: {error}')
        common.atomic_json(out / 'manifest.json', meta); raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('prepare', 'run', 'verify'))
    p.add_argument('--stage', choices=('validation', 'final'), default='validation')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--max-wall-seconds', type=float, default=2700)
    args = p.parse_args()
    if args.action == 'prepare':
        proof = prepare(); print(f"Prepared48 fresh validation +24 untouched final; preserved={len(proof['preserved_sha256'])}; no traffic")
    else:
        with context():
            meta = run_stage(args.stage, args.resume, args.max_wall_seconds) if args.action == 'run' else verify_stage(args.stage)
            print(f"{args.stage}: {meta['status']} {meta['actual_new_runs']}/{meta['expected_runs']}")
