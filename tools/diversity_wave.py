"""Frozen-model screening on six synthetic traffic families; no training."""
import argparse
import csv
import math
from pathlib import Path
import random
import shutil
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import VERSION, runner
from transferable_signal.control import SignalClock
from transferable_signal.deployment import validated_policy
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json
from transferable_signal.suite import catalog, load_suite, xml_write
from tools import run_adaptive_wave as common

FAMILIES = ('sparse', 'wave', 'reversal', 'side_surge', 'left_bias', 'heavy_mix')
CONTROLLERS = ('fixed', 'webster', 'max_pressure', 'smart')
MAIN = ('yonggang_southwest_in', 'seungsam_north_in')
TLS = {'gj_yonggang', 'gj_geunhwa', 'gj_seungsam'}
SOURCE = ROOT / 'network/transferable_v1/suite.json'
SEED_BASE = 78000
DURATION, DRAIN = 600, 300


def plan():
    return dict(families=list(FAMILIES), samples=2, seed_base=SEED_BASE,
                duration_s=DURATION, drain_s=DRAIN, controllers=list(CONTROLLERS),
                base_vph_per_lane=dict(sparse=60, wave=[80, 360, 80, 360],
                                       reversal=[360, 90, 160], side_surge=[160, 360],
                                       left_bias=240, heavy_mix=240),
                ordinary_type_weights=[.8, .1, .1], heavy_type_weights=[.5, .25, .25],
                left_turn_weights=dict(l=.75, s=.15, r=.10),
                criteria=dict(mean_queue_change_fixed=-.05, arrivals_nondecrease=True,
                              worst_family_queue_change_fixed=.10))


def rate(family, source, segment):
    if family == 'sparse':
        return 60
    if family == 'wave':
        return (80, 360, 80, 360)[segment]
    if family == 'reversal':
        priority = MAIN[0 if segment < 2 else 1]
        return 360 if source == priority else 90 if source in MAIN else 160
    if family == 'side_surge':
        return 160 if source in MAIN else 360
    return 240


def turn_map(spec):
    turns = {}
    for junction in spec['junctions']:
        for movement in junction['movements']:
            pair = tuple(spec['lanes'][movement[key]]['edge'] for key in ('incoming', 'outgoing'))
            if pair in turns and turns[pair] != movement['turn']:
                raise ValueError('First-turn classification is ambiguous')
            turns[pair] = movement['turn']
    return turns


def make_demand(routes, spec, family, seed, path):
    if family not in FAMILIES:
        raise ValueError('Unknown demand family')
    rng = random.Random(seed)
    root = ET.Element('routes')
    for kind, length in (('passenger', 5), ('bus', 12), ('truck', 12)):
        ET.SubElement(root, 'vType', id=kind, vClass=kind, length=str(length), maxSpeed='13.89')
    sources, turns = {}, turn_map(spec)
    for route in routes:
        ET.SubElement(root, 'route', id=route['id'], edges=' '.join(route['edges']))
        sources.setdefault(route['source'], []).append(route)
    vehicles, details = [], dict(types={}, first_turns={}, sources={}, segments={})
    for source, choices in sorted(sources.items()):
        kinds = [turns[tuple(route['edges'][:2])] for route in choices]
        weights = ([plan()['left_turn_weights'][kind] / kinds.count(kind) for kind in kinds]
                   if family == 'left_bias' else [1] * len(choices))
        for segment in range(4):
            now = segment * 150
            frequency = rate(family, source, segment) * choices[0]['lanes'] / 3600
            while True:
                now += rng.expovariate(frequency)
                if now >= (segment + 1) * 150:
                    break
                route = rng.choices(choices, weights=weights)[0]
                kind = rng.choices(['passenger', 'bus', 'truck'],
                                   weights=plan()['heavy_type_weights'] if family == 'heavy_mix'
                                   else plan()['ordinary_type_weights'])[0]
                vehicles.append((math.floor(now * 1000) / 1000, route['id'], kind))
                for field, key in (('types', kind), ('first_turns', turns[tuple(route['edges'][:2])]),
                                   ('sources', source), ('segments', str(segment))):
                    details[field][key] = details[field].get(key, 0) + 1
    for index, (depart, route, kind) in enumerate(sorted(vehicles)):
        ET.SubElement(root, 'vehicle', id=f'veh_{index:05d}', type=kind, route=route,
                      depart=f'{depart:.3f}', departLane='best', departSpeed='0')
    xml_write(path, root)
    return len(vehicles), details


