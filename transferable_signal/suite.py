"""Deterministic network/demand preparation. Never starts a simulation."""
import itertools
import math
import random
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import sumolib

from . import VERSION
from .spec import make_spec, load_spec, digest, read_json, write_json, validate_limits

ROOT = Path(__file__).resolve().parents[1]


def xml_write(path, root):
    ET.indent(root, space='  ')
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def star_network(case, directory, limits):
    arms, count, length = case['arms'], case['lanes'], case['length_m']
    if arms not in ('NES', 'NESW') or count not in (1, 2, 3) or length < 100:
        raise ValueError("Synthetic v1 requires NES/NESW, 1..3 lanes and length >= 100m")
    nodes, edges, turns, signals = [ET.Element(t) for t in ('nodes', 'edges', 'connections', 'tlLogics')]
    ET.SubElement(nodes, 'node', id='center', x='0', y='0', type='traffic_light', radius='12')
    coords = dict(N=(0, length), E=(length, 0), S=(0, -length), W=(-length, 0))
    for arm in arms:
        x, y = coords[arm]
        ET.SubElement(nodes, 'node', id=arm, x=str(x), y=str(y), type='priority', fringe='outer')
        for suffix, a, b in (('in', arm, 'center'), ('out', 'center', arm)):
            ET.SubElement(edges, 'edge', {'id': f'{arm}_{suffix}', 'from': a, 'to': b,
                          'numLanes': str(count), 'speed': '13.89', 'width': '3.2', 'allow': 'passenger bus truck'})
    groups, index = [], 0
    for arm in arms:
        position = 'NESW'.index(arm)
        targets = {turn: 'NESW'[(position + shift) % 4] for turn, shift in [('right', -1), ('straight', 2), ('left', 1)]}
        group = []
        for turn, target in targets.items():
            if target not in arms:
                continue
            if targets['straight'] in arms:
                lanes = range(count) if turn == 'straight' else [0 if turn == 'right' else count-1]
            elif count == 1:
                lanes = [0]
            else:
                lanes = range((count+1)//2) if turn == 'right' else range((count+1)//2, count)
            for lane in lanes:
                # A T branch has no straight feeder: its inner lanes receive the
                # protected left turn, while the outer lane receives the right turn.
                opposite = 'NESW'[('NESW'.index(target) + 2) % 4]
                receiving = range(1, count) if turn == 'left' and opposite not in arms and count > 1 else [lane]
                for destination_lane in receiving:
                    ET.SubElement(turns, 'connection', {'from': f'{arm}_in', 'to': f'{target}_out',
                                  'fromLane': str(lane), 'toLane': str(destination_lane), 'tl': 'center', 'linkIndex': str(index)})
                    group.append(index)
                    index += 1
        groups.append(group)
    program = ET.SubElement(signals, 'tlLogic', id='center', type='static', programID='0', offset='0')
    for arm, group in zip(arms, groups):
        for seconds, color in ((limits['fixed_green_s'], 'G'), (limits['yellow_s'], 'y'), (limits['all_red_s'], 'r')):
            ET.SubElement(program, 'phase', duration=str(seconds), name=f'{arm}_{color}',
                          state=''.join(color if i in group else 'r' for i in range(index)))
    filenames = ('nodes.nod.xml', 'roads.edg.xml', 'turns.con.xml', 'signals.tll.xml')
    for name, root in zip(filenames, (nodes, edges, turns, signals)):
        xml_write(directory / name, root)
    converted = subprocess.run([sumolib.checkBinary('netconvert'), '--node-files', str(directory / filenames[0]),
                    '--edge-files', str(directory / filenames[1]), '--connection-files', str(directory / filenames[2]),
                    '--tllogic-files', str(directory / filenames[3]), '--output-file', str(directory / 'network.net.xml'),
                    '--no-turnarounds', 'true', '--junctions.corner-detail', '8'], check=True, capture_output=True, text=True)
    (directory / 'netconvert.log').write_text(converted.stdout + converted.stderr, encoding='utf-8')
    xml_write(directory / 'network.net.xml', ET.parse(directory / 'network.net.xml').getroot())


def corridor_network(case, directory, limits):
    """Independent short synthetic corridor, not copied from held-out Gyeongju."""
    from tools.build_gyeongju_simple import build
    length = case['length_m']
    if length < 150 or case['lanes'] not in (1, 2, 3):
        raise ValueError("Invalid synthetic corridor geometry")
    layout = dict(green_s=limits['fixed_green_s'], yellow_s=limits['yellow_s'], all_red_s=limits['all_red_s'],
                  speed_mps=13.89, lane_width_m=3.2,
                  junctions=[dict(id=f'j{i}', label=f'Synthetic {i}', x=0, y=i*length) for i in range(3)], roads=[])
    for i in range(2):
        layout['roads'].append(dict(id=f'link{i}', a=f'j{i}', b=f'j{i+1}', arm_a='N', arm_b='S',
                                    lanes=case['lanes'], name='Synthetic connector'))
    for i in range(3):
        for arm in 'NESW':
            if (arm == 'N' and i < 2) or (arm == 'S' and i > 0):
                continue
            dx, dy = dict(N=(0,length), E=(length,0), S=(0,-length), W=(-length,0))[arm]
            name = f'j{i}_{arm}'
            layout['roads'].append(dict(id=name, a=f'j{i}', b=name, arm_a=arm, lanes=case['lanes'],
                                        end_xy=[dx,i*length+dy], name='Synthetic boundary'))
    write_json(directory / 'layout.json', layout)
    build(directory / 'layout.json', directory)
    shutil.copyfile(directory / 'simple.net.xml', directory / 'network.net.xml')


def catalog(network):
    net = sumolib.net.readNet(str(network))
    starts = [e for e in net.getEdges() if not e.getIncoming()]
    ends = [e for e in net.getEdges() if not e.getOutgoing()]
    routes = []
    for a, b in itertools.product(sorted(starts, key=lambda e: e.getID()), sorted(ends, key=lambda e: e.getID())):
        if a.getFromNode() == b.getToNode():
            continue
        path, _ = net.getShortestPath(a, b, vClass='passenger')
        if not path:
            raise ValueError(f"Disconnected boundary route: {a.getID()} -> {b.getID()}")
        if not all(lane.allows(kind) for edge in path for lane in edge.getLanes() for kind in ('bus', 'truck')):
            raise ValueError("Incompatible vehicle class permissions")
        routes.append(dict(id=f'od_{len(routes):03d}', edges=[e.getID() for e in path],
                           source=a.getID(), lanes=a.getLaneNumber()))
    if not routes:
        raise ValueError("No boundary routes")
    return routes


def demand(routes, profile, duration, seed, path):
    rng = random.Random(seed)
    root = ET.Element('routes')
    for kind, length in (('passenger', 5), ('bus', 12), ('truck', 12)):
        ET.SubElement(root, 'vType', id=kind, vClass=kind, length=str(length), maxSpeed='13.89')
    sources = {}
    for route in routes:
        ET.SubElement(root, 'route', id=route['id'], edges=' '.join(route['edges']))
        sources.setdefault(route['source'], []).append(route)
    vehicles = []
    for source_index, (source, choices) in enumerate(sorted(sources.items())):
        # Piecewise stationary Poisson inputs, frozen to explicit vehicles for every controller.
        for half in (0, 1):
            rate = (180 if profile == 'quiet' else 360 if profile == 'busy' else 240) * choices[0]['lanes']
            if profile == 'directional':
                rate *= 2 if source_index % 2 == half else .5
            time = half * duration / 2
            while True:
                time += rng.expovariate(rate / 3600)
                if time >= (half + 1) * duration / 2:
                    break
                weights = [3 if profile == 'directional' and i == half % len(choices) else 1 for i in range(len(choices))]
                route = rng.choices(choices, weights=weights)[0]
                kind = rng.choices(['passenger', 'bus', 'truck'], weights=[.8, .1, .1])[0]
                vehicles.append((time, route['id'], kind))
    for i, (time, route, kind) in enumerate(sorted(vehicles)):
        ET.SubElement(root, 'vehicle', id=f'veh_{i:05d}', type=kind, route=route, depart=f'{math.floor(time*1000)/1000:.3f}',
                      departLane='best', departSpeed='0')
    xml_write(path, root)
    return len(vehicles)


def prepare(config_path, output):
    config = read_json(config_path)
    if config['version'] != VERSION:
        raise ValueError("Unsupported suite version")
    validate_limits(config['limits'])
    if not isinstance(config['duration_s'], int) or config['duration_s'] <= 0 or not isinstance(config['drain_s'], int) or config['drain_s'] < 0:
        raise ValueError("Invalid episode duration")
    if not config['profiles'] or len(config['profiles']) != len(set(config['profiles'])) or set(config['profiles']) - {'quiet', 'busy', 'directional'}:
        raise ValueError("Unknown demand profile")
    cases = config['cases']
    if len({c['id'] for c in cases}) != len(cases) or len({c['seed'] for c in cases}) != len(cases):
        raise ValueError("Case IDs and seeds must be unique")
    for case in cases:
        if not case['id'].replace('_', '').isalnum() or case['split'] not in ('train', 'validation', 'test'):
            raise ValueError("Invalid case identifier or split")
        if 'source_network' in case and case['split'] != 'test':
            raise ValueError("Existing corridor is held out for final testing")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / 'source_config.json', config)
    manifest = dict(version=VERSION, cases=[], scenarios=[], preparation_only=True)
    network_hashes = set()
    for case in cases:
        directory = output / case['id']
        directory.mkdir()
        network = directory / 'network.net.xml'
        if 'source_network' in case:
            source = ROOT / case['source_network']
            shutil.copyfile(source, network)
            for name in ('view.xml', 'labels.add.xml'):
                if (source.parent / name).exists():
                    shutil.copyfile(source.parent / name, directory / name)
        elif case.get('layout') == 'corridor':
            corridor_network(case, directory, config['limits'])
        else:
            star_network(case, directory, config['limits'])
        spec = make_spec(network, config['limits'])
        if spec['network_sha256'] in network_hashes:
            raise ValueError("Duplicate network across suite cases")
        network_hashes.add(spec['network_sha256'])
        write_json(directory / 'intersection.json', spec)
        manifest['cases'].append(dict(id=case['id'], split=case['split'], spec=f"{case['id']}/intersection.json",
                                      spec_sha256=digest(directory / 'intersection.json')))
        routes = catalog(network)
        for index, profile in enumerate(config['profiles']):
            route_file = directory / f'{profile}.rou.xml'
            seed = case['seed'] * 10 + index
            count = demand(routes, profile, config['duration_s'], seed, route_file)
            scenario = dict(id=f"{case['id']}_{profile}", case=case['id'], split=case['split'], profile=profile,
                            routes=f"{case['id']}/{profile}.rou.xml", routes_sha256=digest(route_file),
                            scheduled=count, seed=seed, duration_s=config['duration_s'], drain_s=config['drain_s'])
            manifest['scenarios'].append(scenario)
            cfg = ET.Element('configuration')
            inputs = ET.SubElement(cfg, 'input')
            ET.SubElement(inputs, 'net-file', value='network.net.xml')
            ET.SubElement(inputs, 'route-files', value=route_file.name)
            time = ET.SubElement(cfg, 'time')
            ET.SubElement(time, 'end', value=str(config['duration_s'] + config['drain_s']))
            xml_write(directory / f'{profile}.sumocfg', cfg)
    write_json(output / 'suite.json', manifest)
    return manifest


def load_suite(path):
    path = Path(path).resolve()
    manifest = read_json(path)
    if manifest['version'] != VERSION:
        raise ValueError("Incompatible suite")
    cases = {}
    for case in manifest['cases']:
        spec_path = path.parent / case['spec']
        if digest(spec_path) != case['spec_sha256']:
            raise ValueError("Suite contract was modified")
        cases[case['id']] = (*load_spec(spec_path), case)
    for scenario in manifest['scenarios']:
        if scenario['split'] != cases[scenario['case']][2]['split']:
            raise ValueError("Scenario split differs from its network split")
        if digest(path.parent / scenario['routes']) != scenario['routes_sha256']:
            raise ValueError("Frozen demand was modified")
    return manifest, cases
