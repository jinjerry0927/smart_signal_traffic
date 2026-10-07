"""Unseen T/X geometries with frozen validated inference; no training."""
import argparse
import csv
import math
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import VERSION, runner
from transferable_signal.deployment import validated_policy
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json
from transferable_signal.suite import catalog, demand, load_suite
from tools.build_adaptive_holdout import build_skew
from tools import diversity_wave, run_adaptive_wave as common

GEOMETRIES = (
    dict(id='structure_t1', arms='NES', lanes=1, length_m=160,
         coords=dict(N=[-12,170], E=[300,15], S=[20,-230])),
    dict(id='structure_t2', arms='NES', lanes=2, length_m=420,
         coords=dict(N=[-25,450], E=[360,20], S=[25,-520])),
    dict(id='structure_t3', arms='NES', lanes=3, length_m=260,
         coords=dict(N=[-10,300], E=[450,-20], S=[20,-220])),
    dict(id='structure_x1', arms='NESW', lanes=1, length_m=480,
         coords=dict(N=[-15,470], E=[550,20], S=[25,-430], W=[-360,-20])),
    dict(id='structure_x2', arms='NESW', lanes=2, length_m=190,
         coords=dict(N=[-15,210], E=[280,10], S=[10,-180], W=[-160,-10])),
    dict(id='structure_x3', arms='NESW', lanes=3, length_m=360,
         coords=dict(N=[-20,380], E=[520,-15], S=[25,-310], W=[-420,15])),
)
PROFILES = ('quiet', 'busy', 'directional')
CONTROLLERS = ('fixed', 'webster', 'max_pressure', 'smart')
SAMPLES, SEED_BASE, EXPECTED_RUNS = 2, 80000, 144
BUILD_HELPER = ROOT / 'tools/build_adaptive_holdout.py'
TRACE_HELPER = Path(diversity_wave.__file__)


def plan():
    return dict(geometries=list(GEOMETRIES), profiles=list(PROFILES), samples=SAMPLES,
                seed_base=SEED_BASE, duration_s=600, drain_s=300, controllers=list(CONTROLLERS),
                criteria=dict(queue_change_fixed=-.05, arrivals_nondecrease=True,
                              worst_geometry_change=.10, worst_profile_change=.10))


def exclusions(policy):
    suites, networks, routes, seeds = {}, set(policy.data['training']['training_network_sha256']), set(), set()
    for path in sorted((ROOT / 'network').rglob('suite.json')):
        prior = read_json(path)
        suites[str(path.relative_to(ROOT))] = digest(path)
        routes.update(s['routes_sha256'] for s in prior.get('scenarios', []))
        seeds.update(s['seed'] for s in prior.get('scenarios', []))
    networks.update(digest(p) for p in (ROOT / 'network').rglob('*.net.xml'))
    routes.update(policy.data['training']['frozen_demand'].values())
    return dict(suites=suites, network_sha256=sorted(networks), routes_sha256=sorted(routes), seeds=sorted(seeds))