def prepare(output):
    policy, _, identity = validated_policy()
    _, cases = load_suite(SOURCE)
    spec, network, case = cases['gyeongju']
    runner._check_scenario(policy, dict(id='preflight', split='test'), spec, identity, {})
    if {j['id'] for j in spec['junctions']} != TLS or spec['limits'] != DEFAULT_LIMITS:
        raise ValueError('Expected the reviewed three-junction corridor')
    exclusion = {}
    seeds, hashes = set(), set()
    for path in sorted((ROOT / 'network').rglob('suite.json')):
        previous = read_json(path)
        exclusion[str(path.relative_to(ROOT))] = digest(path)
        seeds.update(s['seed'] for s in previous.get('scenarios', []))
        hashes.update(s['routes_sha256'] for s in previous.get('scenarios', []))
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Prepared output already exists; preserve it')
    output.mkdir(parents=True)
    suite = dict(version=VERSION, preparation_status='preparing', preparation_only=True,
                 evaluation_kind='synthetic new traffic / known schematic Gyeongju geometry',
                 plan=plan(), limits=dict(DEFAULT_LIMITS), policy_identity=identity,
                 preparer_sha256=digest(Path(__file__)), source_suite_sha256=digest(SOURCE),
                 exclusion_suites=exclusion, excluded_seeds=sorted(seeds), excluded_routes=sorted(hashes),
                 cases=[], scenarios=[])
    common.atomic_json(output / 'suite.json', suite)
    try:
        folder = output / 'gyeongju'
        folder.mkdir()
        shutil.copy2(network, folder / network.name)
        shutil.copy2(SOURCE.parent / case['spec'], folder / 'intersection.json')
        suite['cases'].append(dict(case, spec='gyeongju/intersection.json'))
        routes = catalog(network)
        for index, family in enumerate(FAMILIES):
            for sample in range(2):
                seed = SEED_BASE + index * 10 + sample
                if seed in seeds:
                    raise ValueError('Demand seed already used')
                name = f'{family}_s{sample:02d}'
                path = folder / f'{name}.rou.xml'
                count, details = make_demand(routes, spec, family, seed, path)
                hashed = digest(path)
                if hashed in hashes:
                    raise ValueError('Demand schedule already used')
                seeds.add(seed)
                hashes.add(hashed)
                suite['scenarios'].append(dict(id=f'gyeongju_{name}', case='gyeongju', split='test',
                    profile=family, sample=sample, seed=seed, scheduled=count, synthetic=True,
                    routes=f'gyeongju/{path.name}', routes_sha256=hashed, composition=details,
                    duration_s=DURATION, drain_s=DRAIN))
        suite['preparation_status'] = 'completed'
        common.atomic_json(output / 'suite.json', suite)
        preflight(output / 'suite.json')
        return suite
    except BaseException as error:
        suite.update(preparation_status='failed', error=f'{type(error).__name__}: {error}')
        common.atomic_json(output / 'suite.json', suite)
        raise


def preflight(suite_path):
    suite_path = Path(suite_path).resolve()
    suite, cases = load_suite(suite_path)
    policy, _, identity = validated_policy()
    if (suite.get('preparation_status') != 'completed' or suite.get('plan') != plan()
            or suite.get('policy_identity') != identity or suite.get('preparer_sha256') != digest(Path(__file__))
            or suite.get('source_suite_sha256') != digest(SOURCE)):
        raise ValueError('Prepared plan, code, source, or frozen policy changed')
    _, source_cases = load_suite(SOURCE)
    if set(cases) != {'gyeongju'} or len(suite['cases']) != 1 or len(suite['scenarios']) != 12:
        raise ValueError('Expected twelve paired traffic conditions on one corridor')
    spec, network, case = cases['gyeongju']
    source_spec, source_network, source_case = source_cases['gyeongju']
    if (spec != source_spec or digest(network) != digest(source_network)
            or case['spec_sha256'] != source_case['spec_sha256'] or suite.get('limits') != DEFAULT_LIMITS):
        raise ValueError('Network, contract, or safety limits changed')
    for relative, hashed in suite['exclusion_suites'].items():
        if digest(ROOT / relative) != hashed:
            raise ValueError('Previous demand provenance changed')
    seen_ids, seen_seeds, seen_hashes = set(), set(suite['excluded_seeds']), set(suite['excluded_routes'])
    for scenario in suite['scenarios']:
        family, sample = scenario['profile'], scenario['sample']
        if family not in FAMILIES or type(sample) is not int or sample not in (0, 1):
            raise ValueError('Unknown family/sample')
        expected = f'gyeongju_{family}_s{sample:02d}'
        if (scenario['id'] != expected or scenario['id'] in seen_ids or scenario['case'] != 'gyeongju'
                or scenario['seed'] != SEED_BASE + FAMILIES.index(family) * 10 + sample
                or scenario['seed'] in seen_seeds or scenario['routes_sha256'] in seen_hashes
                or scenario['synthetic'] is not True or scenario['duration_s'] != DURATION
                or scenario['drain_s'] != DRAIN):
            raise ValueError('Scenario identity, demand isolation, or horizon changed')
        runner._check_scenario(policy, scenario, spec, identity, suite)
        path = (suite_path.parent / scenario['routes']).resolve()
        if not path.is_relative_to(suite_path.parent):
            raise ValueError('Route path escaped the suite')
        vehicles = ET.parse(path).getroot().findall('vehicle')
        if (len(vehicles) != scenario['scheduled'] or not vehicles
                or len({v.get('id') for v in vehicles}) != len(vehicles)
                or any(not 0 <= float(v.get('depart')) < DURATION for v in vehicles)):
            raise ValueError('Invalid explicit vehicle schedule')
        seen_ids.add(scenario['id'])
        seen_seeds.add(scenario['seed'])
        seen_hashes.add(scenario['routes_sha256'])
    fingerprint = dict(suite_sha256=digest(suite_path), policy_identity=identity,
                       driver_sha256=digest(Path(__file__)), helper_sha256=digest(Path(common.__file__)),
                       code_sha256={p.name: digest(p) for p in sorted((ROOT / 'transferable_signal').glob('*.py'))})
    fingerprint['code_sha256']['evaluation_support.py'] = digest(ROOT / 'evaluation_support.py')
    return suite, cases, fingerprint


