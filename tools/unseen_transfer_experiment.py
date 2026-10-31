"""Bounded compatibility, reviewed evaluation, and paired frozen-model tests."""
import argparse
import contextlib
import csv
import io
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import unseen_transfer_build as build
from tools.unseen_transfer_runtime import run_one
from tools.verify_unseen_transfer import verify_run
import numpy as np
from transferable_signal.spec import digest, read_json, write_json
from transferable_signal.suite import load_suite

DEFAULT_OUTPUT = ROOT / 'results/runs/unseen_transfer_20261028'
EVALUATION = build.BUNDLE / 'evaluation-freeze-v2.json'
REVIEW = build.BUNDLE / 'evaluation-review-v2.json'
BATCH_NAME = 'batch-v2.json'
SOURCES = ['tools/unseen_transfer_build.py', 'tools/unseen_transfer_runtime.py',
           'tools/unseen_transfer_experiment.py', 'tools/verify_unseen_transfer.py']


def fingerprint(inputs):
    with contextlib.redirect_stdout(io.StringIO()):
        build.audit.verify()
    return dict(protocol_sha256=digest(build.BUNDLE / 'protocol.json'), input_freeze_sha256=digest(Path(inputs) / 'input-freeze.json'),
                sources_sha256={name: digest(ROOT / name) for name in SOURCES},
                model_sha256=build.audit.MODEL_SHA, policy_sha256=build.audit.POLICY_SHA,
                python=platform.python_version(), numpy=np.__version__,
                sumo=subprocess.check_output([build.sumolib.checkBinary('sumo'), '--version'], text=True).splitlines()[0],
                sumo_binary_sha256=digest(build.sumolib.checkBinary('sumo')))


def producer_identity(value):
    value = dict(value)
    value['sources_sha256'] = {k: v for k, v in value['sources_sha256'].items()
                               if k in ['tools/unseen_transfer_build.py', 'tools/unseen_transfer_runtime.py']}
    return value


