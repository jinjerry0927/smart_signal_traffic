"""Prepare a frozen-policy Gyeongju comparison without advancing SUMO time."""
import argparse
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from transferable_signal import VERSION
from transferable_signal.spec import DEFAULT_LIMITS, digest
from transferable_signal.suite import catalog, demand, load_suite, xml_write
from tools.run_adaptive_holdout import freeze_selection
from tools.run_adaptive_wave import atomic_json

DEFAULT_OUTPUT = ROOT / 'network/adaptive_gyeongju_wave3_20261004'


def prepare(output=DEFAULT_OUTPUT):
    freeze = freeze_selection()
    if freeze['model']['seed'] != 43 or freeze['variant'] != 'load_gate_pressure_wait35':
        raise ValueError('The reviewed policy/model selection changed')
    source_path = ROOT / 'network/transferable_v1/suite.json'
    _, cases = load_suite(source_path)
    spec, network, case = cases['gyeongju']
    if (spec['limits'] != DEFAULT_LIMITS or case['split'] != 'test' or
            {item['id'] for item in spec['junctions']} != {'gj_yonggang', 'gj_geunhwa', 'gj_seungsam'}):
        raise ValueError('Source corridor junctions or safety limits changed')
    seen_seeds, seen_routes = set(), set()
    for path in (source_path, ROOT / 'network/transferable_reliability/suite.json',
                 ROOT / 'network/transferable_holdout_v2_20261004_retry01/suite.json'):
        previous, _ = load_suite(path)
        seen_seeds.update(item['seed'] for item in previous['scenarios'])
        seen_routes.update(item['routes_sha256'] for item in previous['scenarios'])
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    manifest = dict(version=VERSION, preparation_only=True, preparation_status='preparing',
                    selection_freeze=freeze, limits=dict(DEFAULT_LIMITS), cases=[], scenarios=[],
                    preparation_code_sha256=digest(Path(__file__)),
                    source_suite_sha256=digest(source_path), source_network_sha256=digest(network),
                    known_network_new_demand=True,
                    excluded_demand_seeds=sorted(seen_seeds), excluded_routes_sha256=sorted(seen_routes))
    atomic_json(output / 'suite.json', manifest)
    try:
        directory = output / 'gyeongju'
        directory.mkdir()
        shutil.copy2(network, directory / 'network.net.xml')
        shutil.copy2(source_path.parent / case['spec'], directory / 'intersection.json')
        manifest['cases'].append(dict(id='gyeongju', split='test', spec='gyeongju/intersection.json',
                                     spec_sha256=digest(directory / 'intersection.json')))
        routes = catalog(directory / 'network.net.xml')
        for index, profile in enumerate(('quiet', 'busy', 'directional')):
            for sample in range(2):
                seed = 76000 + index * 10 + sample
                if seed in seen_seeds:
                    raise ValueError('Demand seed was already used')
                name = f'{profile}_s{sample:02d}'
                route_path = directory / f'{name}.rou.xml'
                scheduled = demand(routes, profile, 600, seed, route_path)
                route_hash = digest(route_path)
                if route_hash in seen_routes:
                    raise ValueError('Demand schedule was already used')
                seen_seeds.add(seed)
                seen_routes.add(route_hash)
                manifest['scenarios'].append(dict(id=f'gyeongju_{name}', case='gyeongju', split='test',
                    profile=profile, sample=sample, seed=seed, scheduled=scheduled,
                    routes=f'gyeongju/{name}.rou.xml', routes_sha256=route_hash, duration_s=600, drain_s=300))
                configuration = ET.Element('configuration')
                inputs = ET.SubElement(configuration, 'input')
                ET.SubElement(inputs, 'net-file', value='network.net.xml')
                ET.SubElement(inputs, 'route-files', value=route_path.name)
                timing = ET.SubElement(configuration, 'time')
                ET.SubElement(timing, 'end', value='900')
                xml_write(directory / f'{name}.sumocfg', configuration)
        manifest['preparation_status'] = 'completed'
        atomic_json(output / 'suite.json', manifest)
        load_suite(output / 'suite.json')
        return manifest
    except BaseException as exc:
        manifest.update(preparation_status='failed', error=f'{type(exc).__name__}: {exc}')
        atomic_json(output / 'suite.json', manifest)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    result = prepare(parser.parse_args().out)
    print(f"PREPARED: 3 junctions / {len(result['scenarios'])} new demand schedules; no traffic run.")
