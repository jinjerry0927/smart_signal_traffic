"""Reviewed, separate suffix batches; never resume or relabel a failed trial."""
import argparse
import csv
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import unseen_transfer_experiment as trial
from tools import unseen_transfer_build as build
from tools.verify_unseen_transfer import RawObserver, ReplayClock, model_scores, replay_action, verify_run
from transferable_signal.spec import digest, read_json, write_json
import numpy as np

OUTPUT = trial.DEFAULT_OUTPUT


def identifier(job):
    return job[0]['id'] + '/' + job[1]


def suffix(jobs, attempted_ids):
    expected = [identifier(j) for j in jobs]
    if attempted_ids != expected[:len(attempted_ids)] or len(attempted_ids) > len(jobs):
        raise ValueError('Attempts must be the exact planned prefix, without duplicates or retries')
    return jobs[len(attempted_ids):]


def partial_audit(path, job, fingerprint):
    """Verify a closed failed prefix; never invent full-horizon metrics for it."""
    scenario, name, (spec, network, _), routes = job
    metadata = read_json(path / 'manifest.json')
    if (metadata['status'] != 'failed' or metadata['fingerprint'] != fingerprint or metadata['scenario'] != scenario
            or metadata['controller'] != name or metadata['network_sha256'] != digest(network)
            or metadata['routes_sha256'] != digest(routes) or digest(routes) != scenario['routes_sha256']):
        raise ValueError('Failed run identity differs from its planned job')
    if (path / 'metrics.json').exists():
        raise ValueError('A failed partial run must not have completed metrics')
    frames = [json.loads(line) for line in (path / 'frames.jsonl').open(encoding='utf-8')]
    choices = [json.loads(line) for line in (path / 'decisions.jsonl').open(encoding='utf-8')]
    decisions = {r['time_s']: r for r in choices}
    last = frames[-1]['time_s']
    assert last <= scenario['duration_s'] + scenario['drain_s']
    assert len(decisions) == len(choices) and set(decisions) == set(range(0, last, 5))
    observer, plans = RawObserver(spec), {}
    clocks = {j['id']: ReplayClock(j['phases'], spec['limits']) for j in spec['junctions']}
    model = read_json(ROOT / build.audit.RELEASE / 'model.json')
    config = read_json(build.BUNDLE / 'protocol.json')['policy_config']
    events, actions = [], 0
    for now, frame in enumerate(frames):
        assert frame['time_s'] == now
        observer.sample(frame)
        if frame['teleports'] or frame['collisions']:
            events.append(dict(time_s=now, teleports=frame['teleports'], collisions=frame['collisions']))
        for junction in spec['junctions']:
            jid, clock = junction['id'], clocks[junction['id']]
            assert frame['clocks'][jid] == clock.snapshot() and frame['signals'][jid] == clock.signal()
            features, pressure, critical = observer.features(frame, junction, clock)
            obs = frame['observations'][jid]
            np.testing.assert_allclose(obs['features'], features, atol=1e-6, rtol=1e-6)
            np.testing.assert_allclose(obs['pressure'], pressure, atol=1e-7)
            np.testing.assert_allclose(obs['critical_ratio'], critical, atol=1e-7)
            assert obs['mask'] == clock.mask()
            if now in decisions:
                decision = decisions[now]['junctions'][jid]
                assert decision['clock'] == clock.snapshot() and decision['mask'] == clock.mask()
                np.testing.assert_allclose(decision['features'], obs['features'], atol=1e-6, rtol=1e-6)
                np.testing.assert_allclose(decision['pressure'], obs['pressure'], atol=1e-7)
                f, p, c = np.asarray(obs['features'], dtype=np.float32), np.asarray(obs['pressure']), np.asarray(obs['critical_ratio'])
                expected = replay_action(name, f, p, c, clock, model, config, plans, jid)
                assert decision['action'] == expected
                if decision['scores'] is not None:
                    np.testing.assert_allclose(decision['scores'], p if name == 'rules_pressure' else model_scores(f, model), atol=1e-6, rtol=1e-6)
                clock.request(expected)
                actions += 1
        for clock in clocks.values():
            clock.tick()
    assert len(events) == 1 and events[0]['time_s'] == last
    # A new suffix may follow a separately reviewed congestion teleport only.
    # Collision, execution and arithmetic errors remain an unconditional stop.
    assert events[0]['collisions'] == 0 and events[0]['teleports'] > 0
    log = (path / 'sumo-errors.log').read_text(encoding='utf-8')
    assert 'Error:' not in log and 'Teleporting vehicle' in log
    return dict(id=identifier(job), outcome='failed_teleport', path=str(path.relative_to(OUTPUT)),
                manifest_sha256=digest(path / 'manifest.json'), frames=len(frames), actions=actions, event=events[0],
                prefix_replay='PASS', completed_metrics=None,
                files_sha256={p.name: digest(p) for p in path.iterdir() if p.is_file()})


