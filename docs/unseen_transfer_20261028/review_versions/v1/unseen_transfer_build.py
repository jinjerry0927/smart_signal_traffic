"""Build the three preregistered networks without modifying frozen inference."""
import argparse
import importlib.util
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if importlib.util.find_spec('sumolib') is None:
    tools = Path(os.environ.get('SUMO_HOME', 'C:/Program Files (x86)/Eclipse/Sumo')) / 'tools'
    if tools.is_dir():
        sys.path.insert(0, str(tools))

import sumolib
from tools import audit_unseen_transfer as audit
from transferable_signal import VERSION
from transferable_signal.spec import digest, make_spec, read_json, write_json
from transferable_signal.suite import catalog, demand, load_suite, xml_write

BUNDLE = ROOT / 'docs/unseen_transfer_20261028'
DEFAULT_INPUT = ROOT / 'network/unseen_transfer_20261028'
ARMS = 'NESW'


def source_hashes():
    return {'tools/unseen_transfer_build.py': digest(Path(__file__))}


def layout(target):
    def boundary(jid, x, y, arm, lanes, length):
        dx, dy = dict(N=(0, length), E=(length, 0), S=(0, -length), W=(-length, 0))[arm]
        return dict(id=f'{jid}_{arm}', a=jid, b=f'{jid}_{arm}_end', arm_a=arm,
                    lanes=lanes, end_xy=[x + dx, y + dy])
    if target['signal_count'] == 1:
        jid = target['id']
        junctions = [dict(id=jid, x=0, y=0)]
        roads = [boundary(jid, 0, 0, arm, target['lanes'][arm], target['endpoint_distance_m'][arm])
                 for arm in target['arms']]
    else:
        distance = target['junction_spacing_m']
        junctions = [dict(id='zt_a', x=0, y=0), dict(id='zt_b', x=0, y=distance)]
        roads = [dict(id='zt_link', a='zt_a', b='zt_b', arm_a='N', arm_b='S', lanes=target['connector_lanes'])]
        for j, spec in zip(junctions, target['junctions']):
            for arm in ARMS:
                if (j['id'], arm) in [('zt_a', 'N'), ('zt_b', 'S')]:
                    continue
                key = dict(N='north', E='east', S='south', W='west')[arm]
                roads.append(boundary(j['id'], j['x'], j['y'], arm, spec['lanes'][arm],
                                      target['outer_endpoint_distance_m'][key]))
    return dict(junctions=junctions, roads=roads)


def align_signals(network, declared, programs):
    """Map colors by movement identity after netconvert renumbers TLS links."""
    mappings = {}
    for program in programs:
        jid = program.get('id')
        key = lambda c: tuple(c.get(k) for k in ('from', 'to', 'fromLane', 'toLane'))
        old = {key(c): int(c.get('linkIndex')) for c in declared if c.get('tl') == jid}
        imported = [c for c in network.findall('connection') if c.get('tl') == jid]
        new = {key(c): int(c.get('linkIndex')) for c in imported}
        if (len(old) != len(new) or set(old) != set(new)
                or set(old.values()) != set(range(len(old))) or set(new.values()) != set(range(len(new)))):
            raise ValueError('Converted lane movements differ: ' + jid)
        actual = network.find(f"tlLogic[@id='{jid}']")
        if actual is None or len(actual) != len(program):
            raise ValueError('Converted signal phases differ: ' + jid)
        mapping = {old[m]: new[m] for m in old}
        for source, destination in zip(program, actual):
            if any(source.get(k) != destination.get(k) for k in ('duration', 'name', 'state')):
                raise ValueError('Unexpected converter signal transformation: ' + jid)
            state = ['r'] * len(old)
            for before, after in mapping.items():
                state[after] = source.get('state')[before]
            source.set('state', ''.join(state))
            destination.set('state', ''.join(state))
        for connection in declared:
            if connection.get('tl') == jid:
                connection.set('linkIndex', str(new[key(connection)]))
        mappings[jid] = {str(k): v for k, v in sorted(mapping.items())}
    return mappings


