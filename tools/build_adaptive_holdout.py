"""Prepare two unseen skew junctions after freezing a successful validation policy.

Preparation calls netconvert and static checks, never TraCI or simulationStep.
"""
import argparse
import math
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sumolib
from transferable_signal import VERSION
from transferable_signal.spec import DEFAULT_LIMITS, digest, make_spec, read_json, write_json
from transferable_signal.suite import star_network, catalog, demand, load_suite, xml_write
from tools.run_adaptive_wave import atomic_json

DEFAULT_OUTPUT = ROOT / 'network/transferable_holdout_v2_20261004'
GEOMETRIES = (
    dict(id='holdout_t3_skew', arms='NES', lanes=3, length_m=340,
         coords=dict(N=[-30, 340], E=[440, 45], S=[70, -290])),
    dict(id='holdout_x1_skew', arms='NESW', lanes=1, length_m=310,
         coords=dict(N=[-45, 310], E=[470, -30], S=[35, -360], W=[-280, 55])),
)
PROFILES = ('quiet', 'busy', 'directional')
SAMPLES = 2
FIRST_SEED = 75000


def excluded_hashes(model_paths):
    networks, routes, seeds = set(), set(), set()
    for suite_path in (ROOT / 'network/transferable_v1/suite.json', ROOT / 'network/transferable_reliability/suite.json'):
        suite, cases = load_suite(suite_path)
        networks.update(spec['network_sha256'] for spec, _, _ in cases.values())
        routes.update(scenario['routes_sha256'] for scenario in suite['scenarios'])
        seeds.update(scenario['seed'] for scenario in suite['scenarios'])
    for path in model_paths:
        training = read_json(path)['training']
        networks.update(training['training_network_sha256'])
        routes.update(training['frozen_demand'].values())
    return networks, routes, seeds


def align_signal_indices(declared_connections, declared_signals, network):
    """Keep each intended movement's color when netconvert renumbers links.

Only signal states/linkIndex declarations change. Generated geometry, request
indices, foes, and response bits remain untouched for make_spec's safety audit.
The inputs are XML roots so regression tests do not need a live converter.
"""
    def movement(item):
        keys = ('from', 'to', 'fromLane', 'toLane')
        if any(item.get(key) is None for key in keys):
            raise ValueError('A declared/imported movement lacks its lane identity')
        return tuple(item.get(key) for key in keys)
    declared, imported = {}, {}
    for collection, items in ((declared, list(declared_connections)),
                               (imported, [item for item in network.findall('connection') if item.get('tl') == 'center'])):
        for item in items:
            if item.get('tl') != 'center':
                raise ValueError('Holdout movements must belong to the center signal')
            key = movement(item)
            if key in collection:
                raise ValueError('Duplicate declared/imported movement tuple')
            try:
                index = int(item.get('linkIndex'))
            except (TypeError, ValueError) as exc:
                raise ValueError('A declared/imported movement has an invalid signal index') from exc
            if index < 0 or index in {value[0] for value in collection.values()}:
                raise ValueError('Duplicate or invalid declared/imported signal index')
            collection[key] = (index, item)
    if not declared or set(declared) != set(imported):
        raise ValueError('Converted lane movements are missing or changed')
    count = len(declared)
    if ({value[0] for value in declared.values()} != set(range(count)) or
            {value[0] for value in imported.values()} != set(range(count))):
        raise ValueError('Declared/imported signal indices must be contiguous')
    mapping = {declared[key][0]: imported[key][0] for key in declared}
    programs = list(declared_signals)
    actual_programs = network.findall('tlLogic')
    if len(programs) != 1 or len(actual_programs) != 1 or programs[0].get('id') != 'center' or actual_programs[0].get('id') != 'center':
        raise ValueError('Holdout requires one declared/imported signal program')
    if any(programs[0].get(key) != actual_programs[0].get(key) for key in ('programID', 'offset')):
        raise ValueError('Converted signal program identity/offset changed')
    source_phases = programs[0].findall('phase')
    actual_phases = actual_programs[0].findall('phase')
    if len(source_phases) != len(actual_phases) or not source_phases:
        raise ValueError('Converted signal phases are missing or changed')
    aligned = []
    for source, actual in zip(source_phases, actual_phases):
        state = source.get('state', '')
        if len(state) != count or set(state) - {'G', 'y', 'r'}:
            raise ValueError('Invalid declared protected signal state')
        if actual.get('duration') != source.get('duration') or actual.get('name') != source.get('name'):
            raise ValueError('Converted signal phase duration/name changed')
        # netconvert currently preserves raw input colors before our correction.
        # Reject an unexpected converter transformation instead of guessing it.
        if actual.get('state') != state:
            raise ValueError('Converter already changed the declared signal state')
        reordered = ['r'] * count
        for old, new in mapping.items():
            reordered[new] = state[old]
        aligned.append(''.join(reordered))
    # Mutate only after every movement, program, and phase has been validated.
    for source, actual, state in zip(source_phases, actual_phases, aligned):
        source.set('state', state)
        actual.set('state', state)
    for key, (index, item) in declared.items():
        item.set('linkIndex', str(imported[key][0]))
    return mapping


