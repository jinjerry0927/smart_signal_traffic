"""Frozen validation resampling and user-run comparison of independent models."""
import csv
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from . import VERSION
from .model import SmartSignal
from .runner import run
from .spec import digest, write_json
from .suite import catalog, demand, load_suite, xml_write

BASELINES = ('fixed', 'webster', 'max_pressure')
PROFILES = {'quiet', 'busy', 'directional'}


def prepare_validation(source_suite, output, samples=5):
    """Copy validation geometry unchanged and freeze additional demand samples.

    Sample zero preserves the original departure/route schedule byte for byte.
    Other seeds start above every source-suite seed and are unique within the
    expansion. This function never opens a TraCI connection or advances SUMO.
    """
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
        raise ValueError("samples must be a positive integer")
    source_suite, output = Path(source_suite).resolve(), Path(output).resolve()
    source, cases = load_suite(source_suite)
    selected = [s for s in source['scenarios'] if s['split'] == 'validation']
    selected_cases = {s['case'] for s in selected}
    if not selected or len({s['id'] for s in selected}) != len(selected):
        raise ValueError("Source needs unique validation scenarios")
    for case_id in selected_cases:
        scenarios = [s for s in selected if s['case'] == case_id]
        if len(scenarios) != 3 or {s['profile'] for s in scenarios} != PROFILES:
            raise ValueError("Each source validation case needs exactly three demand profiles")
        if cases[case_id][2]['split'] != 'validation':
            raise ValueError("Validation scenario references another split")
    source_seeds = [s['seed'] for s in source['scenarios']]
    if any(not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 for seed in source_seeds):
        raise ValueError("Source demand seeds must be nonnegative integers")
    if len({s['seed'] for s in selected}) != len(selected):
        raise ValueError("Source validation demand seeds must be unique")
    additional_seed_base = max(source_seeds) + 1
    if additional_seed_base + len(selected) * (samples - 1) > 2**31 - 1:
        raise ValueError("Requested demand seeds exceed SUMO's signed integer range")
    for scenario in selected:
        for key, minimum in (('duration_s', 1), ('drain_s', 0)):
            value = scenario[key]
            if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
                raise ValueError("Invalid source scenario duration")
        count = len(ET.parse(source_suite.parent / scenario['routes']).findall('vehicle'))
        if count != scenario['scheduled']:
            raise ValueError("Source scheduled count differs from frozen vehicles")
    output.mkdir(parents=True, exist_ok=False)
    manifest = dict(version=VERSION, cases=[], scenarios=[], preparation_only=True,
                    validation_expansion=dict(source_suite_sha256=digest(source_suite),
                                              samples_per_profile=samples,
                                              additional_seed_base=additional_seed_base,
                                              sample_zero='Exact original frozen demand',
                                              status='preparing'))
    write_json(output / 'suite.json', manifest)
    try:
        for case in source['cases']:
            if case['id'] not in selected_cases:
                continue
            spec, network, _ = cases[case['id']]
            directory = output / case['id']
            directory.mkdir()
            shutil.copyfile(network, directory / network.name)
            target_spec = directory / 'intersection.json'
            shutil.copyfile(source_suite.parent / case['spec'], target_spec)
            if digest(directory / network.name) != spec['network_sha256'] or digest(target_spec) != case['spec_sha256']:
                raise ValueError("Validation copy changed its network or contract")
            manifest['cases'].append(dict(case, spec=f"{case['id']}/intersection.json"))
            routes = catalog(network)
            for source_index, scenario in enumerate(selected):
                if scenario['case'] != case['id']:
                    continue
                for sample in range(samples):
                    basename = f"{scenario['profile']}_s{sample:02d}"
                    route_file = directory / f'{basename}.rou.xml'
                    seed = scenario['seed'] if sample == 0 else additional_seed_base + source_index * (samples - 1) + sample - 1
                    if sample == 0:
                        shutil.copyfile(source_suite.parent / scenario['routes'], route_file)
                        scheduled = scenario['scheduled']
                    else:
                        scheduled = demand(routes, scenario['profile'], scenario['duration_s'], seed, route_file)
                    frozen = dict(scenario, id=f"{case['id']}_{basename}",
                                  routes=f"{case['id']}/{route_file.name}", routes_sha256=digest(route_file),
                                  seed=seed, scheduled=scheduled, sample=sample,
                                  source_scenario=scenario['id'])
                    manifest['scenarios'].append(frozen)
                    configuration = ET.Element('configuration')
                    inputs = ET.SubElement(configuration, 'input')
                    ET.SubElement(inputs, 'net-file', value=network.name)
                    ET.SubElement(inputs, 'route-files', value=route_file.name)
                    time = ET.SubElement(configuration, 'time')
                    ET.SubElement(time, 'end', value=str(scenario['duration_s'] + scenario['drain_s']))
                    xml_write(directory / f'{basename}.sumocfg', configuration)
        manifest['validation_expansion']['status'] = 'completed'
        write_json(output / 'suite.json', manifest)
        load_suite(output / 'suite.json')
        return manifest
    except BaseException as exc:
        manifest['validation_expansion'].update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                                                error=f'{type(exc).__name__}: {exc}')
        write_json(output / 'suite.json', manifest)
        raise