def ledger(number):
    fp = trial.checked_evaluation(build.DEFAULT_INPUT, require_review=True)
    jobs = trial.jobs_for('final', build.DEFAULT_INPUT)
    attempts = []
    parents = []
    folders = [OUTPUT / 'final'] + [OUTPUT / f'followup_round{i:02d}' for i in range(1, number)]
    for folder in folders:
        batch_file = folder / trial.BATCH_NAME
        batch = read_json(batch_file)
        assert batch['fingerprint'] == fp
        if folder.name != 'final':
            old_plan = read_json(build.BUNDLE / f'followup-round-{int(folder.name[-2:]):02d}.json')
            assert batch['plan_sha256'] == digest(build.BUNDLE / f'followup-round-{int(folder.name[-2:]):02d}.json')
            assert old_plan['runner_sha256'] == digest(Path(__file__))
            assert old_plan['review_note_sha256'] == digest(ROOT / old_plan['review_note'])
            assert old_plan['expected_jobs'] == [identifier(j) for j in suffix(jobs, [a['id'] for a in attempts])]
            assert next(a for a in attempts if a['id'] == old_plan['reviewed_failure']['id']) == old_plan['reviewed_failure']
        parents.append(dict(path=str(batch_file.relative_to(OUTPUT)), sha256=digest(batch_file)))
        for record in batch['records']:
            job = jobs[len(attempts)]
            trial.verify_completed(record, job, folder, fp)
            attempts.append(dict(id=record['id'], outcome='completed', path=str((folder / record['id']).relative_to(OUTPUT)), record=record))
        if batch['status'] == 'failed':
            job = jobs[len(attempts)]
            assert batch['failed_job'] == identifier(job)
            attempts.append(partial_audit(folder / identifier(job), job, fp))
        elif batch['status'] not in ('completed', 'paused_at_boundary'):
            raise ValueError('The prior process is not at a closed run boundary')
    suffix(jobs, [a['id'] for a in attempts])
    return fp, jobs, attempts, parents


def prepare(number, note):
    fp, jobs, attempts, parents = ledger(number)
    remaining = suffix(jobs, [a['id'] for a in attempts])
    assert remaining and attempts[-1]['outcome'] == 'failed_teleport'
    assert note.is_file() and len(note.read_text(encoding='utf-8')) > 200
    target = build.BUNDLE / f'followup-round-{number:02d}.json'
    if target.exists() or (OUTPUT / f'followup_round{number:02d}').exists():
        raise FileExistsError('Preserve existing plan/output')
    write_json(target, dict(status='Codex_separate_review_PASS', original_batch_status='failed_preserved',
        authority='User requested all comparisons after a separate Codex review. The stop was enforced and diagnosed; this is a new batch of previously authorized, unattempted jobs, not an automatic retry or a claim that the original protocol passed.',
        fingerprint=fp, runner_sha256=digest(Path(__file__)), review_note=str(note.relative_to(ROOT)), review_note_sha256=digest(note),
        stop_rule='Any error, collision, teleport, fingerprint or raw mismatch stops the entire suffix batch; any further suffix requires a new separate review',
        parameter_changes=0, additional_training=0, failed_job_retries=0, parents=parents,
        attempted_before=len(attempts), reviewed_failure=attempts[-1], expected_jobs=[identifier(j) for j in remaining],
        original_total_attempts_max=162, formal_primary_target_claim='INCOMPLETE/NOT PASSED; failed partial metrics never enter averages'))
    print('REVIEW PASS: separate suffix prepared', number, len(remaining), 'unattempted jobs; original failure preserved', flush=True)