def build_skew(case, directory, limits):
    """Reuse explicit movements, then change approach lengths and angles."""
    star_network(case, directory, limits)
    nodes_path = directory / 'nodes.nod.xml'
    nodes = ET.parse(nodes_path).getroot()
    for node in nodes:
        if node.get('id') == 'center':
            continue
        x, y = case['coords'][node.get('id')]
        if not all(math.isfinite(value) for value in (x, y)) or math.hypot(x, y) < 100:
            raise ValueError('Skew arm coordinates must be finite and at least 100m long')
        node.set('x', str(x))
        node.set('y', str(y))
    xml_write(nodes_path, nodes)
    result = subprocess.run([
        sumolib.checkBinary('netconvert'), '--node-files', str(nodes_path),
        '--edge-files', str(directory / 'roads.edg.xml'),
        '--connection-files', str(directory / 'turns.con.xml'),
        '--tllogic-files', str(directory / 'signals.tll.xml'),
        '--output-file', str(directory / 'network.net.xml'), '--no-turnarounds', 'true',
        '--junctions.corner-detail', '8'], check=True, capture_output=True, text=True)
    (directory / 'netconvert-skew.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if 'Error:' in result.stderr:
        raise ValueError('netconvert reported a skew-network error')
    network = directory / 'network.net.xml'
    imported = ET.parse(network).getroot()
    connections = ET.parse(directory / 'turns.con.xml').getroot()
    signals = ET.parse(directory / 'signals.tll.xml').getroot()
    mapping = align_signal_indices(connections, signals, imported)
    xml_write(directory / 'turns.con.xml', connections)
    xml_write(directory / 'signals.tll.xml', signals)
    xml_write(network, imported)
    write_json(directory / 'signal-index-map.json', dict(
        reason='Preserve movement colors after netconvert renumbers geometry-dependent TLS links',
        declared_to_imported={str(old): new for old, new in sorted(mapping.items())}))
    spec = make_spec(network, limits)
    expected = {(item.get('from'), item.get('to'), item.get('fromLane'), item.get('toLane'), item.get('linkIndex'))
                for item in ET.parse(directory / 'turns.con.xml').getroot()}
    actual = {(item.get('from'), item.get('to'), item.get('fromLane'), item.get('toLane'), item.get('linkIndex'))
              for item in imported.findall('connection') if item.get('tl') == 'center'}
    if actual != expected:
        raise ValueError('Skew conversion changed explicit lane connections')
    write_json(directory / 'intersection.json', spec)
    return network, spec


def prepare_holdout(output=DEFAULT_OUTPUT, selection_wave=None):
    from tools.run_adaptive_holdout import DEFAULT_SELECTION, freeze_selection
    selection_wave = DEFAULT_SELECTION if selection_wave is None else selection_wave
    freeze = freeze_selection(selection_wave)
    model_paths = [item['path'] for item in freeze['source_models']]
    networks, routes, seeds = excluded_hashes(model_paths)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = dict(version=VERSION, preparation_only=True, holdout_status='preparing',
                    selection_freeze=freeze, limits=dict(DEFAULT_LIMITS), cases=[], scenarios=[],
                    geometry=list(GEOMETRIES), samples_per_profile=SAMPLES,
                    exclusion=dict(network_sha256=sorted(networks), routes_sha256=sorted(routes), demand_seeds=sorted(seeds)),
                    preparation_code_sha256=digest(Path(__file__)))
    atomic_json(output / 'suite.json', manifest)
    try:
        for case_index, case in enumerate(GEOMETRIES):
            directory = output / case['id']
            directory.mkdir()
            network, spec = build_skew(case, directory, DEFAULT_LIMITS)
            network_hash = digest(network)
            if network_hash in networks:
                raise ValueError('Independent holdout network duplicates a seen or training network')
            networks.add(network_hash)
            manifest['cases'].append(dict(id=case['id'], split='test', spec=f"{case['id']}/intersection.json",
                                          spec_sha256=digest(directory / 'intersection.json')))
            route_catalog = catalog(network)
            for profile_index, profile in enumerate(PROFILES):
                for sample in range(SAMPLES):
                    seed = FIRST_SEED + case_index * 100 + profile_index * 10 + sample
                    if seed in seeds:
                        raise ValueError('Holdout demand seed duplicates a seen seed')
                    seeds.add(seed)
                    basename = f'{profile}_s{sample:02d}'
                    route_path = directory / f'{basename}.rou.xml'
                    scheduled = demand(route_catalog, profile, 600, seed, route_path)
                    route_hash = digest(route_path)
                    if route_hash in routes:
                        raise ValueError('Independent holdout demand duplicates a seen schedule')
                    routes.add(route_hash)
                    manifest['scenarios'].append(dict(id=f"{case['id']}_{basename}", case=case['id'], split='test',
                        profile=profile, sample=sample, routes=f"{case['id']}/{route_path.name}", routes_sha256=route_hash,
                        scheduled=scheduled, seed=seed, duration_s=600, drain_s=300))
                    configuration = ET.Element('configuration')
                    inputs = ET.SubElement(configuration, 'input')
                    ET.SubElement(inputs, 'net-file', value=network.name)
                    ET.SubElement(inputs, 'route-files', value=route_path.name)
                    clock = ET.SubElement(configuration, 'time')
                    ET.SubElement(clock, 'end', value='900')
                    xml_write(directory / f'{basename}.sumocfg', configuration)
        manifest['holdout_status'] = 'completed'
        atomic_json(output / 'suite.json', manifest)
        load_suite(output / 'suite.json')
        return manifest
    except BaseException as exc:
        manifest.update(holdout_status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'failed',
                        error=f'{type(exc).__name__}: {exc}')
        atomic_json(output / 'suite.json', manifest)
        raise


def main():
    from tools.run_adaptive_holdout import DEFAULT_SELECTION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--selection-wave', type=Path, default=DEFAULT_SELECTION)
    args = parser.parse_args()
    result = prepare_holdout(args.out, args.selection_wave)
    print(f"Prepared {len(result['cases'])} unseen networks / {len(result['scenarios'])} test schedules. No traffic run.")


if __name__ == '__main__':
    main()
