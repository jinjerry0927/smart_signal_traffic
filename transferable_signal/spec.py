"""Portable network contract, shared by every controller and topology."""
import hashlib
import itertools
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from . import VERSION, FEATURES

DEFAULT_LIMITS = dict(decision_s=5, min_green_s=15, max_green_s=60,
                      yellow_s=3, all_red_s=2, max_red_s=180,
                      fixed_green_s=25, arrival_window_s=60,
                      saturation_vph_per_lane=1800, storage_m_per_vehicle=7.5)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def validate_limits(limits):
    if set(limits) != set(DEFAULT_LIMITS):
        raise ValueError("Control options do not match the v1 schema")
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in limits.values()):
        raise ValueError("Control options must be finite and positive")
    for key, value in limits.items():
        if key.endswith('_s') and not isinstance(value, int):
            raise ValueError("Time options must be integer seconds")
    lo, hi, dt = (limits[k] for k in ('min_green_s', 'max_green_s', 'decision_s'))
    if not lo <= limits['fixed_green_s'] <= hi or any(v % dt for v in (lo, hi, limits['fixed_green_s'])):
        raise ValueError("Green times must fit min/max and decision grid")
    if (limits['yellow_s'] + limits['all_red_s']) % dt:
        raise ValueError("Clearance time must align with the decision grid")
    if limits['max_red_s'] <= hi:
        raise ValueError("max_red_s must exceed max_green_s")


def make_spec(network, limits=None):
    import sumolib

    network = Path(network)
    limits = dict(DEFAULT_LIMITS if limits is None else limits)
    validate_limits(limits)
    tree = ET.parse(network).getroot()
    net = sumolib.net.readNet(str(network))
    all_lanes = {lane.get('id'): lane for edge in tree.findall('edge') for lane in edge.findall('lane')}
    junctions, lane_data = [], {}
    for program in tree.findall('tlLogic'):
        jid = program.get('id')
        if any(j['id'] == jid for j in junctions):
            raise ValueError("v1 requires one signal program per junction")
        links = sorted((c for c in tree.findall('connection') if c.get('tl') == jid), key=lambda c: int(c.get('linkIndex')))
        if [int(c.get('linkIndex')) for c in links] != list(range(len(links))):
            raise ValueError("Signal link indices must be unique and contiguous")
        movements = [dict(index=int(c.get('linkIndex')), incoming=f"{c.get('from')}_{c.get('fromLane')}",
                          outgoing=f"{c.get('to')}_{c.get('toLane')}", turn=c.get('dir')) for c in links]
        internal = {}
        for lane, target, index in net.getTLS(jid).getConnections():
            internal[index] = next(c.getJunctionIndex() for c in lane.getOutgoing() if c.getToLane() == target)
        phases = []
        for p in program.findall('phase'):
            state = p.get('state')
            if 'y' in state.lower() or not any(s in 'Gg' for s in state):
                continue
            if len(state) != len(links) or set(state) - {'G', 'r'}:
                raise ValueError("v1 supports fully protected vehicle greens only")
            active = [i for i, s in enumerate(state) if s == 'G']
            node = net.getNode(jid)
            if any(node.areFoes(internal[a], internal[b]) or node.areFoes(internal[b], internal[a]) for a, b in itertools.combinations(active, 2)):
                raise ValueError(f"Conflicting protected greens: {jid}")
            for lane in {m['incoming'] for m in movements}:
                if len({state[m['index']] for m in movements if m['incoming'] == lane}) != 1:
                    raise ValueError("Shared-lane turns must receive the same signal")
            phases.append(dict(id=p.get('name', f'green_{len(phases)}'), state=state,
                               incoming=sorted({movements[i]['incoming'] for i in active})))
        if len(phases) < 2 or len({p['state'] for p in phases}) != len(phases):
            raise ValueError("At least two distinct green phases are required")
        if any(not any(p['state'][m['index']] == 'G' for p in phases) for m in movements):
            raise ValueError("Unserved signal connection")
        for m in movements:
            for key in ('incoming', 'outgoing'):
                lid = m[key]
                lane = all_lanes[lid]
                length = float(lane.get('length'))
                if length <= 0:
                    raise ValueError("Lane length must be positive")
                lane_data[lid] = dict(length_m=length, capacity_vehicles=length / limits['storage_m_per_vehicle'],
                                      edge=lid.rsplit('_', 1)[0])
        junctions.append(dict(id=jid, phases=phases, movements=movements))
    if not junctions:
        raise ValueError("No signalized junctions found")
    return dict(version=VERSION, features=list(FEATURES), network=network.name,
                network_sha256=digest(network), limits=limits, lanes=lane_data, junctions=junctions)


def load_spec(path):
    path = Path(path).resolve()
    spec = read_json(path)
    if spec['version'] != VERSION or spec['features'] != list(FEATURES):
        raise ValueError("Incompatible observation/control version")
    network = path.parent / spec['network']
    actual = make_spec(network, spec['limits'])
    if actual != spec:
        raise ValueError("Network and contract differ; regenerate the suite")
    return spec, network