def prepare(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Preserve the existing prepared structure output')
    policy, _, identity = validated_policy()
    excluded = exclusions(policy)
    output.mkdir(parents=True)
    suite = dict(version=VERSION, preparation_only=True, preparation_status='preparing',
                 evaluation_kind='unseen virtual geometry / frozen validated model; no field data',
                 plan=plan(), limits=dict(DEFAULT_LIMITS), policy_identity=identity, exclusion=excluded,
                 preparer_sha256=digest(Path(__file__)), builder_sha256=digest(BUILD_HELPER),
                 cases=[], scenarios=[])
    common.atomic_json(output / 'suite.json', suite)
    try:
        seen_nets, seen_routes, seen_seeds = (set(excluded[key]) for key in ('network_sha256', 'routes_sha256', 'seeds'))
        for index, geometry in enumerate(GEOMETRIES):
            folder = output / geometry['id']
            folder.mkdir()
            network, spec = build_skew(geometry, folder, DEFAULT_LIMITS)
            if spec['network_sha256'] in seen_nets:
                raise ValueError('Structure duplicates a previous/training geometry')
            seen_nets.add(spec['network_sha256'])
            case = dict(id=geometry['id'], split='test', spec=f"{geometry['id']}/intersection.json",
                        spec_sha256=digest(folder / 'intersection.json'))
            suite['cases'].append(case)
            routes = catalog(network)
            for profile_index, profile in enumerate(PROFILES):
                for sample in range(SAMPLES):
                    seed = SEED_BASE + index * 100 + profile_index * 10 + sample
                    if seed in seen_seeds:
                        raise ValueError('Demand seed repeats a previous experiment')
                    name = f'{profile}_s{sample:02d}'
                    path = folder / f'{name}.rou.xml'
                    scheduled = demand(routes, profile, 600, seed, path)
                    hashed = digest(path)
                    if hashed in seen_routes:
                        raise ValueError('Demand schedule repeats a previous experiment')
                    seen_routes.add(hashed)
                    seen_seeds.add(seed)
                    suite['scenarios'].append(dict(id=f"{geometry['id']}_{name}", case=case['id'], split='test',
                        profile=profile, sample=sample, seed=seed, scheduled=scheduled, synthetic=True,
                        routes=f"{case['id']}/{name}.rou.xml", routes_sha256=hashed, duration_s=600, drain_s=300))
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
            or suite.get('policy_identity') != identity or suite.get('limits') != DEFAULT_LIMITS
            or suite.get('preparer_sha256') != digest(Path(__file__)) or suite.get('builder_sha256') != digest(BUILD_HELPER)):
        raise ValueError('Prepared structure, source, or frozen policy changed')
    if len(suite['cases']) != 6 or set(cases) != {g['id'] for g in GEOMETRIES} or len(suite['scenarios']) != 36:
        raise ValueError('Expected six unique geometries and 36 paired conditions')
    for relative, hashed in suite['exclusion']['suites'].items():
        if digest(ROOT / relative) != hashed:
            raise ValueError('Previous suite provenance changed')
    nets = set(suite['exclusion']['network_sha256'])
    for geometry in GEOMETRIES:
        spec, network, case = cases[geometry['id']]
        if (case['split'] != 'test' or spec['network_sha256'] in nets or spec['limits'] != DEFAULT_LIMITS
                or len(spec['junctions']) != 1 or len(spec['junctions'][0]['phases']) != len(geometry['arms'])
                or any(len(p['incoming']) != geometry['lanes'] for p in spec['junctions'][0]['phases'])):
            raise ValueError('Structure contamination, duplicate network, lanes, or phases differ')
        nodes = ET.parse(network.parent / 'nodes.nod.xml').getroot()
        actual = {n.get('id'): [float(n.get('x')), float(n.get('y'))] for n in nodes if n.get('id') != 'center'}
        if actual != geometry['coords']:
            raise ValueError('Generated approach coordinates differ')
        edges = ET.parse(network).getroot().findall('edge')
        ordinary = [e for e in edges if e.get('function') != 'internal']
        if len(ordinary) != 2 * len(geometry['arms']) or any(len(e.findall('lane')) != geometry['lanes'] for e in ordinary):
            raise ValueError('Generated edge/lane count differs')
        nets.add(spec['network_sha256'])
    seen, seeds, hashes = set(), set(suite['exclusion']['seeds']), set(suite['exclusion']['routes_sha256'])
    for scenario in suite['scenarios']:
        case_id, profile, sample = scenario['case'], scenario['profile'], scenario['sample']
        case_index = next(i for i,g in enumerate(GEOMETRIES) if g['id'] == case_id)
        if (profile not in PROFILES or type(sample) is not int or sample not in (0,1)
                or scenario['id'] != f'{case_id}_{profile}_s{sample:02d}' or scenario['id'] in seen
                or scenario['seed'] != SEED_BASE + case_index * 100 + PROFILES.index(profile) * 10 + sample
                or scenario['seed'] in seeds or scenario['routes_sha256'] in hashes
                or scenario['duration_s'] != 600 or scenario['drain_s'] != 300 or scenario['synthetic'] is not True):
            raise ValueError('Malformed, reused, or duplicated structure condition')
        runner._check_scenario(policy, scenario, cases[case_id][0], identity, suite)
        path = (suite_path.parent / scenario['routes']).resolve()
        if not path.is_relative_to(suite_path.parent):
            raise ValueError('Demand path escaped its suite')
        vehicles = ET.parse(path).getroot().findall('vehicle')
        if (not vehicles or len(vehicles) != scenario['scheduled'] or len({v.get('id') for v in vehicles}) != len(vehicles)
                or any(not 0 <= float(v.get('depart')) < 600 for v in vehicles)):
            raise ValueError('Explicit vehicle schedule is invalid')
        seen.add(scenario['id'])
        seeds.add(scenario['seed'])
        hashes.add(scenario['routes_sha256'])
    fingerprint = dict(suite_sha256=digest(suite_path), policy_identity=identity,
        driver_sha256=digest(Path(__file__)), builder_sha256=digest(BUILD_HELPER),
        helpers_sha256={p.name:digest(p) for p in (Path(common.__file__), TRACE_HELPER)},
        code_sha256={p.name:digest(p) for p in sorted((ROOT / 'transferable_signal').glob('*.py'))})
    fingerprint['code_sha256']['evaluation_support.py'] = digest(ROOT / 'evaluation_support.py')
    return suite, cases, fingerprint