def compatibility(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve() / 'compatibility'
    suite, cases = build.preflight(inputs / 'suite.json')
    if output.exists():
        raise FileExistsError('Compatibility output exists; preserve it')
    output.mkdir(parents=True)
    fp, records = fingerprint(inputs), []
    for index, case in enumerate(suite['cases']):
        spec, network, _ = cases[case['id']]
        routes = inputs / case['id'] / 'compatibility.rou.xml'
        scheduled = len(ET.parse(routes).getroot().findall('vehicle'))
        scenario = dict(id=case['id'] + '_compatibility', case=case['id'], profile='compatibility',
                        seed=289000 + index, scheduled=scheduled, duration_s=300, drain_s=300)
        path = output / case['id']
        result = run_one(spec, network, routes, scenario, 'fixed', path, fp, kind='compatibility')
        verified = verify_run(path, spec, fp)
        if result['arrived_vehicles'] != scheduled or result['not_departed_vehicles']:
            raise ValueError('Compatibility probe did not serve every planned OD/class vehicle')
        records.append(dict(case=case['id'], scheduled=scheduled, arrived=result['arrived_vehicles'],
                            warnings=result['warnings'], verification_sha256=digest(path / 'verification.json'),
                            actions=verified['actions']))
        print('COMPATIBILITY PASS', case['id'], f'{scheduled}/{scheduled}', 'warnings', len(result['warnings']), flush=True)
    write_json(output / 'summary.json', dict(status='PASS', fingerprint=fp, records=records,
               traffic_runs=3, horizon_s=600, role='functional probes; not performance comparisons or policy selection'))
    return records


def prepare_evaluation(inputs, output):
    build.preflight(Path(inputs) / 'suite.json')
    compatibility_record = read_json(Path(output) / 'compatibility/summary.json')
    fp = fingerprint(inputs)
    if compatibility_record['status'] != 'PASS' or producer_identity(compatibility_record['fingerprint']) != producer_identity(fp):
        raise ValueError('Compatibility evidence differs from current evaluation code/inputs')
    # Review may strengthen orchestration/checking after functional probes.
    # Recheck the unchanged producer's raw records without overwriting evidence
    # or repeating traffic; the old and reviewed source identities stay visible.
    _, cases = build.preflight(Path(inputs) / 'suite.json')
    rechecks = []
    for record in compatibility_record['records']:
        path = Path(output) / 'compatibility' / record['case']
        if digest(path / 'verification.json') != record['verification_sha256']:
            raise ValueError('Original compatibility verification changed')
        rechecks.append(verify_run(path, cases[record['case']][0], compatibility_record['fingerprint'], write=False))
    if EVALUATION.exists():
        raise FileExistsError('Evaluation freeze already exists')
    write_json(EVALUATION, dict(status='prepared_pending_review', fingerprint=fp,
        evaluation_revision=2, superseded_pre_review_freeze_sha256=digest(build.BUNDLE / 'evaluation-freeze.json'),
        compatibility_summary_sha256=digest(Path(output) / 'compatibility/summary.json'),
        compatibility_rechecks=rechecks,
        comparison_runs_max=174, functional_probe_runs=3, total_new_traffic_runs_max=177,
        note='Latest user request includes three new-network functional probes. The frozen 12-pilot/162-final comparison budget and all performance criteria remain unchanged.',
        review_requirement='separate code review note, tests and all 12 pilot verifications before final comparisons'))
    print('PASS: evaluation code and inputs frozen; final experiment requires review record')


def checked_evaluation(inputs, require_review=False):
    data = read_json(EVALUATION)
    current = fingerprint(inputs)
    if data['fingerprint'] != current:
        raise ValueError('Evaluation source/runtime/inputs changed after freeze')
    if require_review:
        review = read_json(REVIEW)
        if (review['status'] != 'PASS' or review['evaluation_sha256'] != digest(EVALUATION)
                or digest(build.BUNDLE / 'evaluation-review.md') != review['review_note_sha256']):
            raise ValueError('Review is missing or does not match current evaluation')
    return current


def jobs_for(stage, inputs):
    plan = read_json(build.BUNDLE / 'protocol.json')
    names = plan['execution']['run_order']
    if stage == 'pilot':
        suite_path = ROOT / 'network/transferable_v1/suite.json'
        suite, cases = load_suite(suite_path)
        scenarios = [s for s in suite['scenarios'] if s['case'] in ['valid_t2', 'valid_x3'] and s['profile'] == 'quiet']
        assert len(scenarios) == 2
    else:
        suite_path = Path(inputs) / 'suite.json'
        suite, cases = build.preflight(suite_path)
        scenarios = suite['scenarios']
    return [(scenario, name, cases[scenario['case']], suite_path.parent / scenario['routes'])
            for scenario in scenarios for name in names]


def verify_completed(record, job, output, fp):
    scenario, controller_name, (_, network, _), routes = job
    expected_id = scenario['id'] + '/' + controller_name
    if (record['id'] != expected_id or record['scenario'] != scenario['id'] or record['case'] != scenario['case']
            or record['profile'] != scenario['profile'] or record['controller'] != controller_name):
        raise ValueError('Completed job does not match the planned condition/controller')
    path = Path(output) / expected_id
    verification_file = record.get('verification_file', 'verification.json')
    if digest(path / 'manifest.json') != record['manifest_sha256'] or digest(path / verification_file) != record['verification_sha256']:
        raise ValueError('Completed verification or manifest changed')
    metadata = read_json(path / 'manifest.json')
    producer_fp = record.get('producer_fingerprint', fp)
    if producer_fp != fp and (not record.get('adopted_pre_final_pilot') or metadata['kind'] != 'pilot'
            or scenario['split'] != 'validation' or producer_identity(producer_fp) != producer_identity(fp)):
        raise ValueError('Only a producer-unchanged pre-final pilot audit can be adopted')
    if (metadata['status'] != 'completed' or metadata['fingerprint'] != producer_fp or metadata['scenario'] != scenario
            or metadata['controller'] != controller_name or metadata['network_sha256'] != digest(network)
            or metadata['routes_sha256'] != scenario['routes_sha256'] or digest(routes) != scenario['routes_sha256']):
        raise ValueError('Completed run violates paired input/controller provenance')
    for name, expected in metadata['files_sha256'].items():
        if digest(path / name) != expected:
            raise ValueError('Verified raw output changed: ' + str(path / name))
    verified = read_json(path / verification_file)
    if (verified['status'] != 'PASS' or verified['verification_source_sha256'] != digest(ROOT / 'tools/verify_unseen_transfer.py')
            or verified['recomputed_metrics'] != record['metrics'] or verified['warnings'] != record['warnings']):
        raise ValueError('Cached independent audit differs')


def run_batch(stage, inputs, output, resume=False, max_wall_seconds=2700):
    fp = checked_evaluation(inputs, require_review=stage == 'final')
    jobs = jobs_for(stage, inputs)
    expected = 12 if stage == 'pilot' else 162
    assert len(jobs) == expected
    output = Path(output).resolve() / stage
    manifest = output / BATCH_NAME
    if resume:
        batch = read_json(manifest)
        if batch['status'] not in ('paused_at_boundary', 'completed') or batch['fingerprint'] != fp:
            raise ValueError('Resume requires a completed-run boundary and unchanged provenance')
    else:
        if output.exists():
            raise FileExistsError('Batch output exists; resume only a verified boundary')
        output.mkdir(parents=True)
        batch = dict(status='running', stage=stage, fingerprint=fp, expected_runs=expected, records=[])
        write_json(manifest, batch)
    expected_order = [s['id'] + '/' + name for s, name, _, _ in jobs]
    if [r['id'] for r in batch['records']] != expected_order[:len(batch['records'])]:
        raise ValueError('Completed records are duplicated or not a valid planned prefix')
    completed = {r['id']: r for r in batch['records']}
    for record, job in zip(batch['records'], jobs):
        verify_completed(record, job, output, fp)
    started = time.monotonic()
    for scenario, name, (spec, network, _), routes in jobs:
        job_id = scenario['id'] + '/' + name
        if job_id in completed:
            continue
        if time.monotonic() - started >= max_wall_seconds:
            batch['status'] = 'paused_at_boundary'
            write_json(manifest, batch)
            print('PAUSED at verified boundary', len(batch['records']), '/', expected, flush=True)
            return batch
        path = output / job_id
        try:
            if fingerprint(inputs) != fp:
                raise ValueError('Frozen provenance changed during comparison')
            if digest(routes) != scenario['routes_sha256']:
                raise ValueError('Vehicle schedule changed')
            result = run_one(spec, network, routes, scenario, name, path, fp, kind=stage)
            verified = verify_run(path, spec, fp)
            record = dict(id=job_id, scenario=scenario['id'], case=scenario['case'], profile=scenario['profile'],
                          controller=name, manifest_sha256=digest(path / 'manifest.json'),
                          verification_sha256=digest(path / 'verification.json'), metrics=verified['recomputed_metrics'],
                          warnings=verified['warnings'], raw_seconds=verified['raw_seconds'], actions=verified['actions'])
            verify_completed(record, (scenario, name, (spec, network, None), routes), output, fp)
            batch['records'].append(record)
            write_json(manifest, batch)
            print(f'[{len(batch["records"])}/{expected}] {job_id}: arrived {result["arrived_vehicles"]}/{result["scheduled_vehicles"]}; warnings {len(result["warnings"])}', flush=True)
        except BaseException as error:
            batch.update(status='failed', failed_job=job_id, error=f'{type(error).__name__}: {error}')
            write_json(manifest, batch)
            raise
    batch['status'] = 'completed'
    write_json(manifest, batch)
    verify_batch(stage, inputs, output.parent)
    return batch


def verify_batch(stage, inputs, output):
    fp = checked_evaluation(inputs, require_review=stage == 'final')
    output = Path(output).resolve() / stage
    batch = read_json(output / BATCH_NAME)
    jobs = jobs_for(stage, inputs)
    expected_ids = {s['id'] + '/' + name for s, name, _, _ in jobs}
    if (batch['status'] != 'completed' or batch['fingerprint'] != fp or len(batch['records']) != len(jobs)
            or {r['id'] for r in batch['records']} != expected_ids):
        raise ValueError('Batch is incomplete, duplicated or has wrong provenance')
    expected_order = [s['id'] + '/' + name for s, name, _, _ in jobs]
    if [r['id'] for r in batch['records']] != expected_order:
        raise ValueError('Batch order differs from preregistered order')
    for record, job in zip(batch['records'], jobs):
        verify_completed(record, job, output, fp)
    print('PASS:', stage, len(jobs), 'paired runs and independent raw audit fingerprints', flush=True)
    return batch


def record_review(inputs, output):
    fp = checked_evaluation(inputs)
    pilot = verify_batch('pilot', inputs, output)
    note = build.BUNDLE / 'evaluation-review.md'
    if not note.is_file() or REVIEW.exists():
        raise ValueError('Write the separate review note first; preserve existing review records')
    write_json(REVIEW, dict(status='PASS', reviewer='Codex, separate source inspection and independent arithmetic/safety replay',
        evaluation_sha256=digest(EVALUATION), review_note_sha256=digest(note),
        pilot_batch_sha256=digest(Path(output) / 'pilot' / BATCH_NAME), pilot_runs=len(pilot['records']), fingerprint=fp))
    print('PASS: separate review recorded; final comparisons enabled')


def change(value, baseline):
    return (value - baseline) / baseline if baseline else 0. if not value else None


def summarize(records):
    controllers = ['fixed', 'webster', 'max_pressure', 'smart_v2', 'raw_smart', 'rules_pressure']
    grouped = {}
    for name in controllers:
        rows = [r for r in records if r['controller'] == name]
        grouped[name] = dict(mean_queue=sum(r['metrics']['mean_queue_vehicles'] for r in rows) / len(rows),
            arrived=sum(r['metrics']['arrived_vehicles'] for r in rows), scheduled=sum(r['metrics']['scheduled_vehicles'] for r in rows),
            remaining=sum(r['metrics']['not_departed_vehicles'] + r['metrics']['departed_not_arrived_vehicles'] for r in rows),
            warning_count=sum(len(r['warnings']) for r in rows))
    changes = {name: change(grouped['smart_v2']['mean_queue'], grouped[name]['mean_queue']) for name in controllers if name != 'smart_v2'}
    return dict(controllers=grouped, smart_queue_change=changes)


def summary(inputs, output):
    batch = verify_batch('final', inputs, output)
    rows = batch['records']
    overall = summarize(rows)
    cases = {case: summarize([r for r in rows if r['case'] == case]) for case in sorted({r['case'] for r in rows})}
    profiles = {profile: summarize([r for r in rows if r['profile'] == profile]) for profile in sorted({r['profile'] for r in rows})}
    conditions = {scenario: summarize([r for r in rows if r['scenario'] == scenario]) for scenario in sorted({r['scenario'] for r in rows})}
    criteria = read_json(build.BUNDLE / 'protocol.json')['criteria']
    check = lambda value, bound: value is not None and value <= bound + 1e-12
    checks = dict(
        fixed_queue=check(overall['smart_queue_change']['fixed'], criteria['fixed_queue_change_max']),
        webster_queue=check(overall['smart_queue_change']['webster'], criteria['webster_queue_change_max']),
        max_pressure_queue=check(overall['smart_queue_change']['max_pressure'], criteria['max_pressure_queue_change_max']),
        arrivals=all(overall['controllers']['smart_v2']['arrived'] >= overall['controllers'][name]['arrived']
                     for name in criteria['arrivals_nondecrease_vs']),
        case_guard=all(check(v['smart_queue_change']['fixed'], .10) for v in cases.values()),
        profile_guard=all(check(v['smart_queue_change']['fixed'], .10) for v in profiles.values()))
    contribution = check(overall['smart_queue_change']['rules_pressure'], -.02) and overall['controllers']['smart_v2']['arrived'] >= overall['controllers']['rules_pressure']['arrived']
    result = dict(status='completed', primary_target_met=all(checks.values()), checks=checks,
        learned_contribution_target_met=contribution, overall=overall, cases=cases, profiles=profiles, conditions=conditions,
        actual_final_runs=162, raw_seconds=sum(r['raw_seconds'] for r in rows), actions=sum(r['actions'] for r in rows),
        additional_training=0, policy_changes=0, field_validation=False,
        final_batch_sha256=digest(Path(output) / 'final' / BATCH_NAME))
    write_json(Path(output) / 'summary.json', result)
    with (Path(output) / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['scenario', 'case', 'profile', 'controller', 'mean_queue_vehicles', 'arrived', 'scheduled', 'remaining', 'warnings'])
        for r in rows:
            m = r['metrics']
            writer.writerow([r['scenario'], r['case'], r['profile'], r['controller'], m['mean_queue_vehicles'], m['arrived_vehicles'],
                             m['scheduled_vehicles'], m['not_departed_vehicles'] + m['departed_not_arrived_vehicles'], len(r['warnings'])])
    print('FINAL RESULT', 'primary target:', result['primary_target_met'], 'learning contribution:', contribution,
          'queue changes:', overall['smart_queue_change'], flush=True)
    return result


def recover_pilot(inputs, output):
    """Re-audit completed pre-final traces; never rerun or rewrite their producer outputs."""
    fp = checked_evaluation(inputs)
    folder = Path(output).resolve() / 'pilot'
    if REVIEW.exists() or (folder / BATCH_NAME).exists():
        raise ValueError('Pilot recovery must precede review and preserve any current batch')
    old = read_json(folder / 'batch.json')
    if old['status'] != 'failed' or producer_identity(old['fingerprint']) != producer_identity(fp):
        raise ValueError('Cannot recover a failed/changed producer as a verifier-only correction')
    jobs = jobs_for('pilot', inputs)
    records = []
    for scenario, name, (spec, network, _), routes in jobs:
        job_id = scenario['id'] + '/' + name
        path = folder / job_id
        if not (path / 'manifest.json').exists():
            break
        metadata = read_json(path / 'manifest.json')
        if metadata['status'] != 'completed' or metadata['fingerprint'] != old['fingerprint']:
            raise ValueError('Only already completed, producer-unchanged pilot runs can be recovered')
        filename = 'verification-review-v2.json'
        if (path / filename).exists():
            raise FileExistsError('Preserve previous recovery certificate')
        verified = verify_run(path, spec, old['fingerprint'], verification_name=filename)
        record = dict(id=job_id, scenario=scenario['id'], case=scenario['case'], profile=scenario['profile'], controller=name,
            manifest_sha256=digest(path / 'manifest.json'), verification_sha256=digest(path / filename),
            verification_file=filename, producer_fingerprint=old['fingerprint'], adopted_pre_final_pilot=True,
            metrics=verified['recomputed_metrics'], warnings=verified['warnings'], raw_seconds=verified['raw_seconds'], actions=verified['actions'])
        verify_completed(record, (scenario, name, (spec, network, None), routes), folder, fp)
        records.append(record)
    if len(records) != len(old['records']) + 1 or records[-1]['id'] != old['failed_job']:
        raise ValueError('Recovery does not match the explicitly diagnosed verifier failure')
    write_json(folder / BATCH_NAME, dict(status='paused_at_boundary', stage='pilot', fingerprint=fp,
        expected_runs=12, records=records, adopted_prior_batch_sha256=digest(folder / 'batch.json'), traffic_reruns=0,
        correction='Independent Webster replay numeric scalar type only; frozen producer, inputs, model and rules unchanged'))
    print('PASS: re-audited', len(records), 'completed pilot runs; traffic reruns 0; original evidence preserved')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['compatibility', 'prepare-evaluation', 'recover-pilot', 'pilot', 'record-review', 'final', 'verify', 'summary'])
    parser.add_argument('--inputs', type=Path, default=build.DEFAULT_INPUT)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=int, default=2700)
    args = parser.parse_args()
    if not 1 <= args.max_wall_seconds <= 2700:
        parser.error('--max-wall-seconds must be between 1 and 2700')
    if args.action == 'compatibility':
        compatibility(args.inputs, args.out)
    elif args.action == 'prepare-evaluation':
        prepare_evaluation(args.inputs, args.out)
    elif args.action == 'recover-pilot':
        recover_pilot(args.inputs, args.out)
    elif args.action in ('pilot', 'final'):
        run_batch(args.action, args.inputs, args.out, args.resume, args.max_wall_seconds)
    elif args.action == 'record-review':
        record_review(args.inputs, args.out)
    elif args.action == 'verify':
        verify_batch('final', args.inputs, args.out)
    else:
        summary(args.inputs, args.out)