def compare_models(suite_path, model_paths, output):
    """Evaluate paired validation demand; run each baseline once per scenario.

    All model files and validation isolation are checked before output creation.
    TraCI runs begin only when a caller explicitly invokes this function.
    """
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    suite, cases = load_suite(suite_path)
    scenarios = suite['scenarios']
    if not scenarios or any(c['split'] != 'validation' for _, _, c in cases.values()) or any(s['split'] != 'validation' for s in scenarios):
        raise ValueError("Multi-model comparison accepts a validation-only suite")
    if len({s['id'] for s in scenarios}) != len(scenarios):
        raise ValueError("Duplicate validation scenario identifier")
    expansion = suite.get('validation_expansion')
    if expansion:
        samples = expansion.get('samples_per_profile')
        if expansion.get('status') != 'completed':
            raise ValueError("Validation preparation was not completed")
        if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
            raise ValueError("Invalid validation sample count")
        if {s['case'] for s in scenarios} != set(cases):
            raise ValueError("Validation cases and scenarios differ")
        for case_id in cases:
            for profile in PROFILES:
                found = [s for s in scenarios if s['case'] == case_id and s['profile'] == profile]
                if len(found) != samples or {s.get('sample') for s in found} != set(range(samples)):
                    raise ValueError("Validation samples are incomplete or duplicated")
        if len(scenarios) != len(cases) * len(PROFILES) * samples:
            raise ValueError("Unexpected validation demand profile")
    demand_seeds = [s['seed'] for s in scenarios]
    if any(not isinstance(seed, int) or isinstance(seed, bool) or seed < 0 for seed in demand_seeds) or len(set(demand_seeds)) != len(demand_seeds):
        raise ValueError("Validation demand seeds must be unique nonnegative integers")
    for scenario in scenarios:
        if len(ET.parse(suite_path.parent / scenario['routes']).findall('vehicle')) != scenario['scheduled']:
            raise ValueError("Validation scheduled count differs from frozen vehicles")
    if not model_paths:
        raise ValueError("Provide at least one trained model")
    paths = [Path(path).resolve() for path in model_paths]
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate model path")
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Model does not exist: {path}")
    validation_hashes = {spec['network_sha256'] for spec, _, _ in cases.values()}
    models, seeds, hashes = [], set(), set()
    for path in paths:
        policy = SmartSignal(path)
        training = policy.data['training']
        seed = training.get('seed')
        if not isinstance(seed, int) or isinstance(seed, bool):
            raise ValueError("Independent model comparison needs a recorded integer training seed")
        if seed in seeds:
            raise ValueError("Models must have distinct training seeds")
        trained_on = training.get('training_network_sha256')
        if not isinstance(trained_on, list) or not trained_on or not all(isinstance(value, str) for value in trained_on):
            raise ValueError("Model lacks training-network provenance")
        if validation_hashes.intersection(trained_on):
            raise ValueError("Validation network occurred in model training")
        model_hash = digest(path)
        if model_hash in hashes:
            raise ValueError("Duplicate model content")
        models.append(dict(label=f'smart_seed{seed}', seed=seed, path=str(path), sha256=model_hash,
                           updates=training['updates'], completed_episodes=training.get('completed_episodes')))
        seeds.add(seed)
        hashes.add(model_hash)
    output.mkdir(parents=True, exist_ok=False)
    metadata = dict(version=VERSION, status='started', split='validation',
                    suite_sha256=digest(suite_path), models=models, baselines=list(BASELINES),
                    expected_runs=len(scenarios) * (len(BASELINES) + len(models)), completed_runs=0,
                    scenario_count=len(scenarios),
                    frozen_demand={s['id']: dict(seed=s['seed'], sha256=s['routes_sha256']) for s in scenarios})
    write_json(output / 'manifest.json', metadata)
    writer = None
    try:
        with (output / 'comparison.csv').open('w', newline='', encoding='utf-8-sig') as stream:
            for scenario in scenarios:
                jobs = [(controller, None, dict(label=controller, seed=None)) for controller in BASELINES]
                jobs.extend(('smart', model['path'], model) for model in models)
                for controller, model_path, model in jobs:
                    run_number = metadata['completed_runs'] + 1
                    if run_number == 1 or run_number % 10 == 0 or run_number == metadata['expected_runs']:
                        print(f"Validation run {run_number}/{metadata['expected_runs']}: {scenario['id']} / {model['label']}", flush=True)
                    metadata['active_run'] = dict(scenario=scenario['id'], model_label=model['label'], controller=controller)
                    write_json(output / 'manifest.json', metadata)
                    result = run(suite_path, scenario['id'], controller,
                                 output / scenario['id'] / model['label'], model_path=model_path)
                    row = dict(result, scenario=scenario['id'], controller=controller,
                               model_label=model['label'], model_seed=model['seed'],
                               case=scenario['case'], profile=scenario['profile'],
                               sample=scenario.get('sample', 0), demand_seed=scenario['seed'])
                    if writer is None:
                        writer = csv.DictWriter(stream, fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow(row)
                    stream.flush()
                    metadata['completed_runs'] += 1
                    metadata.pop('active_run', None)
                    write_json(output / 'manifest.json', metadata)
        metadata['status'] = 'completed'
        write_json(output / 'manifest.json', metadata)
        return metadata
    except BaseException as exc:
        metadata.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                        error=f'{type(exc).__name__}: {exc}')
        write_json(output / 'manifest.json', metadata)
        raise
