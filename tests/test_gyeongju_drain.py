"""Fake drain-boundary and sequential-checkpoint tests; no live SUMO."""
from contextlib import redirect_stdout
import copy
import csv
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import diagnose_gyeongju_drain as drain
from transferable_signal.spec import read_json, write_json


def csv_write(path, fields, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def snapshot(second=900):
    active = [dict(id='b', lane='N_in_0', edge='N_in', route=['N_in', 'S_out'], route_index=0,
                   lane_position_m=10., speed_mps=0., current_continuous_wait_s=3.)] if second == 900 else []
    arrived = ['a'] if active else ['a', 'b']
    return dict(time_s=second, running=len(active), pending=0, arrived=len(arrived), departed=2,
                arrived_ids=arrived, departed_ids=['a', 'b'], pending_ids=[], vehicles=active, queued=len(active))


class DrainBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.reference, self.output = self.path / 'reference', self.path / 'diagnostic'
        self.reference.mkdir()
        self.output.mkdir()
        self.spec = dict(lanes={'N_in_0': dict(edge='N_in', capacity_vehicles=10),
                                'S_out_0': dict(edge='S_out', capacity_vehicles=10)},
                         junctions=[dict(id=f'j{i}', movements=[dict(incoming='N_in_0', outgoing='S_out_0')]) for i in range(3)])
        self.scenario = dict(id='fixture', case='gyeongju', scheduled=2, routes='fixture.rou.xml')
        self.rows = [dict(time_s=t, queued=1 if 1 <= t <= 900 else 0,
                         system_current_wait_s=3 if 1 <= t <= 900 else 0,
                         arrived=0 if t < 500 else 1 if t <= 900 else 2,
                         departed=0 if t == 0 else 2, running=0 if t == 0 or t > 900 else 2 if t < 500 else 1,
                         pending=0) for t in range(1801)]
        csv_write(self.output / 'seconds.csv', list(self.rows[0]), self.rows)
        signals = [dict(time_s=t, junction=f'j{i}', phase=0, stage='green', green_elapsed_s=25,
                        queue_ratio=.1 if t <= 900 else 0, state='G') for t in range(5, 1801, 5) for i in range(3)]
        csv_write(self.output / 'signals.csv', list(signals[0]), signals)
        csv_write(self.reference / 'signals.csv', list(signals[0]), signals[:540])
        trips = ET.Element('tripinfos')
        self.first = dict(id='a', depart='0', arrival='499', duration='499', waitingTime='3', timeLoss='2', arrivalLane='S_out_0')
        # Arrival=900 belongs to step901. Its ID is absent from snapshot900.
        self.future = dict(id='b', depart='1', arrival='900', duration='899', waitingTime='3', timeLoss='2', arrivalLane='S_out_0')
        for trip in (self.first, self.future):
            ET.SubElement(trips, 'tripinfo', trip)
        ET.ElementTree(trips).write(self.output / 'tripinfo.xml')
        old = ET.Element('tripinfos')
        ET.SubElement(old, 'tripinfo', self.first)
        ET.SubElement(old, 'tripinfo', dict(self.future, arrival='-1', arrivalLane=''))
        ET.ElementTree(old).write(self.reference / 'tripinfo.xml')
        self.metrics = dict(scenario='fixture', controller='smart', status='completed',
            mean_system_total_waiting_time_s=3., mean_queue_vehicles=1., max_queue_vehicles=1,
            arrived_vehicles=1, departed_vehicles=2, scheduled_vehicles=2, not_departed_vehicles=0,
            departed_not_arrived_vehicles=1, running_vehicles_end=1, pending_vehicles_end=0, sample_seconds=900,
            mean_completed_waiting_time_s=3., mean_completed_travel_time_s=499., mean_completed_time_loss_s=2.,
            p95_completed_waiting_time_s=3., teleport_events=0, collision_vehicle_events=0,
            observed_collision_or_teleport=False, mean_decision_ms_per_junction=.01, p95_decision_ms_per_junction=.02)
        write_json(self.reference / 'metrics.json', self.metrics)
        for t in drain.SNAPSHOTS:
            write_json(self.output / f'snapshot_{t}.json', snapshot(t))
        for directory in (self.output, self.reference):
            for filename in ('sumo-errors.log', 'sumo.log'):
                (directory / filename).write_text('', encoding='utf-8')

    def audit(self):
        return drain.audit_prefix(self.output, self.reference, self.scenario, self.spec)

    def test_arrival900_future_row_excluded_by_observed_id_set(self):
        result = self.audit()
        self.assertEqual(result['arrived_vehicles'], 1)
        self.assertEqual(result['mean_completed_travel_time_s'], 499)
        self.assertNotIn('mean_decision_ms_per_junction', result)
        self.assertNotIn('p95_decision_ms_per_junction', result)

    def test_changed_completed_trip_attribute_is_rejected(self):
        root = ET.parse(self.output / 'tripinfo.xml')
        root.getroot().find('tripinfo').set('arrivalLane', 'different_lane')
        root.write(self.output / 'tripinfo.xml')
        with self.assertRaisesRegex(ValueError, 'trip attributes'):
            self.audit()

    def test_changed_900_signal_queue_ratio_is_rejected(self):
        path = self.output / 'signals.csv'
        path.write_text(path.read_text(encoding='utf-8').replace('0.1', '0.2', 1), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'signal/queue-ratio'):
            self.audit()

    def test_changed_900_counts_and_queue_aggregate_are_rejected(self):
        value = read_json(self.output / 'snapshot_900.json')
        value['running'] = 0
        write_json(self.output / 'snapshot_900.json', value)
        with self.assertRaisesRegex(ValueError, 'aggregate'):
            self.audit()
        write_json(self.output / 'snapshot_900.json', snapshot())
        self.rows[2]['queued'] = 0
        csv_write(self.output / 'seconds.csv', list(self.rows[0]), self.rows)
        with self.assertRaisesRegex(ValueError, 'aggregate'):
            self.audit()

    def test_warning899_included_warning900_excluded(self):
        warning = lambda t: f"Warning: Vehicle 'b' performs emergency braking on lane ':j_0' with decel=7.00, time={t}.00.\n"
        (self.reference / 'sumo-errors.log').write_text(warning(899), encoding='utf-8')
        (self.output / 'sumo-errors.log').write_text(warning(899) + warning(900), encoding='utf-8')
        self.audit()
        self.assertEqual(len(drain._warnings_until(self.output / 'sumo-errors.log', 900)), 1)
        self.assertEqual(len(drain.common.warning_signatures(self.output / 'sumo-errors.log')), 2)

    def test_snapshot_duplicate_missing_departed_nan_and_queue_rejected(self):
        drain._snapshot_validate(snapshot(), {'a', 'b'})
        changes = []
        value = snapshot(); value['arrived_ids'].append('a'); changes.append(value)
        value = snapshot(); value['departed_ids'] = ['a']; changes.append(value)
        value = snapshot(); value['pending_ids'] = ['b']; changes.append(value)
        value = snapshot(); value['vehicles'][0]['speed_mps'] = float('nan'); changes.append(value)
        value = snapshot(); value['queued'] = 0; changes.append(value)
        for value in changes:
            with self.subTest(value=value), self.assertRaises(ValueError):
                drain._snapshot_validate(value, {'a', 'b'})

    def test_observe_deduplicates_and_clear_requires_all_arrived_after600(self):
        route = self.path / 'fixture.rou.xml'
        route.write_text('<routes><vehicle id="a"/><vehicle id="b"/></routes>', encoding='utf-8')
        env = drain.DrainEnvironment(self.spec, 'unused.net.xml', route, self.output, 1800)
        self.addCleanup(env.close)
        state = dict(t=0, running=0, pending=(), arrived=set(), departed=set())
        vehicle = SimpleNamespace(getIDCount=lambda: state['running'])
        env.connection = SimpleNamespace(simulation=SimpleNamespace(getTime=lambda: state['t'],
            getPendingVehicles=lambda: state['pending']), vehicle=vehicle,
            trafficlight=SimpleNamespace(getRedYellowGreenState=lambda _: 'G'), close=lambda: None)
        env.clocks = {j['id']: SimpleNamespace(current=0, stage='green', elapsed=25, state=lambda: 'G') for j in self.spec['junctions']}
        env.observer.samples = {'N_in_0': dict(queue=0, vehicles=0), 'S_out_0': dict(queue=0, vehicles=0)}
        with patch.object(drain.SignalEnvironment, 'observe', return_value={'unchanged': True}):
            for t in range(602):
                state['t'] = t
                env.metrics.arrived = {'a', 'b'} if t == 601 else {'a'} if t >= 600 else set()
                env.metrics.departed = env.metrics.arrived.copy()
                if t:
                    env.metrics.waits.append(0); env.metrics.queues.append(0)
                self.assertEqual(env.observe(), {'unchanged': True})
                env.observe()
                if t <= 600:
                    self.assertIsNone(env.clear_time)
        self.assertEqual(env.clear_time, 601)
        self.assertEqual(env.last_arrival_time, 601)
        env.close()
        with (self.output / 'seconds.csv').open(encoding='utf-8') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))), 602)

    def test_full_trace_coverage_accounting_and_clear_time(self):
        lane_rows = [dict(time_s=t, lane=lid, edge=data['edge'], queued=0, vehicles=0,
                          storage_capacity_proxy=10, density_proxy=0) for t in range(1801) for lid, data in self.spec['lanes'].items()]
        node_rows = [dict(time_s=t, junction=j['id'], phase=0, stage='green', green_elapsed_s=25,
                         incoming_queued=0, incoming_vehicles=0, downstream_storage_pressure_lanes='[]')
                    for t in range(1801) for j in self.spec['junctions']]
        csv_write(self.output / 'lanes.csv', list(lane_rows[0]), lane_rows)
        csv_write(self.output / 'junctions.csv', list(node_rows[0]), node_rows)
        seconds, observed = drain._audit_trace(self.output, self.spec, 2)
        self.assertEqual(len(seconds), 1801)
        self.assertEqual(len(observed['junctions.csv'][900]), 3)
        csv_write(self.output / 'lanes.csv', list(lane_rows[0]), lane_rows[:-1])
        with self.assertRaisesRegex(ValueError, 'coverage'):
            drain._audit_trace(self.output, self.spec, 2)


class DrainSequentialTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.output, self.source = self.path / 'wave', self.path / 'suite.json'
        self.source.write_text('{}', encoding='utf-8')
        self.selected = [dict(id=sid, case='gyeongju', profile='busy' if 'busy' in sid else 'directional',
            sample=int(sid[-2:]), scheduled=2, duration_s=600, drain_s=300) for sid in drain.SELECTED]
        self.original = dict(scenarios=self.selected)
        self.freeze = dict(variant='load_gate_pressure_wait35', policy_config={}, model=dict(seed=43, path='frozen_model43.json', sha256='model43'))
        self.fingerprint = dict(diagnostic_tool_sha256='codehash', prefix_comparison='No original pointwise queue trace')
        self.calls, self.interrupt, self.orphan, self.collision = [], None, None, False

    def prepare(self, output, *_):
        directory = output / 'prepared_input'
        directory.mkdir()
        path = directory / 'suite.json'
        write_json(path, dict(scenarios=[dict(s, drain_s=1200) for s in self.selected]))
        return path

    def fake_run(self, suite, scenario, controller, output, model_path=None):
        self.calls.append((scenario, controller, model_path))
        output.mkdir(parents=True)
        if len(self.calls) == self.interrupt:
            (output / 'partial.txt').write_text('preserve', encoding='utf-8')
            raise KeyboardInterrupt()
        write_json(output / 'metrics.json', dict(scenario=scenario, controller=controller, collision=self.collision))
        if len(self.calls) == self.orphan:
            raise KeyboardInterrupt()

    def audit(self, directory, scenario, controller, suite, freeze, reference, fingerprint, output):
        value = read_json(directory / 'metrics.json')
        if value['collision']:
            raise drain.common.SafetyHold('collision')
        if value['scenario'] != scenario['id'] or value['controller'] != controller:
            raise ValueError('raw identity mismatch')
        return dict(metrics1800=value, prefix900=dict(arrived_vehicles=1),
                    snapshots={str(t): dict(running=1 if t == 900 else 0, pending=0) for t in drain.SNAPSHOTS},
                    diagnostic_directory=str(directory.relative_to(output)))

    def run_fake(self, **kwargs):
        data = self.original, {}, self.freeze, self.selected, {(s['id'], c): self.path / 'ref' for s in self.selected for c in drain.wave3.CONTROLLERS}, copy.deepcopy(self.fingerprint)
        with patch.object(drain, '_preflight', return_value=data), patch.object(drain, '_prepare_input', side_effect=self.prepare), \
                patch.object(drain, 'load_suite', side_effect=lambda path: (read_json(path), {})), \
                patch.object(drain, 'audit_drain_run', side_effect=self.audit), \
                patch.object(drain.runner, 'run', side_effect=self.fake_run), redirect_stdout(io.StringIO()):
            return drain.run_drain_wave(self.output, self.source, self.path / 'reference', **kwargs)

    def test_exact16_runs_no_learning_or_model_reselection(self):
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['actual_new_runs'], 16)
        self.assertEqual(len({call[:2] for call in self.calls}), 16)
        self.assertTrue(all(model == self.freeze['model']['path'] if c == 'smart' else model is None for _, c, model in self.calls))
        self.calls.clear()
        self.assertEqual(self.run_fake(resume=True)['actual_new_runs'], 16)
        self.assertEqual(self.calls, [])

    def test_partial_and_completed_preserved_resume_without_repeat(self):
        self.interrupt = 3
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        manifest = read_json(self.output / 'manifest.json')
        path = self.output / manifest['records'][0]['diagnostic_directory'] / 'metrics.json'
        original = path.read_bytes()
        self.interrupt = None
        self.assertEqual(self.run_fake(resume=True)['actual_new_runs'], 16)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list(self.output.rglob('partial.txt'))), 1)
        self.assertEqual(len(self.calls), 17)

    def test_completed_orphan_recovered_without_repeat(self):
        self.orphan = 3
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        self.orphan = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['actual_new_runs'], 16)
        self.assertEqual(len(self.calls), 16)

    def test_collision_safety_hold_and_no_second_run(self):
        self.collision = True
        with self.assertRaises(drain.common.SafetyHold):
            self.run_fake()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read_json(self.output / 'manifest.json')['status'], 'safety_hold')
        with self.assertRaisesRegex(ValueError, 'safety review'):
            self.run_fake(resume=True)

    def test_changed_code_prepared_horizon_and_saved_metrics_refuse_resume(self):
        self.run_fake()
        self.calls.clear()
        self.fingerprint['diagnostic_tool_sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'input/code changed'):
            self.run_fake(resume=True)
        self.fingerprint['diagnostic_tool_sha256'] = 'codehash'
        path = self.output / 'prepared_input/suite.json'
        original = path.read_bytes()
        value = read_json(path); value['scenarios'][0]['drain_s'] = 300; write_json(path, value)
        with self.assertRaisesRegex(ValueError, 'prepared horizon'):
            self.run_fake(resume=True)
        path.write_bytes(original)
        path = self.output / 'manifest.json'
        value = read_json(path); value['records'][0]['prefix900']['arrived_vehicles'] = 999; write_json(path, value)
        with self.assertRaisesRegex(ValueError, 'Saved drain result'):
            self.run_fake(resume=True)
        self.assertEqual(self.calls, [])

    def test_walltime_boundary_resumes_and_preflight_failure_creates_nothing(self):
        with patch.object(drain.time, 'monotonic', side_effect=[0, 0, 100]):
            result = self.run_fake(max_wall_seconds=1)
        self.assertEqual(result['status'], 'checkpoint_walltime')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.run_fake(resume=True)['actual_new_runs'], 16)
        other = self.path / 'new_output'
        with patch.object(drain, '_preflight', side_effect=ValueError('reference changed')), patch.object(drain.runner, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'reference changed'):
                drain.run_drain_wave(other)
            run.assert_not_called()
        self.assertFalse(other.exists())


if __name__ == '__main__':
    unittest.main()
