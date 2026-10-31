"""Audit existing evidence and seal a test protocol; never trains or runs SUMO."""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RELEASE = 'results/runs/final_signal_wave10_20261007/release'
BASE_SUITE = 'network/transferable_v1/suite.json'
MODEL_SHA = '8eca32d46b9cc285162cf0e70fceb1a8d696254bf72b6954d0a764a482c8b999'
POLICY_SHA = 'a375c53e085959d9521bf6090b9bac85cf257133241fd28c07eeb82d158379f3'
DEFAULT_OUT = ROOT / 'docs/unseen_transfer_20261028'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def canonical(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def relative(root, path):
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def resolve_reference(root, parent, value):
    """Read old machine paths from this checkout without modifying old evidence."""
    text = value.replace('\\', '/')
    if (len(text) > 1 and text[1] == ':') or text.startswith('/'):
        choices = [text.split('/' + folder + '/', 1)[1]
                   for folder in ('network', 'results', '.codex-artifacts')
                   if '/' + folder + '/' in text]
        folders = [folder for folder in ('network', 'results', '.codex-artifacts')
                   if '/' + folder + '/' in text]
        if len(choices) != 1:
            raise ValueError('Unrecognized historical repository path: ' + text)
        path = root / folders[0] / choices[0]
    else:
        path = parent / text
    path = path.resolve()
    path.relative_to(root.resolve())
    return path


def reference_record(root, parent, value, expected):
    path = resolve_reference(root, parent, value)
    actual = digest(path) if path.is_file() else None
    return dict(path=relative(root, path), expected_sha256=expected, actual_sha256=actual,
                status='missing' if actual is None else 'matched' if actual == expected else 'mismatch')


def network_description(path):
    """Conservative ID/translation-independent screening, not a safety validator."""
    net = ET.parse(path).getroot()
    edges = {e.get('id'): e for e in net.findall('edge') if e.get('function') != 'internal'}
    junctions = []
    for program in net.findall('tlLogic'):
        jid = program.get('id')
        moves = [c for c in net.findall('connection') if c.get('tl') == jid]
        approaches = []
        for eid in sorted({c.get('from') for c in moves}):
            lanes = edges[eid].findall('lane')
            lengths = [float(l.get('length')) for l in lanes]
            approaches.append(dict(edge=eid, lanes=len(lanes), length_m=min(lengths)))
        greens = [p for p in program.findall('phase') if 'G' in p.get('state', '')
                  and 'y' not in p.get('state', '').lower()]
        junctions.append(dict(id=jid, arms=len(approaches), green_phases=len(greens),
                              approaches=approaches, turn_counts=dict(collections.Counter(c.get('dir') for c in moves))))
    tls_ids = {j['id'] for j in junctions}
    links = [dict(lanes=len(e.findall('lane')), length_m=min(float(l.get('length')) for l in e.findall('lane')))
             for e in edges.values() if e.get('from') in tls_ids and e.get('to') in tls_ids]
    # A collision in this coarse signature is conservatively excluded. Different
    # signatures alone do not establish a novel intersection or safe signal plan.
    normalized = dict(junctions=sorted([
        dict(arms=j['arms'], green_phases=j['green_phases'], turn_counts=j['turn_counts'],
             lane_lengths=sorted([a['lanes'], round(a['length_m'], 1)] for a in j['approaches']))
        for j in junctions], key=lambda x: json.dumps(x, sort_keys=True)),
        links=sorted([a['lanes'], round(a['length_m'], 1)] for a in links))
    return dict(signal_count=len(junctions), junctions=junctions, internal_links=links,
                screening_signature_sha256=canonical(normalized))


def build_history(root=ROOT):
    root = Path(root).resolve()
    release = read(root / RELEASE / 'release.json')
    profile = read(root / RELEASE / 'policy.json')
    model = read(root / RELEASE / 'model.json')
    training = model['training']
    frozen = {RELEASE + '/model.json': MODEL_SHA, RELEASE + '/policy.json': POLICY_SHA,
              profile['model']['path']: MODEL_SHA, 'configs/smart_signal_default.json': POLICY_SHA,
              **profile['source_sha256']}
    frozen.update({'transferable_signal/deployment.py': release['factory_sha256']})
    for name in ('signal_demo/release.py', 'transferable_signal/runner.py'):
        frozen[name] = digest(root / name)
    for name, expected in frozen.items():
        if digest(root / name) != expected:
            raise ValueError('Frozen source/model/policy changed: ' + name)
    if digest(root / BASE_SUITE) != training['suite_sha256']:
        raise ValueError('Training suite differs from exported training metadata')
    base = read(root / BASE_SUITE)
    scenarios = {s['id']: s for s in base['scenarios']}
    trained = [scenarios[name] for name in training['scenarios']]
    if any(s['split'] != 'train' for s in trained) or len(trained) != 21:
        raise ValueError('Training scenario lineage differs')
    train_cases = {s['case'] for s in trained}
    training_networks = []
    for case in base['cases']:
        if case['id'] not in train_cases:
            continue
        spec_path = root / 'network/transferable_v1' / case['spec']
        spec = read(spec_path)
        if digest(spec_path.parent / spec['network']) != spec['network_sha256']:
            raise ValueError('Training network changed: ' + case['id'])
        training_networks.append(spec['network_sha256'])
    if sorted(training_networks) != sorted(training['training_network_sha256']):
        raise ValueError('Training network metadata differs')
    for scenario in trained:
        path = root / 'network/transferable_v1' / scenario['routes']
        expected = training['frozen_demand'][scenario['id']]
        if digest(path) != expected or expected != scenario['routes_sha256']:
            raise ValueError('Training demand changed: ' + scenario['id'])
    suites, seeds, route_hashes, network_hashes = [], set(), set(training['frozen_demand'].values()), set(training_networks)
    for path in sorted((root / 'network').rglob('suite.json')):
        data = read(path)
        cases, demands = [], []
        for case in data.get('cases', []):
            record = reference_record(root, path.parent, case['spec'], case['spec_sha256'])
            if record['status'] == 'matched':
                spec_path = root / record['path']
                spec = read(spec_path)
                record['network'] = reference_record(root, spec_path.parent, spec['network'], spec['network_sha256'])
                network_hashes.add(spec['network_sha256'])
            cases.append(dict(id=case['id'], split=case['split'], **record))
        for scenario in data.get('scenarios', []):
            seeds.add(scenario['seed'])
            route_hashes.add(scenario['routes_sha256'])
            demands.append(dict(id=scenario['id'], case=scenario['case'], split=scenario['split'], seed=scenario['seed'],
                                **reference_record(root, path.parent, scenario['routes'], scenario['routes_sha256'])))
        seeds.update(data.get('exclusion', {}).get('demand_seeds', []))
        suites.append(dict(path=relative(root, path), sha256=digest(path),
                           preparation_status=data.get('holdout_status', data.get('preparation_status', 'prepared_only')),
                           cases=cases, scenarios=demands,
                           execution_proof='input files alone do not establish completed simulations'))
    networks = [dict(path=relative(root, path), sha256=digest(path), **network_description(path))
                for path in sorted((root / 'network').rglob('*.net.xml'))]
    network_hashes.update(n['sha256'] for n in networks)
    route_hashes.update(digest(p) for p in (root / 'network').rglob('*.rou.xml'))
    demos = []
    for path in sorted((root / 'results/runs').rglob('manifest.json')):
        data = read(path)
        if 'identity' not in data:
            continue
        identity = data['identity']
        if identity['model_sha256'] != MODEL_SHA or identity['policy_sha256'] != POLICY_SHA:
            raise ValueError('Showcase used a different policy: ' + str(path))
        demos.append(dict(path=relative(root, path), sha256=digest(path), status=data.get('status'),
                          controller=data.get('controller'), scenario=data.get('scenario'),
                          network_sha256=data.get('network_sha256'), identity_sha256=identity.get('identity_sha256')))
    evidence = [reference_record(root, root, name, expected) for name, expected in release['evidence_sha256'].items()]
    references = evidence + [c for s in suites for c in s['cases']] + [d for s in suites for d in s['scenarios']]
    references += [c['network'] for s in suites for c in s['cases'] if 'network' in c]
    mismatches = [r for r in references if r['status'] == 'mismatch']
    if mismatches:
        raise ValueError('Historical evidence hash mismatch: ' + json.dumps(mismatches))
    base_case = next(c for c in base['cases'] if c['id'] == 'gyeongju')
    showcase_spec_path = root / 'network/transferable_v1' / base_case['spec']
    showcase_spec = read(showcase_spec_path)
    showcase_net = showcase_spec_path.parent / showcase_spec['network']
    return dict(schema='unseen-transfer-history-v1', date='2026-10-28',
                audit_scope='local files and exported metadata; no historical raw-run re-audit',
                frozen_files_sha256=frozen,
                training=dict(seed=training['seed'], completed_episodes=training['completed_episodes'], updates=training['updates'],
                              metadata_suite_sha256=training['suite_sha256'], case_ids=sorted(train_cases),
                              scenario_ids=training['scenarios'], network_sha256=training_networks,
                              frozen_demand_entries=len(training['frozen_demand']),
                              frozen_demand_note='30 suite hashes include validation/test; only 21 listed train scenarios were selected by the training code'),
                suites=suites, networks=networks, showcase_manifests=demos,
                showcase=dict(network=relative(root, showcase_net), sha256=digest(showcase_net), **network_description(showcase_net)),
                prior_release_evidence=evidence,
                missing_evidence=[r for r in references if r['status'] == 'missing'],
                exclusions=dict(network_sha256=sorted(network_hashes), routes_sha256=sorted(route_hashes),
                                demand_seeds=sorted(seeds), screening_signature_sha256=sorted({n['screening_signature_sha256'] for n in networks})))


def protocol(history):
    profile = read(ROOT / RELEASE / 'policy.json')
    targets = [
        dict(id='zt_t1_short', shape='T', signal_count=1, arms='NES', lanes=dict(N=1, E=1, S=1),
             endpoint_distance_m=dict(N=150, E=330, S=210),
             novelty_axes=['topology', 'storage'],
             novelty=['three_arms_instead_of_four', 'one_signal_instead_of_three', 'short_storage']),
        dict(id='zt_x31_asymmetric', shape='X', signal_count=1, arms='NESW', lanes=dict(N=3, E=1, S=3, W=1),
             endpoint_distance_m=dict(N=320, E=140, S=260, W=180),
             novelty_axes=['lane_capacity', 'storage'],
             novelty=['three_lane_major_road', 'three_to_one_lane_ratio', 'short_storage']),
        dict(id='zt_pair_short', shape='two_connected_X', signal_count=2, junction_spacing_m=160,
             junctions=[dict(id='zt_a', lanes=dict(N=2, E=1, S=3, W=1)),
                        dict(id='zt_b', lanes=dict(N=3, E=1, S=2, W=1))],
             connector_lanes=2, outer_endpoint_distance_m=dict(north=360, south=300, east=180, west=220),
             novelty_axes=['topology', 'storage', 'lane_capacity'],
             novelty=['two_signals_instead_of_three', 'short_connector_storage', 'three_lane_major_road'])]
    profiles = ['quiet', 'busy', 'directional']
    seeds = {t['id']: {p: [280000 + i * 1000 + j * 100 + k for k in range(3)]
                        for j, p in enumerate(profiles)} for i, t in enumerate(targets)}
    planned = {s for values in seeds.values() for row in values.values() for s in row}
    if planned.intersection(history['exclusions']['demand_seeds']):
        raise ValueError('Reserved demand seeds overlap historical scenarios')
    return dict(schema='unseen-transfer-protocol-v1', status='frozen_before_environment_generation', date='2026-10-28',
                primary_question='Reuse the exact showcased model and all global rules, with zero additional training on unseen supported geometry',
                frozen_files_sha256=history['frozen_files_sha256'], model=RELEASE + '/model.json', policy=RELEASE + '/policy.json',
                seed=43, variant=profile['variant'], features=profile['features'], limits=profile['limits'],
                policy_config=profile['policy_config'], additional_training=0, target_specific_tuning=False,
                allowed_environment_changes=['physical lane/connection geometry', 'legal protected signal mapping', 'synthetic demand'],
                support=dict(arms=[3, 4], lanes_per_approach=[1, 2, 3], signal_count=[1, 2],
                             greens='fully protected G/r vehicle phases; no permissive g, pedestrians or conflicting streams',
                             acceptance='make_spec/load_spec, served-lane checks and movement/vehicle checks must pass; support of new geometry is not yet verified'),
                targets=targets, demand=dict(profiles=profiles, samples_per_profile=3, seeds=seeds,
                    vehicle_type_weights=dict(passenger=.8, bus=.1, truck=.1), duration_s=600, drain_s=300,
                    arrival_process='piecewise Poisson, frozen into explicit vehicles before comparing controllers',
                    base_arrival_vph_per_lane=dict(quiet=180, busy=360, directional=240),
                    directional='source-index parity alternates at 300s; matching half x2, other sources x0.5; route preference weight 3 vs 1',
                    generator='unchanged suite.demand (already source-frozen); freeze the new geometry builder in input manifest before any final run'),
                novelty_gate=dict(minimum_material_axes=2, cosmetic_changes_count=False,
                    max_short_incoming_lane_m=180, short_connector_lane_range_m=[100, 180],
                    showcase_min_incoming_lane_m=min(a['length_m'] for j in history['showcase']['junctions'] for a in j['approaches']),
                    require_no_historical_byte_or_screening_signature_duplicate=True,
                    note='endpoint distances are design inputs; novelty uses imported SUMO lane lengths. A failed geometry gate is recorded and resolved before performance inspection'),
                comparators=dict(primary=['fixed', 'webster', 'max_pressure', 'smart_v2'],
                    diagnostics=['raw_smart', 'rules_pressure'],
                    smart_v2='unchanged showcased AdaptiveSmartSignalV2 loaded from frozen release',
                    raw_smart='unchanged trained scorer under the identical SignalClock mask',
                    rules_pressure='same V2 decision flow with the raw learned score vector replaced by observation.pressure; keep normalization, thresholds, masks and tie-breaking unchanged'),
                budget=dict(pilot_runs_max=12, final_conditions=27, primary_runs=108, diagnostic_runs=54,
                            final_runs=162, total_runs_max=174, training_runs=0,
                            pilot_environment='existing valid_t2/valid_x3, one quiet scenario each, six controllers',
                            invocation_wall_seconds=2700, automatic_retry=False),
                metrics=dict(primary=['mean_queue_vehicles', 'arrived_vehicles', 'not_departed_vehicles', 'departed_not_arrived_vehicles'],
                    secondary=['max_queue_vehicles', 'p95_completed_waiting_time_s', 'mean_completed_waiting_time_s', 'mean_completed_travel_time_s', 'decision_latency', 'policy_mode_usage'],
                    stopped_vehicle_threshold_mps=.1, sample_interval_s=1, samples_per_final_run=900,
                    aggregation='arithmetic mean of per-run queue means over 27 paired conditions; arrivals summed over identical scheduled lists; case/profile groups reported separately',
                    fractional_change='(smart-baseline)/baseline; both zero -> 0, positive smart with zero baseline -> +infinity and failure',
                    uncertainty='show all 27 paired results and per-case/profile range over 3 traffic samples; no vehicle-level independent sample or universal/statistical-certainty claim'),
                criteria=dict(fixed_queue_change_max=-.05, webster_queue_change_max=-.05, max_pressure_queue_change_max=0.,
                    arrivals_nondecrease_vs=['fixed', 'webster', 'max_pressure'],
                    worst_case_mean_queue_change_vs_fixed_max=.10, worst_profile_mean_queue_change_vs_fixed_max=.10,
                    collision_vehicle_events_max=0, teleport_events_max=0,
                    learned_contribution=dict(comparison='smart_v2 vs rules_pressure', queue_change_max=-.02,
                                             arrivals_nondecrease=True, affects_primary_pass=False)),
                execution=dict(pairing='same network, explicit departure/route list, seed, 900-second horizon and SignalClock limits',
                    run_order=['fixed', 'webster', 'max_pressure', 'smart_v2', 'raw_smart', 'rules_pressure'],
                    stop_on=['execution error', 'collision', 'teleport', 'provenance mismatch', 'raw record inconsistency'],
                    warnings='preserve raw emergency-braking and other warning messages; investigate separately; never relabel as zero',
                    resume='only at a completed-run boundary with all frozen hashes unchanged',
                    final_feedback='no retraining/reselection/threshold changes; record failure, then use a new development and final set if improvement is pursued'),
                scope_boundary='this bundle finalizes history and test rules only; no new geometry, live traffic evaluation, web update or physical Pi validation is executed')


def prepare(output=DEFAULT_OUT, root=ROOT):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Refusing to overwrite an existing audit bundle')
    history = build_history(root)
    plan = protocol(history)
    output.mkdir(parents=True)
    for name, value in (('history.json', history), ('protocol.json', plan)):
        with (output / name).open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    seal = dict(schema='unseen-transfer-freeze-v1', date='2026-10-28',
                files_sha256={name: digest(output / name) for name in ('history.json', 'protocol.json')},
                audit_tool_sha256=digest(Path(__file__)), frozen_files_sha256=history['frozen_files_sha256'])
    with (output / 'freeze.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(seal, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    verify(output, root)
    return history


def verify(output=DEFAULT_OUT, root=ROOT):
    root, output = Path(root).resolve(), Path(output).resolve()
    seal = read(output / 'freeze.json')
    if seal['audit_tool_sha256'] != digest(Path(__file__)):
        raise ValueError('Audit tool changed after protocol freeze')
    for name, expected in seal['files_sha256'].items():
        path = output / name
        path.resolve().relative_to(output)
        if digest(path) != expected:
            raise ValueError('Frozen audit/protocol changed: ' + name)
    for name, expected in seal['frozen_files_sha256'].items():
        if digest(root / name) != expected:
            raise ValueError('Frozen model/rule/source changed: ' + name)
    plan = read(output / 'protocol.json')
    if plan['frozen_files_sha256'] != seal['frozen_files_sha256']:
        raise ValueError('Protocol source identities differ from seal')
    print('PASS: audit/protocol seal and', len(seal['frozen_files_sha256']), 'frozen model/rule/source files')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'verify'])
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.action == 'prepare':
        history = prepare(args.out)
        print('History:', len(history['suites']), 'suites;', len(history['networks']), 'networks;',
              len(history['showcase_manifests']), 'showcase manifests;', len(history['missing_evidence']), 'missing evidence references')
    else:
        verify(args.out)


if __name__ == '__main__':
    main()
