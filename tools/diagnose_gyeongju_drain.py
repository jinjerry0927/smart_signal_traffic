"""Read-only 1800-second drain diagnostics; preserve each original 900-second prefix.

The policy, demand, geometry, and signal limits stay frozen. Only the evaluation
end changes. Logging latency cannot be compared with original decision latency.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import shutil
import sys
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from tools import run_adaptive_gyeongju_wave as wave3
from tools import run_adaptive_holdout as holdout
from tools import run_adaptive_wave as common
from tools import run_adaptive_wave2 as wave2
from transferable_signal import runner
from transferable_signal.environment import SignalEnvironment
from transferable_signal.spec import digest, read_json
from transferable_signal.suite import load_suite

DEFAULT_REFERENCE = wave3.DEFAULT_OUTPUT
DEFAULT_OUTPUT = ROOT / 'results/runs/gyeongju_drain_wave4_20261004'
SELECTED = ('gyeongju_busy_s00', 'gyeongju_busy_s01', 'gyeongju_directional_s00', 'gyeongju_directional_s01')
HORIZON = 1800
SNAPSHOTS = (900, 1200, 1800)
LATENCY = {'mean_decision_ms_per_junction', 'p95_decision_ms_per_junction'}


def _snapshot_validate(snapshot, scheduled_ids):
    active = [item['id'] for item in snapshot['vehicles']]
    pending, arrived = snapshot['pending_ids'], snapshot['arrived_ids']
    groups = [set(active), set(pending), set(arrived)]
    if any(len(values) != len(group) for values, group in zip((active, pending, arrived), groups)):
        raise ValueError('Duplicate snapshot vehicle ID')
    if any(groups[a] & groups[b] for a, b in ((0, 1), (0, 2), (1, 2))) or set.union(*groups) != set(scheduled_ids):
        raise ValueError('Snapshot vehicle accounting is overlapping or incomplete')
    if snapshot['running'] != len(active) or snapshot['pending'] != len(pending) or snapshot['arrived'] != len(arrived):
        raise ValueError('Snapshot counters differ from vehicle ID lists')
    departed = snapshot['departed_ids']
    if len(set(departed)) != len(departed) or set(departed) != groups[0] | groups[2] or snapshot['departed'] != len(departed):
        raise ValueError('Snapshot departed IDs do not equal active and arrived IDs')
    for item in snapshot['vehicles']:
        if not item['lane'] or not item['edge'] or not 0 <= item['route_index'] < len(item['route']):
            raise ValueError('Snapshot vehicle location/route is incomplete')
        if any(not math.isfinite(item[field]) or item[field] < 0 for field in
               ('lane_position_m', 'speed_mps', 'current_continuous_wait_s')):
            raise ValueError('Snapshot vehicle observations are invalid')
    if snapshot['queued'] != sum(item['speed_mps'] < .1 for item in snapshot['vehicles']):
        raise ValueError('Snapshot queued count differs from vehicle speeds')


class DrainEnvironment(SignalEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.drain_streams = {}
        self.last_time = None
        self.snapshot_data = {}
        self.clear_time = None
        self.last_arrival_time = None
        self.previous_arrived = 0
        self.scheduled_ids = {item.get('id') for item in ET.parse(self.routes).getroot().findall('vehicle')}
        self.incoming = {j['id']: sorted({m['incoming'] for m in j['movements']}) for j in self.spec['junctions']}
        self.outgoing = {j['id']: sorted({m['outgoing'] for m in j['movements']}) for j in self.spec['junctions']}

    def _writer(self, name, fields):
        if name not in self.drain_streams:
            stream = (self.output / name).open('w', newline='', encoding='utf-8')
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            self.drain_streams[name] = stream, writer
        return self.drain_streams[name][1]

    def _snapshot(self, now, running, pending_ids):
        vehicle = self.connection.vehicle
        active_ids = list(vehicle.getIDList())
        items = []
        for vid in sorted(active_ids):
            items.append(dict(id=vid, lane=vehicle.getLaneID(vid), edge=vehicle.getRoadID(vid),
                              route=list(vehicle.getRoute(vid)), route_index=vehicle.getRouteIndex(vid),
                              lane_position_m=vehicle.getLanePosition(vid), speed_mps=vehicle.getSpeed(vid),
                              current_continuous_wait_s=vehicle.getWaitingTime(vid)))
        snapshot = dict(time_s=int(now), running=running, pending=len(pending_ids),
                        arrived=len(self.metrics.arrived), departed=len(self.metrics.departed),
                        arrived_ids=sorted(self.metrics.arrived), departed_ids=sorted(self.metrics.departed),
                        pending_ids=sorted(pending_ids), vehicles=items, queued=sum(item['speed_mps'] < .1 for item in items),
                        waiting_semantics='Current consecutive waiting only; not cumulative trip waiting or a diagnosed cause')
        _snapshot_validate(snapshot, self.scheduled_ids)
        common.atomic_json(self.output / f'snapshot_{int(now)}.json', snapshot)
        self.snapshot_data[int(now)] = snapshot

    def observe(self):
        result = super().observe()
        now = self.connection.simulation.getTime()
        if now == self.last_time:
            return result
        if not float(now).is_integer() or now != (0 if self.last_time is None else self.last_time + 1):
            raise ValueError('Diagnostic seconds are skipped or noninteger')
        running = self.connection.vehicle.getIDCount()
        pending_ids = list(self.connection.simulation.getPendingVehicles())
        queued = self.metrics.queues[-1] if now else 0
        waiting = self.metrics.waits[-1] if now else 0
        if len(self.metrics.arrived) > self.previous_arrived:
            self.last_arrival_time = int(now)
        if now >= 600 and running == 0 and not pending_ids and len(self.metrics.arrived) == self.scheduled:
            if self.clear_time is None:
                self.clear_time = int(now)
        self.previous_arrived = len(self.metrics.arrived)
        self._writer('seconds.csv', ['time_s', 'queued', 'system_current_wait_s', 'arrived', 'departed', 'running', 'pending']).writerow(
            dict(time_s=int(now), queued=queued, system_current_wait_s=waiting, arrived=len(self.metrics.arrived),
                 departed=len(self.metrics.departed), running=running, pending=len(pending_ids)))
        lane_writer = self._writer('lanes.csv', ['time_s', 'lane', 'edge', 'queued', 'vehicles', 'storage_capacity_proxy', 'density_proxy'])
        for lid, sample in self.observer.samples.items():
            capacity = self.spec['lanes'][lid]['capacity_vehicles']
            lane_writer.writerow(dict(time_s=int(now), lane=lid, edge=self.spec['lanes'][lid]['edge'],
                                     queued=sample['queue'], vehicles=sample['vehicles'],
                                     storage_capacity_proxy=capacity, density_proxy=sample['vehicles'] / capacity))
        junction_writer = self._writer('junctions.csv', ['time_s', 'junction', 'phase', 'stage', 'green_elapsed_s',
                                                        'incoming_queued', 'incoming_vehicles', 'downstream_storage_pressure_lanes'])
        for junction in self.spec['junctions']:
            jid, clock = junction['id'], self.clocks[junction['id']]
            if self.connection.trafficlight.getRedYellowGreenState(jid) != clock.state():
                raise common.SafetyHold('Recorded signal differs from unchanged Clock')
            marked = [lid for lid in self.outgoing[jid] if self.observer.samples[lid]['queue'] > 0 and
                      self.observer.samples[lid]['vehicles'] / self.spec['lanes'][lid]['capacity_vehicles'] >= .85]
            junction_writer.writerow(dict(time_s=int(now), junction=jid, phase=clock.current, stage=clock.stage,
                green_elapsed_s=clock.elapsed, incoming_queued=sum(self.observer.samples[lid]['queue'] for lid in self.incoming[jid]),
                incoming_vehicles=sum(self.observer.samples[lid]['vehicles'] for lid in self.incoming[jid]),
                downstream_storage_pressure_lanes=json.dumps(marked)))
        if now in SNAPSHOTS:
            self._snapshot(now, running, pending_ids)
        if self.metrics.teleports or self.metrics.collisions:
            raise common.SafetyHold(f'Unsafe event in drain diagnostic at {now}s')
        for filename in ('sumo-errors.log', 'sumo.log'):
            path = self.output / filename
            if path.exists():
                common.warning_signatures(path)
        self.last_time = now
        return result

    def close(self):
        try:
            super().close()
        finally:
            for stream, _ in self.drain_streams.values():
                if not stream.closed:
                    stream.close()
            if self.last_time is not None:
                common.atomic_json(self.output / 'drain_state.json', dict(
                    last_recorded_s=self.last_time, first_fully_cleared_after_inflow_s=self.clear_time,
                    last_observed_arrival_s=self.last_arrival_time,
                    limitation='Observation storage proxies flag possible downstream pressure; they do not establish causality. Logging invalidates latency comparisons.'))


def _warnings_until(path, seconds):
    warnings = common.warning_signatures(path)
    selected = []
    for warning in warnings:
        found = re.search(r'time=([0-9.]+)', warning)
        if not found:
            raise ValueError('A diagnostic warning lacks its simulation time')
        # SUMO's warning time labels the beginning of a step: time=900 is
        # generated in the first added step (900 -> 901), outside the prefix.
        if float(found.group(1).rstrip('.')) < seconds:
            selected.append(warning)
    return selected


def audit_prefix(directory, reference, scenario, spec):
    """Use the t900 arrived ID set, never a tripinfo arrival-time shortcut."""
    directory, reference = Path(directory), Path(reference)
    snapshot = read_json(directory / 'snapshot_900.json')
    with (directory / 'seconds.csv').open(encoding='utf-8', newline='') as stream:
        seconds = list(csv.DictReader(stream))
    if [int(row['time_s']) for row in seconds] != list(range(HORIZON + 1)):
        raise ValueError('Diagnostic second curve is incomplete')
    prefix = seconds[1:901]
    all_trips = ET.parse(directory / 'tripinfo.xml').getroot().findall('tripinfo')
    by_id = {trip.get('id'): trip for trip in all_trips}
    if len(by_id) != len(all_trips):
        raise ValueError('Duplicate diagnostic tripinfo ID')
    selected = [by_id[vid] for vid in snapshot['arrived_ids']]
    old_trips = [trip for trip in ET.parse(reference / 'tripinfo.xml').getroot().findall('tripinfo') if float(trip.get('arrival')) >= 0]
    if {trip.get('id') for trip in old_trips} != set(snapshot['arrived_ids']):
        raise ValueError('Diagnostic 900-second arrived set differs from original')
    if {trip.get('id'): trip.attrib for trip in old_trips} != {trip.get('id'): trip.attrib for trip in selected}:
        raise ValueError('Diagnostic 900-second completed trip attributes changed')
    mean = lambda values: sum(values) / len(values) if values else None
    waiting = [float(trip.get('waitingTime')) for trip in selected]
    actual = read_json(reference / 'metrics.json').copy()
    actual.update(mean_system_total_waiting_time_s=mean([float(row['system_current_wait_s']) for row in prefix]),
                  mean_queue_vehicles=mean([int(row['queued']) for row in prefix]), max_queue_vehicles=max(int(row['queued']) for row in prefix),
                  arrived_vehicles=snapshot['arrived'], departed_vehicles=snapshot['departed'],
                  scheduled_vehicles=scenario['scheduled'], not_departed_vehicles=scenario['scheduled'] - snapshot['departed'],
                  departed_not_arrived_vehicles=snapshot['departed'] - snapshot['arrived'],
                  running_vehicles_end=snapshot['running'], pending_vehicles_end=snapshot['pending'], sample_seconds=900,
                  mean_completed_waiting_time_s=mean(waiting),
                  mean_completed_travel_time_s=mean([float(trip.get('duration')) for trip in selected]),
                  mean_completed_time_loss_s=mean([float(trip.get('timeLoss')) for trip in selected]),
                  p95_completed_waiting_time_s=float(np.percentile(waiting, 95)) if waiting else None)
    old = read_json(reference / 'metrics.json')
    if any(not wave2._equal(actual.get(key), value) for key, value in old.items() if key not in LATENCY):
        raise ValueError('Diagnostic first-900 aggregate metrics differ from original')
    with (directory / 'signals.csv').open(encoding='utf-8', newline='') as stream:
        signals = [row for row in csv.DictReader(stream) if float(row['time_s']) <= 900]
    with (reference / 'signals.csv').open(encoding='utf-8', newline='') as stream:
        old_signals = list(csv.DictReader(stream))
    if signals != old_signals or len(signals) != 180 * len(spec['junctions']):
        raise ValueError('Diagnostic first-900 signal/queue-ratio trace changed')
    for filename in ('sumo-errors.log', 'sumo.log'):
        if _warnings_until(directory / filename, 900) != _warnings_until(reference / filename, 900):
            raise ValueError('Diagnostic first-900 warning signatures changed')
    actual.pop('mean_decision_ms_per_junction', None)
    actual.pop('p95_decision_ms_per_junction', None)
    common.atomic_json(directory / 'prefix900_metrics.json', actual)
    return actual


def _audit_trace(directory, spec, scheduled):
    """Check full observation coverage and accounting without replaying SUMO."""
    with (directory / 'seconds.csv').open(encoding='utf-8', newline='') as stream:
        seconds = list(csv.DictReader(stream))
    if [int(row['time_s']) for row in seconds] != list(range(HORIZON + 1)):
        raise ValueError('Diagnostic seconds are missing, duplicated, or unordered')
    previous_arrived, previous_departed = 0, 0
    for row in seconds:
        values = {key: int(row[key]) for key in ('queued', 'arrived', 'departed', 'running', 'pending')}
        wait = float(row['system_current_wait_s'])
        if any(value < 0 for value in values.values()) or not math.isfinite(wait) or wait < 0:
            raise ValueError('Invalid per-second counts/waiting')
        accounted = values['arrived'] + values['running'] + values['pending']
        if (values['arrived'] < previous_arrived or values['departed'] < previous_departed or
                accounted > scheduled or (int(row['time_s']) >= 600 and accounted != scheduled) or
                values['departed'] != values['arrived'] + values['running'] or values['queued'] > values['running']):
            raise ValueError('Per-second vehicle accounting changed')
        previous_arrived, previous_departed = values['arrived'], values['departed']
    at_snapshot = {}
    for name, id_field, expected in (('lanes.csv', 'lane', set(spec['lanes'])),
                                    ('junctions.csv', 'junction', {j['id'] for j in spec['junctions']})):
        current, seen = 0, set()
        snapshots = {}
        with (directory / name).open(encoding='utf-8', newline='') as stream:
            for row in csv.DictReader(stream):
                second = int(row['time_s'])
                if second != current:
                    if second != current + 1 or seen != expected:
                        raise ValueError(f'{name} has incomplete/unordered second coverage')
                    current, seen = second, set()
                identity = row[id_field]
                if identity not in expected or identity in seen:
                    raise ValueError(f'{name} contains unknown/duplicate observations')
                seen.add(identity)
                if name == 'lanes.csv':
                    queued, vehicles = int(row['queued']), int(row['vehicles'])
                    capacity, density = float(row['storage_capacity_proxy']), float(row['density_proxy'])
                    if (not 0 <= queued <= vehicles or row['edge'] != spec['lanes'][identity]['edge'] or
                            not wave2._equal(capacity, spec['lanes'][identity]['capacity_vehicles']) or
                            not wave2._equal(density, vehicles / capacity)):
                        raise ValueError('Lane count/storage proxy differs from its frozen contract')
                elif row['stage'] not in ('green', 'yellow', 'all_red') or any(int(row[key]) < 0 for key in
                        ('phase', 'green_elapsed_s', 'incoming_queued', 'incoming_vehicles')):
                    raise ValueError('Invalid per-junction observation')
                if second in SNAPSHOTS:
                    snapshots.setdefault(second, {})[identity] = row
        if current != HORIZON or seen != expected:
            raise ValueError(f'{name} ended before complete 1800-second coverage')
        at_snapshot[name] = snapshots
    return seconds, at_snapshot


def audit_drain_run(directory, scenario, controller, suite_path, freeze, reference, fingerprint, output):
    directory, suite_path = Path(directory), Path(suite_path)
    _, cases = load_suite(suite_path)
    spec = cases[scenario['case']][0]
    metrics, warnings = holdout._audit(directory, scenario, controller, spec,
        dict(suite_sha256=digest(suite_path), selection_freeze=freeze))
    run_manifest = read_json(directory / 'manifest.json')
    if (run_manifest.get('diagnostic_tool_sha256') != fingerprint['diagnostic_tool_sha256'] or
            run_manifest.get('latency_comparable') is not False):
        raise ValueError('Drain observation code/latency provenance changed')
    vehicles = ET.parse(suite_path.parent / scenario['routes']).getroot().findall('vehicle')
    scheduled_ids = {vehicle.get('id') for vehicle in vehicles}
    seconds, observations = _audit_trace(directory, spec, scenario['scheduled'])
    snapshots = {}
    for second in SNAPSHOTS:
        snapshot = read_json(directory / f'snapshot_{second}.json')
        _snapshot_validate(snapshot, scheduled_ids)
        row = seconds[second]
        if snapshot['time_s'] != second or any(snapshot[key] != int(row[key]) for key in
                ('arrived', 'departed', 'running', 'pending', 'queued')):
            raise ValueError('Snapshot time/counters differ from the second trace')
        by_lane, by_edge = {}, {}
        for vehicle in snapshot['vehicles']:
            by_lane[vehicle['lane']] = by_lane.get(vehicle['lane'], 0) + 1
            by_edge[vehicle['edge']] = by_edge.get(vehicle['edge'], 0) + 1
        snapshots[str(second)] = dict(arrived=snapshot['arrived'], running=snapshot['running'], pending=snapshot['pending'],
            queued=snapshot['queued'], active_by_lane=by_lane, active_by_edge=by_edge,
            junctions=observations['junctions.csv'][second], lanes=observations['lanes.csv'][second])
    prefix = audit_prefix(directory, reference, scenario, spec)
    state = read_json(directory / 'drain_state.json')
    clear = next((int(row['time_s']) for row in seconds if int(row['time_s']) >= 600 and
                  int(row['arrived']) == scenario['scheduled'] and int(row['running']) == int(row['pending']) == 0), None)
    arrivals = [int(row['time_s']) for previous, row in zip(seconds, seconds[1:]) if int(row['arrived']) > int(previous['arrived'])]
    if (state['last_recorded_s'] != HORIZON or state['first_fully_cleared_after_inflow_s'] != clear or
            state['last_observed_arrival_s'] != (arrivals[-1] if arrivals else None)):
        raise ValueError('Drain clear/arrival-time state differs from the raw second curve')
    planned = {vehicle.get('id'): float(vehicle.get('depart')) for vehicle in vehicles}
    trips = ET.parse(directory / 'tripinfo.xml').getroot().findall('tripinfo')
    late = [dict(id=trip.get('id'), scheduled_depart_s=planned[trip.get('id')], actual_depart_s=float(trip.get('depart')))
            for trip in trips if float(trip.get('depart')) > 600]
    raw_names = ('seconds.csv', 'lanes.csv', 'junctions.csv', 'drain_state.json', 'tripinfo.xml', 'signals.csv',
                 'sumo.log', 'sumo-errors.log', *(f'snapshot_{second}.json' for second in SNAPSHOTS))
    return dict(metrics1800=metrics, prefix900=prefix, snapshots=snapshots, drain_state=state,
                actual_departures_after_scheduled_inflow_cutoff=late, warning_details=warnings,
                raw_sha256={name: digest(directory / name) for name in raw_names},
                diagnostic_directory=str(directory.relative_to(output)))


def _preflight(source_suite, reference_wave):
    suite, cases, freeze, source = wave3._preflight(source_suite)
    reference_wave = Path(reference_wave).resolve()
    old = read_json(reference_wave / 'manifest.json')
    if old.get('status') != 'completed' or old.get('actual_new_runs') != 24 or old.get('expected_runs') != 24:
        raise ValueError('Drain diagnosis requires the completed 24-run reference')
    expected = dict(suite_sha256=digest(source_suite), selection_freeze=freeze, prepared_source=source,
                    driver_sha256=digest(ROOT / 'tools/run_adaptive_gyeongju_wave.py'), preparation_sha256=digest(wave3.PREPARER),
                    helper_sha256={name: digest(ROOT / 'tools' / name) for name in wave3.HELPERS},
                    criteria=dict(queue_improvement_fixed=.05, arrivals_nondecrease=True, worst_profile_change=.10))
    if old.get('fingerprint') != expected:
        raise ValueError('The original Gyeongju reference provenance changed')
    scenarios = {scenario['id']: scenario for scenario in suite['scenarios']}
    reference = {}
    for record in old['records']:
        row = record['row']
        scenario, controller = scenarios[row['scenario']], row['controller']
        path = (reference_wave / record['directory']).resolve()
        if not path.is_relative_to(reference_wave) or controller not in wave3.CONTROLLERS:
            raise ValueError('Invalid reference run path/controller')
        metrics, warnings = holdout._audit(path, scenario, controller, cases[scenario['case']][0], expected)
        if any(not wave2._equal(row.get(key), value) for key, value in dict(metrics, **holdout._labels(scenario, controller, freeze)).items()):
            raise ValueError('Reference row differs from original raw metrics')
        if record['warning_details'] != warnings or row['emergency_braking_warnings'] != len(warnings):
            raise ValueError('Reference warnings changed')
        key = (scenario['id'], controller)
        if key in reference:
            raise ValueError('Duplicate reference simulation')
        reference[key] = path
    if len(reference) != 24:
        raise ValueError('Missing reference simulations')
    with (reference_wave / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    indexed = {(row['scenario'], row['controller']): row for row in rows}
    if len(rows) != 24 or set(indexed) != set(reference) or any(
            not wave2._equal(indexed[(record['row']['scenario'], record['row']['controller'])].get(key), value)
            for record in old['records'] for key, value in record['row'].items()):
        raise ValueError('Reference CSV differs from its audited records')
    selected = [scenario for scenario in suite['scenarios'] if scenario['id'] in SELECTED]
    if len(selected) != 4:
        raise ValueError('Drain diagnostic cases are incomplete')
    fingerprint = dict(reference_manifest_sha256=digest(reference_wave / 'manifest.json'),
                       reference_comparison_sha256=digest(reference_wave / 'comparison.csv'),
                       source_suite_sha256=digest(source_suite), selection_freeze=freeze,
                       diagnostic_tool_sha256=digest(Path(__file__)),
                       source_driver_sha256=expected['driver_sha256'], helper_sha256=expected['helper_sha256'],
                       horizon_s=1800, inflow_s=600, snapshot_times=list(SNAPSHOTS),
                       prefix_comparison='Original aggregates/trip attrs/900 arrived IDs/5-second signals/warnings; old pointwise system queue was not saved',
                       latency_comparable=False)
    return suite, cases, freeze, selected, reference, fingerprint


def _prepare_input(output, source_suite, original, selected):
    destination = output / 'prepared_input'
    destination.mkdir()
    directory = destination / 'gyeongju'
    directory.mkdir()
    source_suite = Path(source_suite).resolve()
    spec_path = source_suite.parent / original['cases'][0]['spec']
    spec = read_json(spec_path)
    shutil.copyfile(spec_path, directory / 'intersection.json')
    shutil.copyfile(spec_path.parent / spec['network'], directory / spec['network'])
    scenarios = []
    for scenario in selected:
        shutil.copyfile(source_suite.parent / scenario['routes'], destination / scenario['routes'])
        scenarios.append(dict(scenario, drain_s=1200))
    prepared = dict(original, scenarios=scenarios, drain_diagnostic=True, original_suite_sha256=digest(source_suite),
                    duration_changed_only='drain_s: 300 to 1200; all geometry/demand/model/policy/Clock inputs unchanged')
    common.atomic_json(destination / 'suite.json', prepared)
    load_suite(destination / 'suite.json')
    return destination / 'suite.json'


def run_drain_wave(output=DEFAULT_OUTPUT, source_suite=wave3.DEFAULT_SUITE, reference_wave=DEFAULT_REFERENCE,
                   resume=False, max_wall_seconds=2700):
    from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2
    if not isinstance(max_wall_seconds, (float, int)) or isinstance(max_wall_seconds, bool) or not math.isfinite(max_wall_seconds) or max_wall_seconds <= 0:
        raise ValueError('max_wall_seconds must be finite and positive')
    started = time.monotonic()
    output, source_suite = Path(output).resolve(), Path(source_suite).resolve()
    original, cases, freeze, selected, reference, fingerprint = _preflight(source_suite, reference_wave)
    manifest_path = output / 'manifest.json'
    if output.exists():
        if not resume:
            raise FileExistsError('Drain output exists; resume unchanged inputs or preserve it separately')
        metadata = read_json(manifest_path)
        if metadata.get('fingerprint') != fingerprint or metadata.get('status') in ('failed', 'safety_hold'):
            raise ValueError('Drain resume input/code changed or previous run requires safety review')
        suite_path = output / 'prepared_input/suite.json'
    else:
        if resume:
            raise FileNotFoundError('No drain diagnosis to resume')
        output.mkdir(parents=True)
        suite_path = _prepare_input(output, source_suite, original, selected)
        metadata = dict(status='started', fingerprint=fingerprint, records=[], actual_new_runs=0,
                        started_run_attempts=0, expected_runs=16, prepared_suite_sha256=digest(suite_path))
        common.atomic_json(manifest_path, metadata)
    prepared, prepared_cases = load_suite(suite_path)
    if digest(suite_path) != metadata['prepared_suite_sha256'] or prepared['scenarios'] != [dict(scenario, drain_s=1200) for scenario in selected]:
        raise ValueError('Diagnostic prepared horizon or copied inputs changed')
    completed = {}
    def verify(directory, scenario, controller):
        return audit_drain_run(directory, scenario, controller, suite_path, freeze,
                               reference[(scenario['id'], controller)], fingerprint, output)
    try:
        if metadata.get('expected_runs') != 16 or len(metadata['records']) > 16:
            raise ValueError('Drain run cardinality changed')
        for record in metadata['records']:
            scenario = next(item for item in prepared['scenarios'] if item['id'] == record['scenario'])
            directory = (output / record['diagnostic_directory']).resolve()
            if not directory.is_relative_to(output) or record['controller'] not in wave3.CONTROLLERS:
                raise ValueError('Invalid saved drain run path/controller')
            actual = verify(directory, scenario, record['controller'])
            if not holdout.same_structure({key: record[key] for key in actual}, actual):
                raise ValueError('Saved drain result differs from audited raw diagnostics')
            key = (scenario['id'], record['controller'])
            if key in completed:
                raise ValueError('Duplicate saved drain run')
            completed[key] = record
        metadata['actual_new_runs'] = len(metadata['records'])
        for scenario in prepared['scenarios']:
            for controller in wave3.CONTROLLERS:
                key = (scenario['id'], controller)
                if key in completed:
                    continue
                if time.monotonic() - started >= max_wall_seconds:
                    raise common.WallTimeCheckpoint('Drain completed-run boundary wall-time checkpoint')
                relative = Path('runs') / scenario['id'] / holdout._labels(scenario, controller, freeze)['model_label']
                directory, retry = output / relative, 0
                while directory.exists() and not (directory / 'metrics.json').exists():
                    retry += 1
                    directory = output / relative.with_name(relative.name + f'_retry{retry:02d}')
                metadata.update(status='running', active_run=dict(scenario=scenario['id'], controller=controller))
                common.atomic_json(manifest_path, metadata)
                if not directory.exists():
                    metadata['started_run_attempts'] += 1
                    common.atomic_json(manifest_path, metadata)
                    original_provenance = runner.provenance
                    policy = dict(version='adaptive-v2', variant=freeze['variant'], config=freeze['policy_config'])
                    def provenance(*args, **kwargs):
                        value = original_provenance(*args, **kwargs)
                        value['diagnostic_tool_sha256'] = fingerprint['diagnostic_tool_sha256']
                        value['latency_comparable'] = False
                        if controller == 'smart':
                            value['adaptive_policy'] = policy
                        return value
                    with patch.object(runner, 'SignalEnvironment', DrainEnvironment), \
                            patch.object(runner, 'provenance', side_effect=provenance), \
                            patch.object(runner, 'SmartSignal', lambda path: AdaptiveSmartSignalV2(path, freeze['variant'])):
                        runner.run(suite_path, scenario['id'], controller, directory,
                                   model_path=freeze['model']['path'] if controller == 'smart' else None)
                actual = verify(directory, scenario, controller)
                record = dict(scenario=scenario['id'], controller=controller, **actual)
                metadata['records'].append(record)
                completed[key] = record
                metadata.update(actual_new_runs=len(metadata['records']))
                metadata.pop('active_run', None)
                common.atomic_json(manifest_path, metadata)
                print(f"Drain {len(metadata['records'])}/16: {scenario['id']} / {controller}; remaining 900/1200/1800=" +
                      '/'.join(str(actual['snapshots'][str(second)]['running'] + actual['snapshots'][str(second)]['pending']) for second in SNAPSHOTS), flush=True)
        metadata.update(status='completed', next_action='Human checkpoint: diagnose retained vehicles and drain time; do not mix 1800-second metrics with original 900-second performance')
        common.atomic_json(manifest_path, metadata)
        common.atomic_json(output / 'summary.json', dict(status='completed', actual_new_runs=16, records=metadata['records'],
                    limitations=[fingerprint['prefix_comparison'], 'Current waiting is consecutive waiting, not cumulative delay; storage pressure is observational.', 'Logging latency is not comparable.']))
        return metadata
    except common.WallTimeCheckpoint as exc:
        metadata.update(status='checkpoint_walltime', error=str(exc))
        common.atomic_json(manifest_path, metadata)
        return metadata
    except BaseException as exc:
        metadata.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, SystemExit)) else 'safety_hold',
                        error=f'{type(exc).__name__}: {exc}')
        common.atomic_json(manifest_path, metadata)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--source-suite', type=Path, default=wave3.DEFAULT_SUITE)
    parser.add_argument('--reference-wave', type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=float, default=2700)
    args = parser.parse_args()
    result = run_drain_wave(args.out, args.source_suite, args.reference_wave, args.resume, args.max_wall_seconds)
    print(f"Drain diagnosis {result['status']}: {result['actual_new_runs']}/16 verified runs", flush=True)


if __name__ == '__main__':
    main()