def verify_record(output, record, scenarios, cases, fingerprint):
    row = record['row']
    key = (row['scenario'], row['controller'])
    path = (output / record['directory']).resolve()
    if key[0] not in scenarios or key[1] not in CONTROLLERS or not path.is_relative_to(output):
        raise ValueError('Saved result identity/path differs')
    scenario = scenarios[key[0]]
    actual, warnings = diversity_wave.verify_run(path, scenario, cases[scenario['case']][0], fingerprint, key[1])
    actual['case'] = scenario['case']
    if row != actual or record['warning_details'] != warnings:
        raise ValueError('Saved structure record differs from raw files')
    return key


def summarize(records):
    baseline = {(r['row']['scenario'],r['row']['controller']):r['row']
                for r in records if r['row']['controller'] in common.BASELINES}
    smart = [r['row'] for r in records if r['row']['controller'] == 'smart']
    result = common._summary(smart, baseline)
    result['geometries'] = {g['id']:common._summary([r for r in smart if r['case']==g['id']], baseline)
                            for g in GEOMETRIES}
    result['conditions'] = {r['scenario']:common._summary([r],baseline) for r in smart}
    worst = max(v['queue_change']['fixed'] for v in result['geometries'].values())
    criteria = plan()['criteria']
    result['worst_geometry_change_fixed'] = worst
    result['target_met'] = (result['queue_change']['fixed'] <= criteria['queue_change_fixed']
        and result['overall']['arrived_vehicles'] >= result['baselines']['fixed']['arrived_vehicles']
        and result['worst_profile_change'] <= criteria['worst_profile_change']
        and worst <= criteria['worst_geometry_change'])
    return result