def build_network(target, folder, limits):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    data = layout(target)
    write_json(folder / 'layout.json', data)
    junctions = {j['id']: j for j in data['junctions']}
    arms = {jid: {} for jid in junctions}
    nodes, edges, connections, programs = [ET.Element(tag) for tag in ('nodes', 'edges', 'connections', 'tlLogics')]
    for j in junctions.values():
        ET.SubElement(nodes, 'node', id=j['id'], x=str(j['x']), y=str(j['y']), type='traffic_light', radius='12')
    for road in data['roads']:
        a, b, count = road['a'], road['b'], road['lanes']
        if b not in junctions:
            ET.SubElement(nodes, 'node', id=b, x=str(road['end_xy'][0]), y=str(road['end_xy'][1]), type='priority', fringe='outer')
        for suffix, start, end in [('out', a, b), ('in', b, a)]:
            ET.SubElement(edges, 'edge', id=road['id'] + '_' + suffix, **{
                'from': start, 'to': end, 'numLanes': str(count), 'speed': '13.89', 'width': '3.2',
                'spreadType': 'right', 'allow': 'passenger bus truck'})
        arms[a][road['arm_a']] = dict(incoming=road['id'] + '_in', outgoing=road['id'] + '_out', lanes=count)
        if b in junctions:
            arms[b][road['arm_b']] = dict(incoming=road['id'] + '_out', outgoing=road['id'] + '_in', lanes=count)
    for jid, approaches in arms.items():
        groups = []
        for arm in ARMS:
            if arm not in approaches:
                continue
            incoming, group = approaches[arm], []
            for turn, shift in [('r', -1), ('s', 2), ('l', 1)]:
                destination = ARMS[(ARMS.index(arm) + shift) % 4]
                if destination not in approaches:
                    continue
                outgoing = approaches[destination]
                if turn == 's':
                    pairs = [(i, i) for i in range(min(incoming['lanes'], outgoing['lanes']))]
                elif turn == 'r':
                    pairs = [(0, 0)]
                else:
                    pairs = [(incoming['lanes'] - 1, outgoing['lanes'] - 1)]
                for source_lane, target_lane in pairs:
                    index = sum(len(g) for _, g in groups) + len(group)
                    ET.SubElement(connections, 'connection', **{'from': incoming['incoming'], 'to': outgoing['outgoing'],
                        'fromLane': str(source_lane), 'toLane': str(target_lane), 'tl': jid, 'linkIndex': str(index)})
                    group.append(index)
            groups.append((arm, group))
        count = sum(len(g) for _, g in groups)
        program = ET.SubElement(programs, 'tlLogic', id=jid, type='static', programID='0', offset='0')
        for arm, group in groups:
            for seconds, color in [(limits['fixed_green_s'], 'G'), (limits['yellow_s'], 'y'), (limits['all_red_s'], 'r')]:
                ET.SubElement(program, 'phase', duration=str(seconds), name=f'{arm}_{color}',
                              state=''.join(color if i in group else 'r' for i in range(count)))
    filenames = ['nodes.nod.xml', 'roads.edg.xml', 'turns.con.xml', 'signals.tll.xml']
    for name, tree in zip(filenames, [nodes, edges, connections, programs]):
        xml_write(folder / name, tree)
    network = folder / 'network.net.xml'
    command = [sumolib.checkBinary('netconvert'), '--node-files', str(folder / filenames[0]), '--edge-files', str(folder / filenames[1]),
               '--connection-files', str(folder / filenames[2]), '--tllogic-files', str(folder / filenames[3]),
               '--output-file', str(network), '--no-turnarounds', 'true', '--offset.disable-normalization', 'true',
               '--junctions.corner-detail', '8', '--junctions.internal-link-detail', '8']
    result = subprocess.run(command, capture_output=True, text=True)
    (folder / 'netconvert.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode or 'Error:' in result.stderr:
        raise ValueError('netconvert failed; preserve netconvert.log')
    imported = ET.parse(network).getroot()
    mappings = align_signals(imported, connections, programs)
    xml_write(folder / 'turns.con.xml', connections)
    xml_write(folder / 'signals.tll.xml', programs)
    xml_write(network, imported)
    write_json(folder / 'signal-index-map.json', mappings)
    spec = make_spec(network, limits)
    by_edge = {e.get('id'): e for e in imported.findall('edge') if e.get('function') != 'internal'}
    served = {m['incoming'] for j in spec['junctions'] for m in j['movements']}
    expected = {lane.get('id') for approaches in arms.values() for arm in approaches.values()
                for lane in by_edge[arm['incoming']].findall('lane')}
    if served != expected:
        raise ValueError('Some approach lanes have no legal movement')
    for j in spec['junctions']:
        if len(j['phases']) != len(arms[j['id']]):
            raise ValueError('Unexpected phase count')
    write_json(folder / 'intersection.json', spec)
    return network, spec


def check_novelty(target, network, history, plan):
    descriptor = audit.network_description(network)
    gate = plan['novelty_gate']
    if (digest(network) in history['exclusions']['network_sha256']
            or descriptor['screening_signature_sha256'] in history['exclusions']['screening_signature_sha256']):
        raise ValueError('New network duplicates a historical network')
    if descriptor['signal_count'] != target['signal_count'] or len(set(target['novelty_axes'])) < gate['minimum_material_axes']:
        raise ValueError('Topology novelty failed')
    lanes = [[a['lanes'] for a in j['approaches']] for j in descriptor['junctions']]
    minimum = min(a['length_m'] for j in descriptor['junctions'] for a in j['approaches'])
    if target['signal_count'] == 1:
        if minimum > gate['max_short_incoming_lane_m']:
            raise ValueError('Short approach storage gate failed')
        expected = sorted(target['lanes'].values())
        if sorted(lanes[0]) != expected:
            raise ValueError('Preregistered approach lanes differ')
    else:
        low, high = gate['short_connector_lane_range_m']
        if len(descriptor['internal_links']) != 2 or any(not low <= l['length_m'] <= high for l in descriptor['internal_links']):
            raise ValueError('Short connector storage gate failed')
        if sorted(sorted(v) for v in lanes) != sorted(sorted(j['lanes'].values()) for j in target['junctions']):
            raise ValueError('Preregistered pair lane configuration differs')
    return descriptor


def compatibility_routes(routes, path):
    root = ET.Element('routes')
    for kind, length in [('passenger', 5), ('bus', 12), ('truck', 12)]:
        ET.SubElement(root, 'vType', id=kind, vClass=kind, length=str(length), maxSpeed='13.89')
    for route in routes:
        ET.SubElement(root, 'route', id=route['id'], edges=' '.join(route['edges']))
    index = 0
    for route in routes:
        for kind in ['passenger', 'bus', 'truck']:
            ET.SubElement(root, 'vehicle', id=f'probe_{index:04d}', type=kind, route=route['id'],
                          depart=str(index * 2), departLane='best', departSpeed='0')
            index += 1
    xml_write(path, root)
    return index


def prepare(output=DEFAULT_INPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Input output exists; use a new path and preserve previous output')
    audit.verify()
    plan, history = read_json(BUNDLE / 'protocol.json'), read_json(BUNDLE / 'history.json')
    output.mkdir(parents=True)
    suite = dict(version=VERSION, limits=plan['limits'], cases=[], scenarios=[], preparation_status='preparing',
                 protocol_sha256=digest(BUNDLE / 'protocol.json'), builder_sha256=source_hashes(), novelty={})
    try:
        for target in plan['targets']:
            folder = output / target['id']
            network, spec = build_network(target, folder, plan['limits'])
            suite['novelty'][target['id']] = check_novelty(target, network, history, plan)
            suite['cases'].append(dict(id=target['id'], split='test', spec=f"{target['id']}/intersection.json",
                                       spec_sha256=digest(folder / 'intersection.json')))
            routes = catalog(network)
            write_json(folder / 'route-catalog.json', routes)
            compatibility_routes(routes, folder / 'compatibility.rou.xml')
            for profile in plan['demand']['profiles']:
                for sample, seed in enumerate(plan['demand']['seeds'][target['id']][profile]):
                    name = f'{profile}_s{sample:02d}'
                    path = folder / (name + '.rou.xml')
                    count = demand(routes, profile, 600, seed, path)
                    if digest(path) in history['exclusions']['routes_sha256']:
                        raise ValueError('Demand schedule reused from history')
                    scenario = dict(id=target['id'] + '_' + name, case=target['id'], split='test', profile=profile,
                                    sample=sample, seed=seed, routes=f'{target["id"]}/{path.name}', routes_sha256=digest(path),
                                    scheduled=count, duration_s=600, drain_s=300)
                    suite['scenarios'].append(scenario)
                    cfg = ET.Element('configuration')
                    inputs = ET.SubElement(cfg, 'input')
                    ET.SubElement(inputs, 'net-file', value='network.net.xml')
                    ET.SubElement(inputs, 'route-files', value=path.name)
                    ET.SubElement(ET.SubElement(cfg, 'time'), 'end', value='900')
                    xml_write(folder / (name + '.sumocfg'), cfg)
        suite['preparation_status'] = 'completed'
        write_json(output / 'suite.json', suite)
        fingerprint = {p.relative_to(output).as_posix(): digest(p) for p in sorted(output.rglob('*')) if p.is_file()}
        write_json(output / 'input-freeze.json', dict(files_sha256=fingerprint, builder_sha256=source_hashes(),
                   protocol_sha256=digest(BUNDLE / 'protocol.json')))
        preflight(output / 'suite.json')
        return suite
    except BaseException as error:
        write_json(output / 'preparation-failure.json', dict(status='failed', error=f'{type(error).__name__}: {error}'))
        raise


def preflight(suite_path=DEFAULT_INPUT / 'suite.json'):
    audit.verify()
    suite_path = Path(suite_path).resolve()
    frozen = read_json(suite_path.parent / 'input-freeze.json')
    if frozen['protocol_sha256'] != digest(BUNDLE / 'protocol.json') or frozen['builder_sha256'] != source_hashes():
        raise ValueError('Builder or preregistered protocol changed')
    for name, expected in frozen['files_sha256'].items():
        if digest(suite_path.parent / name) != expected:
            raise ValueError('Frozen new input changed: ' + name)
    suite, cases = load_suite(suite_path)
    plan, history = read_json(BUNDLE / 'protocol.json'), read_json(BUNDLE / 'history.json')
    if len(cases) != 3 or len(suite['scenarios']) != 27 or suite['limits'] != plan['limits']:
        raise ValueError('Expected exactly three cases and 27 conditions')
    for target in plan['targets']:
        spec, network, _ = cases[target['id']]
        if spec['limits'] != plan['limits'] or check_novelty(target, network, history, plan) != suite['novelty'][target['id']]:
            raise ValueError('Novelty or safety contract changed')
    expected = {(target['id'], profile, sample, seed) for target in plan['targets']
                for profile in plan['demand']['profiles'] for sample, seed in enumerate(plan['demand']['seeds'][target['id']][profile])}
    actual = {(s['case'], s['profile'], s['sample'], s['seed']) for s in suite['scenarios']}
    if actual != expected or len({s['id'] for s in suite['scenarios']}) != 27:
        raise ValueError('Demand conditions differ from protocol')
    for s in suite['scenarios']:
        route = suite_path.parent / s['routes']
        if digest(route) != s['routes_sha256'] or len(ET.parse(route).getroot().findall('vehicle')) != s['scheduled']:
            raise ValueError('Frozen vehicle schedule differs')
    return suite, cases


def zero_load(suite_path, output):
    suite, cases = preflight(suite_path)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    for s in suite['scenarios']:
        _, network, _ = cases[s['case']]
        result = subprocess.run([sumolib.checkBinary('sumo'), '-n', str(network), '-r', str(Path(suite_path).parent / s['routes']),
                                 '--begin', '0', '--end', '0', '--no-step-log', 'true'], capture_output=True, text=True)
        (output / (s['id'] + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode or 'Error:' in result.stderr:
            raise ValueError('SUMO zero-time load failed: ' + s['id'])
    write_json(output / 'summary.json', dict(status='PASS', zero_time_loads=27, traffic_runs=0))
    print('PASS: three novel networks, 27 zero-time SUMO loads')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'check', 'zero-load'])
    parser.add_argument('--inputs', type=Path, default=DEFAULT_INPUT)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.inputs)
        print('PASS: prepared', args.inputs)
    elif args.action == 'check':
        preflight(args.inputs / 'suite.json')
        print('PASS: frozen input compatibility')
    else:
        if args.out is None:
            parser.error('--out is required for zero-load')
        zero_load(args.inputs / 'suite.json', args.out)