def verify_run(directory, scenario, spec, fingerprint, controller):
    identity = fingerprint['policy_identity']
    metrics, warnings = common.audit_run(directory, scenario, spec, fingerprint['suite_sha256'], controller,
        identity['model_sha256'] if controller == 'smart' else None, fingerprint['code_sha256'],
        adaptive_policy={k: identity[k] for k in ('version', 'variant', 'config')} if controller == 'smart' else None)
    if controller == 'smart' and read_json(directory / 'manifest.json').get('smart_policy') != identity:
        raise ValueError('Runtime deployment policy differs')
    with (directory / 'signals.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    step = spec['limits']['decision_s']
    clocks = {j['id']: SignalClock(j['phases'], spec['limits']) for j in spec['junctions']}
    expected = {(t, j) for t in range(step, DURATION + DRAIN + 1, step) for j in clocks}
    seen = set()
    for row in rows:
        key = (float(row['time_s']), row['junction'])
        if key in seen or key not in expected:
            raise ValueError('Missing, duplicate, or unexpected signal sample')
        clock = clocks[row['junction']]
        phase = int(row['phase'])
        clock.request(phase)
        for _ in range(step):
            clock.tick()
        if (row['stage'] != clock.stage or row['state'] != clock.state()
                or int(row['green_elapsed_s']) != clock.elapsed
                or not math.isfinite(float(row['queue_ratio']))):
            raise ValueError('Signal transition record differs from safety clock')
        seen.add(key)
    if seen != expected:
        raise ValueError('Signal trace is incomplete')
    return dict(metrics, profile=scenario['profile'], sample=scenario['sample'],
                emergency_braking_warnings=len(warnings)), warnings


def summarize(records):
    baseline = {(r['row']['scenario'], r['row']['controller']): r['row']
                for r in records if r['row']['controller'] in common.BASELINES}
    smart = [r['row'] for r in records if r['row']['controller'] == 'smart']
    summary = common._summary(smart, baseline)
    summary['conditions'] = {row['scenario']: common._summary([row], baseline) for row in smart}
    criteria = plan()['criteria']
    summary['target_met'] = (summary['queue_change']['fixed'] <= criteria['mean_queue_change_fixed']
        and summary['overall']['arrived_vehicles'] >= summary['baselines']['fixed']['arrived_vehicles']
        and summary['worst_profile_change'] <= criteria['worst_family_queue_change_fixed'])
    return summary


def run_wave(suite_path, output, resume=False, max_wall_seconds=2700):
    if not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise ValueError('Wall-time limit must be positive and finite')
    started = time.monotonic()
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    suite, cases, fingerprint = preflight(suite_path)
    if output.exists():
        if not resume:
            raise FileExistsError('Output exists; preserve it or resume unchanged inputs')
        meta = read_json(output / 'manifest.json')
        if (meta.get('fingerprint') != fingerprint or meta.get('controllers') != list(CONTROLLERS)
                or meta.get('expected_runs') != 48 or meta.get('status') in ('failed', 'safety_hold')):
            raise ValueError('Resume policy/inputs/code changed or failure requires inspection')
    else:
        if resume:
            raise FileNotFoundError('No batch to resume')
        output.mkdir(parents=True)
        meta = dict(status='started', fingerprint=fingerprint, controllers=list(CONTROLLERS),
                    expected_runs=48, records=[], actual_new_runs=0, additional_training=0,
                    evaluation_kind=suite['evaluation_kind'])
    records, completed = meta['records'], set()
    scenarios = {s['id']: s for s in suite['scenarios']}
    common.atomic_json(output / 'manifest.json', meta)
    try:
        for record in records:
            row = record['row']
            scenario = scenarios[row['scenario']]
            controller = row['controller']
            path = (output / record['directory']).resolve()
            if controller not in CONTROLLERS or not path.is_relative_to(output):
                raise ValueError('Saved result controller/path invalid')
            actual, warnings = verify_run(path, scenario, cases['gyeongju'][0], fingerprint, controller)
            key = (scenario['id'], controller)
            if row != actual or warnings != record['warning_details'] or key in completed:
                raise ValueError('Saved record differs from raw output or repeats a run')
            completed.add(key)
        for scenario in suite['scenarios']:
            for controller in CONTROLLERS:
                key = (scenario['id'], controller)
                if key in completed:
                    continue
                if time.monotonic() - started >= max_wall_seconds:
                    meta['status'] = 'checkpoint'
                    common.atomic_json(output / 'manifest.json', meta)
                    return meta
                preflight(suite_path)
                relative = Path(scenario['id']) / controller
                directory = output / relative
                meta.update(status='running', active_run=str(relative))
                common.atomic_json(output / 'manifest.json', meta)
                if not directory.exists():
                    runner.run(suite_path, scenario['id'], controller, directory, smart_policy='validated')
                row, warnings = verify_run(directory, scenario, cases['gyeongju'][0], fingerprint, controller)
                records.append(dict(directory=str(relative), row=row, warning_details=warnings))
                completed.add(key)
                meta.update(actual_new_runs=len(records))
                meta.pop('active_run', None)
                common._write_comparison(output, records)
                common.atomic_json(output / 'manifest.json', meta)
                print(f"Diversity {len(records)}/48: {scenario['id']}/{controller} queue={row['mean_queue_vehicles']:.3f} arrived={row['arrived_vehicles']}/{row['scheduled_vehicles']} braking={len(warnings)}", flush=True)
        meta.update(status='completed', summary=summarize(records))
        common.atomic_json(output / 'manifest.json', meta)
        return meta
    except BaseException as error:
        meta.update(status='interrupted' if isinstance(error, KeyboardInterrupt) else
                    'safety_hold' if isinstance(error, common.SafetyHold) else 'failed',
                    error=f'{type(error).__name__}: {error}')
        common.atomic_json(output / 'manifest.json', meta)
        raise


def verify(suite_path, output):
    suite, cases, fingerprint = preflight(suite_path)
    output = Path(output).resolve()
    meta = read_json(output / 'manifest.json')
    if (meta['status'] != 'completed' or meta['fingerprint'] != fingerprint
            or meta['expected_runs'] != 48 or meta['actual_new_runs'] != 48 or len(meta['records']) != 48):
        raise ValueError('Batch is not a complete unchanged 48-run comparison')
    scenarios = {s['id']: s for s in suite['scenarios']}
    seen = set()
    for record in meta['records']:
        row = record['row']
        key = (row['scenario'], row['controller'])
        path = (output / record['directory']).resolve()
        if key in seen or key[0] not in scenarios or key[1] not in CONTROLLERS or not path.is_relative_to(output):
            raise ValueError('Invalid paired result')
        actual, warnings = verify_run(path, scenarios[key[0]], cases['gyeongju'][0], fingerprint, key[1])
        if row != actual or record['warning_details'] != warnings:
            raise ValueError('Batch result differs from raw output')
        seen.add(key)
    if seen != {(s, c) for s in scenarios for c in CONTROLLERS} or summarize(meta['records']) != meta['summary']:
        raise ValueError('Paired coverage or aggregate differs')
    with (output / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != 48 or rows != [{k: '' if v is None else str(v) for k, v in r['row'].items()} for r in meta['records']]:
        raise ValueError('Comparison CSV differs from raw metrics')
    return meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'verify'))
    parser.add_argument('--suite', type=Path, default=ROOT / 'network/diversity_wave5_20261005/suite.json')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=float, default=2700)
    args = parser.parse_args()
    if args.action == 'prepare':
        result = prepare(args.out)
        print(f"PREPARED: {len(result['scenarios'])} new synthetic schedules; no simulation/training")
    elif args.action == 'run':
        result = run_wave(args.suite, args.out, args.resume, args.max_wall_seconds)
        print(f"Batch {result['status']}: {result['actual_new_runs']}/48; training=0")
    else:
        result = verify(args.suite, args.out)
        print(f"Raw verification PASS: {result['actual_new_runs']} runs; target_met={result['summary']['target_met']}")
