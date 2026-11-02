"""Fail-closed movement contract, rebuilt from the converted network without SUMO."""
import itertools
from pathlib import Path
import xml.etree.ElementTree as ET

import sumolib

from transferable_signal import FEATURES
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json, validate_limits
from . import VERSION

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / 'network/realistic_v2/gyeongju/intersection.json'


def validate_state(junction, state):
    movements = junction['movements']
    if len(state) != len(movements) or set(state) - set('Gyr'):
        raise ValueError('Unsupported movement state')
    # Yellow traffic may still enter. It must not conflict with a retained green.
    active = {i for i, color in enumerate(state) if color in 'Gy'}
    if any(a in active and b in active for a, b in junction['conflicts']):
        raise ValueError('Conflicting protected movements')
    for lane in {m['incoming'] for m in movements}:
        if len({state[m['index']] for m in movements if m['incoming'] == lane}) != 1:
            raise ValueError('Shared-lane movements have different signals')


def validate_junction(junction):
    movements, phases = junction['movements'], junction['phases']
    if [m['index'] for m in movements] != list(range(len(movements))):
        raise ValueError('Noncontiguous movement indices')
    if any(m['turn'] not in ('s', 'l', 'r') for m in movements):
        raise ValueError('Unsupported turn movement')
    if len(phases) < 2 or len({p['id'] for p in phases}) != len(phases) or len({p['state'] for p in phases}) != len(phases):
        raise ValueError('Duplicate or missing signal groups')
    for phase in phases:
        state = phase['state']
        if not set(state) <= set('Gr') or 'G' not in state:
            raise ValueError('Only protected green groups are supported')
        validate_state(junction, state)
        if phase['incoming'] != sorted({m['incoming'] for m in movements if state[m['index']] == 'G'}):
            raise ValueError('Group lanes differ from active movements')
    for m in movements:
        colors = {p['state'][m['index']] for p in phases}
        if colors != {'G', 'r'}:
            raise ValueError('Every movement must have service and a stopping group')


def make_spec(network, limits=None):
    network = Path(network)
    limits = dict(DEFAULT_LIMITS if limits is None else limits)
    validate_limits(limits)
    net = sumolib.net.readNet(str(network))
    tree = ET.parse(network).getroot()
    all_lanes = {lane.get('id'): lane for edge in tree.findall('edge') for lane in edge.findall('lane')}
    junctions, lanes = [], {}
    for program in tree.findall('tlLogic'):
        jid = program.get('id')
        if jid in {j['id'] for j in junctions}:
            raise ValueError('Duplicate signal program')
        links = sorted((c for c in tree.findall('connection') if c.get('tl') == jid), key=lambda c: int(c.get('linkIndex')))
        movements = [dict(index=int(c.get('linkIndex')), incoming=f"{c.get('from')}_{c.get('fromLane')}",
                          outgoing=f"{c.get('to')}_{c.get('toLane')}", turn=c.get('dir')) for c in links]
        internal = {}
        for lane, target, index in net.getTLS(jid).getConnections():
            connections = [c for c in lane.getOutgoing() if c.getToLane() == target]
            if len(connections) != 1:
                raise ValueError('Ambiguous internal connection')
            internal[index] = connections[0].getJunctionIndex()
        if set(internal) != set(range(len(movements))):
            raise ValueError('Missing internal right-of-way links')
        node = net.getNode(jid)
        conflicts = [[a, b] for a, b in itertools.combinations(range(len(movements)), 2)
                     if node.areFoes(internal[a], internal[b]) or node.areFoes(internal[b], internal[a])
                     or (movements[a]['outgoing'] == movements[b]['outgoing']
                         and movements[a]['incoming'] != movements[b]['incoming'])]
        phases = []
        for p in program.findall('phase'):
            state = p.get('state')
            if (p.get('name') or '').startswith('_transition_') or 'y' in state or 'G' not in state:
                continue
            phases.append(dict(id=p.get('name'), state=state,
                               incoming=sorted({m['incoming'] for m in movements if state[m['index']] == 'G'})))
        junction = dict(id=jid, phases=phases, movements=movements, conflicts=conflicts)
        validate_junction(junction)
        for p in program.findall('phase'):
            validate_state(junction, p.get('state'))
        junctions.append(junction)
        for m in movements:
            for key in ('incoming', 'outgoing'):
                lid = m[key]
                length = float(all_lanes[lid].get('length'))
                if length <= 0:
                    raise ValueError('Invalid lane length')
                lanes[lid] = dict(length_m=length, capacity_vehicles=length / limits['storage_m_per_vehicle'],
                                  edge=lid.rsplit('_', 1)[0])
    if len(junctions) != 3:
        raise ValueError('Realistic V2 currently supports the three Gyeongju junctions')
    return dict(version=VERSION, features=list(FEATURES), network=network.name, network_sha256=digest(network),
                limits=limits, lanes=lanes, junctions=junctions,
                right_turn_mode='protected_only', pedestrians=False, model_compatibility='experimental_transfer_unvalidated')


def load_spec(path=DEFAULT_SPEC):
    path = Path(path).resolve()
    spec = read_json(path)
    network = path.parent / spec['network']
    if make_spec(network, spec['limits']) != spec:
        raise ValueError('Realistic V2 network/contract mismatch')
    return spec, network