def run_wave(suite_path, output, resume=False, max_wall_seconds=2700):
    if not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise ValueError('Wall-time limit must be finite and positive')
    started = time.monotonic()
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    suite, cases, fingerprint = preflight(suite_path)
    if output.exists():
        if not resume:
            raise FileExistsError('Preserve prior output; use exact unchanged resume')
        meta = read_json(output / 'manifest.json')
        if (meta.get('fingerprint') != fingerprint or meta.get('expected_runs') != EXPECTED_RUNS
                or meta.get('controllers') != list(CONTROLLERS) or meta.get('status') in ('failed','safety_hold')):
            raise ValueError('Resume source/policy/plan changed or failure needs inspection')
    else:
        if resume:
            raise FileNotFoundError('No existing structure batch')
        output.mkdir(parents=True)
        meta = dict(status='started', fingerprint=fingerprint, expected_runs=EXPECTED_RUNS,
                    controllers=list(CONTROLLERS), actual_new_runs=0, additional_training=0, records=[])
    records, completed = meta['records'], set()
    scenarios = {s['id']:s for s in suite['scenarios']}
    common.atomic_json(output / 'manifest.json',meta)
    try:
        for record in records:
            key = verify_record(output,record,scenarios,cases,fingerprint)
            if key in completed:
                raise ValueError('Duplicate saved structure result')
            completed.add(key)
        if meta['actual_new_runs'] != len(records):
            raise ValueError('Saved run counter differs')
        for scenario in suite['scenarios']:
            for controller in CONTROLLERS:
                key = (scenario['id'],controller)
                if key in completed:
                    continue
                if time.monotonic()-started >= max_wall_seconds:
                    meta['status']='checkpoint'
                    common.atomic_json(output / 'manifest.json',meta)
                    return meta
                preflight(suite_path)
                relative = Path(scenario['id']) / controller
                directory = output / relative
                meta.update(status='running',active_run=str(relative))
                common.atomic_json(output / 'manifest.json',meta)
                if not directory.exists():
                    runner.run(suite_path,scenario['id'],controller,directory,smart_policy='validated')
                row,warnings = diversity_wave.verify_run(directory,scenario,cases[scenario['case']][0],fingerprint,controller)
                row['case']=scenario['case']
                records.append(dict(directory=str(relative),row=row,warning_details=warnings))
                completed.add(key)
                meta['actual_new_runs']=len(records)
                meta.pop('active_run',None)
                common._write_comparison(output,records)
                common.atomic_json(output / 'manifest.json',meta)
                print(f"Structure {len(records)}/{EXPECTED_RUNS}: {scenario['id']}/{controller} queue={row['mean_queue_vehicles']:.3f} arrived={row['arrived_vehicles']}/{row['scheduled_vehicles']} braking={len(warnings)}",flush=True)
        meta.update(status='completed',summary=summarize(records))
        common.atomic_json(output / 'manifest.json',meta)
        return meta
    except BaseException as error:
        meta.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else
                    'safety_hold' if isinstance(error,common.SafetyHold) else 'failed',error=f'{type(error).__name__}: {error}')
        common.atomic_json(output / 'manifest.json',meta)
        raise


def verify(suite_path,output):
    suite,cases,fingerprint=preflight(suite_path)
    output=Path(output).resolve()
    meta=read_json(output / 'manifest.json')
    if (meta['status']!='completed' or meta['fingerprint']!=fingerprint or meta['actual_new_runs']!=EXPECTED_RUNS
            or meta['expected_runs']!=EXPECTED_RUNS or len(meta['records'])!=EXPECTED_RUNS):
        raise ValueError('Structure comparison incomplete or changed')
    scenarios={s['id']:s for s in suite['scenarios']}
    keys=[verify_record(output,r,scenarios,cases,fingerprint) for r in meta['records']]
    if len(set(keys))!=EXPECTED_RUNS or set(keys)!={(s,c) for s in scenarios for c in CONTROLLERS}:
        raise ValueError('Missing or duplicate paired comparison')
    if summarize(meta['records'])!=meta['summary']:
        raise ValueError('Aggregate differs from raw records')
    with (output / 'comparison.csv').open(encoding='utf-8-sig',newline='') as stream:
        rows=list(csv.DictReader(stream))
    if rows!=[{k:'' if v is None else str(v) for k,v in r['row'].items()} for r in meta['records']]:
        raise ValueError('CSV differs from raw metrics')
    return meta


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','run','verify'))
    parser.add_argument('--suite',type=Path,default=ROOT / 'network/structure_wave6_20261007/suite.json')
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--max-wall-seconds',type=float,default=2700)
    args=parser.parse_args()
    if args.action=='prepare':
        result=prepare(args.out)
        print(f"PREPARED: {len(result['cases'])} unseen geometries / {len(result['scenarios'])} schedules; no traffic/training")
    elif args.action=='run':
        result=run_wave(args.suite,args.out,args.resume,args.max_wall_seconds)
        print(f"Batch {result['status']}: {result['actual_new_runs']}/{EXPECTED_RUNS}; training=0")
    else:
        result=verify(args.suite,args.out)
        print(f"Raw verification PASS: {result['actual_new_runs']} runs; target_met={result['summary']['target_met']}")
