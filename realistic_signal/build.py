"""Offline conversion only. Never invokes sumo, sumo-gui, TraCI or a browser."""
import shutil
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

import sumolib

from transferable_signal.spec import digest, read_json, write_json
from transferable_signal.suite import xml_write
from .spec import ROOT, make_spec, load_spec

# Direction labels follow the repository's local approach contract (not bearings).
PLANS = {
    'gj_yonggang': ('N_combined', 'NS_through', 'S_combined', 'E_combined', 'W_combined'),
    'gj_geunhwa': ('NS_left', 'NS_through', 'E_combined', 'W_combined'),
    'gj_seungsam': ('S_combined', 'NS_through', 'N_combined', 'W_combined', 'E_combined'),
}


def check_routes(network, routes):
    net = sumolib.net.readNet(str(network))
    root = ET.parse(routes).getroot()
    for route in root.iter('route'):
        edges = [net.getEdge(e) for e in route.get('edges').split()]
        for first, second in zip(edges, edges[1:]):
            connections = first.getOutgoing().get(second, [])
            for kind in ('passenger', 'bus', 'truck'):
                if not any(c.getFromLane().allows(kind) and c.getToLane().allows(kind) for c in connections):
                    raise ValueError(f'Disconnected {kind} route: {first.getID()} -> {second.getID()}')


def build(output=ROOT / 'network/realistic_v2/gyeongju'):
    output = Path(output).resolve()
    # This builder deliberately has no overwrite switch.
    output.mkdir(parents=True, exist_ok=False)
    legacy = ROOT / 'network/transferable_v1/gyeongju'
    plain = ROOT / 'network/gyeongju_simple'
    old = read_json(legacy / 'intersection.json')
    inputs = {str(p.relative_to(ROOT)).replace('\\', '/'): digest(p) for p in
              [legacy / 'intersection.json', legacy / 'network.net.xml',
               *(plain / n for n in ('nodes.nod.xml', 'roads.edg.xml', 'turns.con.xml'))]}
    for name in ('nodes.nod.xml', 'roads.edg.xml'):
        shutil.copyfile(plain / name, output / name)
    connections = ET.parse(plain / 'turns.con.xml').getroot()
    programs = ET.Element('tlLogics')
    changes = []
    for j in old['junctions']:
        jid = j['id']
        arm_of_edge = {m['incoming'].rsplit('_', 1)[0]: p['id'][0] for p in j['phases']
                       for m in j['movements'] if p['state'][m['index']] == 'G'}
        original = {m['index']: m for m in j['movements']}
        links = [c for c in connections if c.get('tl') == jid]
        kept = []
        for c in links:
            movement = original[int(c.get('linkIndex'))]
            arm = arm_of_edge[c.get('from')]
            # Keep lane count and geometry; reserve inner main-road lane for left.
            if arm in 'NS' and c.get('fromLane') == '1' and movement['turn'] == 's':
                connections.remove(c)
                changes.append(dict(junction=jid, incoming=movement['incoming'], removed=movement,
                                    reason='inner main-road lane becomes protected-left only'))
            else:
                kept.append((c, arm, movement['turn']))
        for index, (c, _, _) in enumerate(kept):
            c.set('linkIndex', str(index))
        groups = []
        for name in PLANS[jid]:
            arms, kind = name.split('_')
            state = ''.join('G' if arm in arms and (kind == 'combined' or (turn == 'l' if kind == 'left' else turn in 'sr'))
                            else 'r' for _, arm, turn in kept)
            groups.append((name, state))
        program = ET.SubElement(programs, 'tlLogic', id=jid, type='static', programID='realistic-v2', offset='0')
        for i, (name, state) in enumerate(groups):
            target = groups[(i + 1) % len(groups)][1]
            # A fixed plan exported for inspection uses the same movement clearance.
            # 15s allows every overlapping movement to stay <=60s including clearances.
            ET.SubElement(program, 'phase', duration='15', state=state, name=name)
            yellow = ''.join('G' if a == b == 'G' else 'y' if a == 'G' else 'r' for a, b in zip(state, target))
            red = ''.join('G' if a == b == 'G' else 'r' for a, b in zip(state, target))
            ET.SubElement(program, 'phase', duration='3', state=yellow, name=f'_transition_{i}_yellow')
            ET.SubElement(program, 'phase', duration='2', state=red, name=f'_transition_{i}_clearance')
    xml_write(output / 'turns.con.xml', connections)
    xml_write(output / 'signals.tll.xml', programs)
    command = [sumolib.checkBinary('netconvert'), '--node-files', str(output / 'nodes.nod.xml'),
               '--edge-files', str(output / 'roads.edg.xml'), '--connection-files', str(output / 'turns.con.xml'),
               '--tllogic-files', str(output / 'signals.tll.xml'), '--output-file', str(output / 'network.net.xml'),
               '--offset.disable-normalization', 'true', '--no-turnarounds', 'true',
               '--junctions.corner-detail', '8', '--junctions.internal-link-detail', '8']
    converted = subprocess.run(command, check=True, capture_output=True, text=True)
    (output / 'netconvert.log').write_text(converted.stdout + converted.stderr, encoding='utf-8')
    xml_write(output / 'network.net.xml', ET.parse(output / 'network.net.xml').getroot())
    spec = make_spec(output / 'network.net.xml')
    write_json(output / 'intersection.json', spec)
    load_spec(output / 'intersection.json')
    suite = read_json(ROOT / 'network/transferable_v1/suite.json')
    scenarios = []
    for s in suite['scenarios']:
        if s['case'] != 'gyeongju':
            continue
        source = ROOT / 'network/transferable_v1' / s['routes']
        if digest(source) != s['routes_sha256']:
            raise ValueError('Frozen demand changed')
        inputs[source.relative_to(ROOT).as_posix()] = digest(source)
        target = output / source.name
        shutil.copyfile(source, target)
        check_routes(output / 'network.net.xml', target)
        scenarios.append(dict(s, id='realistic_v2_' + s['profile'], case='gyeongju', split='experimental',
                              routes=target.name, routes_sha256=digest(target)))
    for name in ('labels.add.xml', 'view.xml'):
        if (legacy / name).exists():
            shutil.copyfile(legacy / name, output / name)
    write_json(output / 'scenarios.json', dict(version='realistic-v2', scenarios=scenarios))
    write_json(output / 'build_manifest.json', dict(version='realistic-v2', input_sha256=inputs,
               generated_sha256={p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()},
               removed_connections=changes, lane_counts_changed=False, simulation_run=False))
    return spec


def load_scenarios(spec_path):
    spec_path = Path(spec_path)
    spec, network = load_spec(spec_path)
    manifest = read_json(spec_path.parent / 'build_manifest.json')
    for name, expected in manifest['generated_sha256'].items():
        if Path(name).name != name or digest(spec_path.parent / name) != expected:
            raise ValueError('Realistic V2 generated input changed: ' + name)
    scenarios = read_json(spec_path.parent / 'scenarios.json')['scenarios']
    for scenario in scenarios:
        routes = spec_path.parent / scenario['routes']
        if digest(routes) != scenario['routes_sha256']:
            raise ValueError('Realistic V2 demand hash mismatch')
        check_routes(network, routes)
    return spec, network, scenarios