def checked_plan(number):
    path = build.BUNDLE / f'followup-round-{number:02d}.json'
    plan = read_json(path)
    if (plan['status'] != 'Codex_separate_review_PASS' or plan['runner_sha256'] != digest(Path(__file__))
            or plan['review_note_sha256'] != digest(ROOT / plan['review_note'])):
        raise ValueError('Separate suffix review is missing or changed')
    fp, jobs, attempts, parents = ledger(number)
    remaining = suffix(jobs, [a['id'] for a in attempts])
    if (fp != plan['fingerprint'] or parents != plan['parents'] or [identifier(j) for j in remaining] != plan['expected_jobs']
            or attempts[-1] != plan['reviewed_failure'] or len(attempts) != plan['attempted_before']):
        raise ValueError('Reviewed suffix provenance/order changed')
    return plan, remaining


def run(number, resume=False, max_wall=2700):
    plan, jobs = checked_plan(number)
    folder = OUTPUT / f'followup_round{number:02d}'
    path = folder / trial.BATCH_NAME
    plan_sha = digest(build.BUNDLE / f'followup-round-{number:02d}.json')
    fp = plan['fingerprint']
    if resume:
        batch = read_json(path)
        if batch['status'] not in ('paused_at_boundary', 'completed') or batch['plan_sha256'] != plan_sha or batch['fingerprint'] != fp:
            raise ValueError('A failed suffix cannot be automatically resumed')
    else:
        folder.mkdir(parents=True, exist_ok=False)
        batch = dict(status='running', stage='reviewed_followup', fingerprint=fp, plan_sha256=plan_sha, records=[])
        write_json(path, batch)
    remaining = suffix(jobs, [r['id'] for r in batch['records']])
    for record, job in zip(batch['records'], jobs):
        trial.verify_completed(record, job, folder, fp)
    started = time.monotonic()
    for job in remaining:
        if time.monotonic() - started >= max_wall:
            batch['status'] = 'paused_at_boundary'
            write_json(path, batch)
            return
        scenario, name, (spec, network, _), routes = job
        job_id, destination = identifier(job), folder / identifier(job)
        try:
            if trial.fingerprint(build.DEFAULT_INPUT) != fp or digest(Path(__file__)) != plan['runner_sha256']:
                raise ValueError('Frozen producer or reviewed suffix source changed')
            result = trial.run_one(spec, network, routes, scenario, name, destination, fp, kind='reviewed_followup')
            verified = verify_run(destination, spec, fp)
            record = dict(id=job_id, scenario=scenario['id'], case=scenario['case'], profile=scenario['profile'], controller=name,
                manifest_sha256=digest(destination / 'manifest.json'), verification_sha256=digest(destination / 'verification.json'),
                metrics=verified['recomputed_metrics'], warnings=verified['warnings'], raw_seconds=verified['raw_seconds'], actions=verified['actions'])
            trial.verify_completed(record, job, folder, fp)
            batch['records'].append(record)
            write_json(path, batch)
            print(f'FOLLOWUP {number} [{len(batch["records"])}/{len(jobs)}] {job_id}: arrived {result["arrived_vehicles"]}/{result["scheduled_vehicles"]}', flush=True)
        except BaseException as error:
            batch.update(status='failed', failed_job=job_id, error=f'{type(error).__name__}: {error}')
            write_json(path, batch)
            raise
    batch['status'] = 'completed'
    write_json(path, batch)
    print('FOLLOWUP COMPLETED', number, len(jobs), flush=True)


