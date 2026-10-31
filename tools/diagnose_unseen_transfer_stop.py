"""Read-only diagnosis and explicit follow-up proposal after a stopped trial."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import unseen_transfer_experiment as experiment
from tools import unseen_transfer_build as build
from tools.verify_unseen_transfer import RawObserver, ReplayClock, replay_action
import numpy as np
from transferable_signal.spec import digest, read_json, write_json


def periods(times):
    groups = []
    for t in times:
        if not groups or t != groups[-1][-1] + 1:
            groups.append([t])
        else:
            groups[-1].append(t)
    return [dict(begin=g[0], end=g[-1], seconds=len(g)) for g in groups]


def diagnose():
    output = experiment.DEFAULT_OUTPUT
    folder = output / 'final'
    batch = read_json(folder / experiment.BATCH_NAME)
    assert batch['status'] == 'failed'
    fp = experiment.checked_evaluation(build.DEFAULT_INPUT, require_review=True)
    assert fp == batch['fingerprint']
    jobs = experiment.jobs_for('final', build.DEFAULT_INPUT)
    assert [r['id'] for r in batch['records']] == [s['id'] + '/' + name for s, name, _, _ in jobs[:len(batch['records'])]]
    for record, job in zip(batch['records'], jobs):
        experiment.verify_completed(record, job, folder, fp)
    index = len(batch['records'])
    scenario, name, (spec, network, _), routes = jobs[index]
    job_id = scenario['id'] + '/' + name
    assert job_id == batch['failed_job']
    path = folder / job_id
    metadata = read_json(path / 'manifest.json')
    assert metadata['status'] == 'failed' and metadata['fingerprint'] == fp
    assert metadata['scenario'] == scenario and metadata['routes_sha256'] == digest(routes)
    frames = [json.loads(line) for line in (path / 'frames.jsonl').open(encoding='utf-8')]
    decisions = {r['time_s']: r for r in [json.loads(line) for line in (path / 'decisions.jsonl').open(encoding='utf-8')]}
    model = read_json(ROOT / build.audit.RELEASE / 'model.json')
    config = read_json(build.BUNDLE / 'protocol.json')['policy_config']
    observer, plans = RawObserver(spec), {}
    clocks = {j['id']: ReplayClock(j['phases'], spec['limits']) for j in spec['junctions']}
    action_count = 0
    for time, frame in enumerate(frames):
        assert frame['time_s'] == time
        observer.sample(frame)
        for j in spec['junctions']:
            jid, clock = j['id'], clocks[j['id']]
            assert frame['clocks'][jid] == clock.snapshot()
            assert frame['signals'][jid] == clock.signal()
            f, pressure, critical = observer.features(frame, j, clock)
            obs = frame['observations'][jid]
            np.testing.assert_allclose(obs['features'], f, atol=1e-6, rtol=1e-6)
            np.testing.assert_allclose(obs['pressure'], pressure, atol=1e-7)
            np.testing.assert_allclose(obs['critical_ratio'], critical, atol=1e-7)
            assert obs['mask'] == clock.mask()
            if time in decisions:
                choice = decisions[time]['junctions'][jid]
                expected = replay_action(name, np.asarray(obs['features'], dtype=np.float32), np.asarray(obs['pressure']),
                    np.asarray(obs['critical_ratio']), clock, model, config, plans, jid)
                assert choice['action'] == expected
                clock.request(expected)
                action_count += 1
        for clock in clocks.values():
            clock.tick()
    route_xml = ET.parse(routes).getroot()
    vehicles = {v.get('id'): v.attrib for v in route_xml.findall('vehicle')}
    route_map = {r.get('id'): r.get('edges').split() for r in route_xml.findall('route')}
    vid = 'veh_00106'
    vehicle = vehicles[vid]
    route = route_map[vehicle['route']]
    first = [m for j in spec['junctions'] for m in j['movements']
             if spec['lanes'][m['incoming']]['edge'] == route[0] and spec['lanes'][m['outgoing']]['edge'] == route[1]]
    second = [m for j in spec['junctions'] for m in j['movements']
              if spec['lanes'][m['incoming']]['edge'] == route[1] and spec['lanes'][m['outgoing']]['edge'] == route[2]]
    compatible = [(a, b) for a in first for b in second if a['outgoing'] == b['incoming']]
    assert compatible, 'The affected bus requires a disconnected lane transition'
    stopped = [f for f in frames if 204 <= f['time_s'] <= 503]
    assert len(stopped) == 300 and all(f['vehicles'][vid][0] < .1 for f in stopped)
    upstream_green = [f for f in stopped if f['clocks']['zt_b']['stage'] == 'green' and f['clocks']['zt_b']['current'] == 1]
    downstream_green = [f['time_s'] for f in stopped if f['clocks']['zt_a']['stage'] == 'green' and f['clocks']['zt_a']['current'] == 0]
    events = [dict(time_s=f['time_s'], teleports=f['teleports'], collisions=f['collisions']) for f in frames if f['teleports'] or f['collisions']]
    counter = {}
    for r in batch['records']:
        counter[r['scenario']] = counter.get(r['scenario'], 0) + 1
    complete_conditions = {s for s, count in counter.items() if count == 6}
    paired = [r for r in batch['records'] if r['scenario'] in complete_conditions]
    diagnosis = dict(status='stopped_on_preregistered_teleport_rule', completed_final_runs=len(batch['records']),
        failed_attempts=1, unattempted_runs=len(jobs)-index-1, complete_paired_conditions=len(complete_conditions),
        failed_job=job_id, failed_manifest_sha256=digest(path / 'manifest.json'),
        failure_files_sha256={p.name: digest(p) for p in path.iterdir() if p.is_file()},
        checked_prefix=dict(frames=len(frames), last_time_s=frames[-1]['time_s'], controller_actions=action_count,
                            clock_signal_observation_policy_replay='PASS', collisions=sum(f['collisions'] for f in frames),
                            teleport_events=sum(f['teleports'] for f in frames)),
        vehicle=dict(id=vid, type=vehicle['type'], scheduled_depart_s=float(vehicle['depart']), route=route,
                     lane_compatible_without_required_lane_change=True, compatible_movements=compatible,
                     continuous_below_0_1_mps_begin=204, continuous_below_0_1_mps_end=503, consecutive_seconds=300),
        event=events, upstream_east_green_windows=periods([f['time_s'] for f in upstream_green]),
        downstream_north_green_windows=periods(downstream_green),
        receiving_lane1_vehicle_count_during_upstream_green=[min(f['lanes']['zt_link_in_1'][1] for f in upstream_green),
                                                           max(f['lanes']['zt_link_in_1'][1] for f in upstream_green)],
        inference='Route and signal/control replay are valid. High receiving-lane occupancy and mismatched upstream/downstream green windows are consistent with spillback/coordination failure under this Webster demand. Exact yield gap/leader cause is not established without position/leader data.',
        official_reference='https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html',
        formal_primary_evaluation='INCOMPLETE; do not claim original 27-condition target passed',
        partial_reference_only=experiment.summarize(paired),
        partial_reference_warning='21 fully completed conditions only; excludes the interrupted and unattempted conditions by the stopping rule, so it is not the preregistered full comparison',
        additional_training=0, model_or_policy_changes=0, teleport_disabled=False)
    proposal = dict(status='requires_user_authorization_to_change_batch_stop_handling',
        purpose='Finish only the 34 unattempted jobs as a separate follow-up; preserve the failed Webster attempt and original stopped batch',
        original_batch_sha256=digest(folder / experiment.BATCH_NAME), original_fingerprint=fp,
        producer_changes=0, parameter_changes=0, training_runs=0, rerun_failed_job=False,
        failure_handling=dict(teleport='stop that run immediately, preserve failure and exclude its partial metrics from paired averages, then continue the next job',
                             collision_or_execution_or_record_error='stop the whole follow-up and investigate'),
        interpretation='Original preregistered batch remains stopped/not passed. Report completion/failure for every condition, full coverage per controller, and means only on explicitly common completed conditions; report the learned-contribution subset separately.',
        jobs=[dict(scenario=s['id'], case=s['case'], profile=s['profile'], controller=n,
                   network_sha256=digest(net), routes_sha256=digest(r), seed=s['seed'], scheduled=s['scheduled'])
              for s,n,(_,net,_),r in jobs[index+1:]],
        additional_traffic_attempts_max=len(jobs)-index-1)
    for name, value in [('teleport-diagnosis.json', diagnosis), ('followup-proposal.json', proposal)]:
        target = build.BUNDLE / name
        if target.exists():
            raise FileExistsError('Preserve previous diagnosis/proposal: ' + str(target))
        write_json(target, value)
    print('PASS: 127 completed run fingerprints; failed prefix 505 frames/202 actions replayed; teleport confirmed')
    print('STOPPED: 21 complete paired conditions, 1 unpaired completed run, 1 failed run, 34 unattempted jobs')


if __name__ == '__main__':
    diagnose()