def report(number):
    fp, jobs, attempts, parents = ledger(number + 1)
    suffix(jobs, [a['id'] for a in attempts])
    successful = [a['record'] for a in attempts if a['outcome'] == 'completed']
    counts = {}
    for r in successful:
        counts[r['scenario']] = counts.get(r['scenario'], 0) + 1
    common = [r for r in successful if counts[r['scenario']] == 6]
    names = read_json(build.BUNDLE / 'protocol.json')['execution']['run_order']
    by_name = {n: {r['scenario']: r for r in successful if r['controller'] == n} for n in names}
    pairs = {}
    for name in names:
        if name == 'smart_v2':
            continue
        keys = sorted(by_name[name].keys() & by_name['smart_v2'].keys())
        mean = lambda n: sum(by_name[n][k]['metrics']['mean_queue_vehicles'] for k in keys) / len(keys)
        arrivals = lambda n: sum(by_name[n][k]['metrics']['arrived_vehicles'] for k in keys)
        pairs[name] = dict(paired_conditions=len(keys), smart_mean_queue=mean('smart_v2'), baseline_mean_queue=mean(name),
            smart_queue_change=trial.change(mean('smart_v2'), mean(name)), smart_arrived=arrivals('smart_v2'), baseline_arrived=arrivals(name))
    result = dict(status='all_planned_attempts_accounted_for' if len(attempts) == 162 else 'incomplete',
        original_protocol_status='stopped_on_teleport; original failed batch preserved', formal_primary_target_met=None,
        formal_primary_target_evaluable=False, completed_runs=len(successful), failed_runs=len(attempts)-len(successful),
        unattempted_runs=162-len(attempts), common_complete_conditions=len(common)//6,
        followup_interpretation='Descriptive extension after reviewed stops. No original full-protocol PASS claim. Failed partial runs have no comparable completed metrics.',
        failed=[a for a in attempts if a['outcome'] != 'completed'], overall_common_completed=trial.summarize(common),
        cases_common_completed={c: trial.summarize([r for r in common if r['case'] == c]) for c in sorted({r['case'] for r in common})},
        profiles_common_completed={p: trial.summarize([r for r in common if r['profile'] == p]) for p in sorted({r['profile'] for r in common})},
        paired_smart_comparisons=pairs,
        learned_contribution_target_on_complete_pairs=pairs['rules_pressure']['smart_queue_change'] <= -.02 and pairs['rules_pressure']['smart_arrived'] >= pairs['rules_pressure']['baseline_arrived'],
        controller_coverage={n: dict(completed=len(by_name[n]), failed=sum(a['outcome'] != 'completed' and a['id'].endswith('/'+n) for a in attempts)) for n in names},
        additional_training=0, parameter_changes=0, traffic_reruns=0, fingerprint=fp, parents=parents,
        total_traffic_attempts=3+12+len(attempts), failed_partial_metrics_in_averages=False)
    target = OUTPUT / 'reviewed-followup-summary.json'
    if target.exists():
        raise FileExistsError('Preserve prior report')
    write_json(target, result)
    with (OUTPUT / 'reviewed-followup-comparison.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['job', 'outcome', 'mean_queue_vehicles', 'arrived', 'scheduled', 'remaining', 'path'])
        for a in attempts:
            m = a.get('record', {}).get('metrics')
            writer.writerow([a['id'], a['outcome'], m['mean_queue_vehicles'] if m else '', m['arrived_vehicles'] if m else '',
                m['scheduled_vehicles'] if m else '', m['not_departed_vehicles'] + m['departed_not_arrived_vehicles'] if m else '', a['path']])
    print('REPORT:', result['completed_runs'], 'completed,', result['failed_runs'], 'failed,', result['unattempted_runs'], 'unattempted; original primary target not claimed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run', 'report'])
    parser.add_argument('--round', type=int, required=True)
    parser.add_argument('--note', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=int, default=2700)
    args = parser.parse_args()
    if not 1 <= args.round <= 162 or not 1 <= args.max_wall_seconds <= 2700:
        parser.error('round/wall limit out of bounds')
    if args.action == 'prepare':
        if not args.note:
            parser.error('prepare requires a separate review note')
        prepare(args.round, args.note.resolve())
    elif args.action == 'run':
        run(args.round, args.resume, args.max_wall_seconds)
    else:
        report(args.round)
