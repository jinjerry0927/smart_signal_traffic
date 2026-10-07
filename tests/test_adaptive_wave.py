"""Fake sequential waves and raw-result audits; never run live traffic."""
import copy
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_adaptive_wave as wave
from transferable_signal import VERSION
from transferable_signal.spec import read_json, write_json

VARIANTS = dict(queue_guard=dict(kind='guard'), pressure_ai=dict(kind='pressure'),
                adaptive_pressure_ai=dict(kind='adaptive'))


class FakePolicy:
    def __init__(self, path, variant):
        self.variant = variant


class WaveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.output = self.directory / 'wave'
        self.suite_path = self.directory / 'suite.json'
        self.scenarios = [dict(id=f'{case}_{profile}_s00', case=case, profile=profile,
                               split='validation', sample=0, duration_s=1, drain_s=0, scheduled=2,
                               routes='fixture.rou.xml', routes_sha256='route', seed=index)
                          for index, (case, profile) in enumerate((case, profile)
                           for case in ('valid_t2', 'valid_x3') for profile in ('quiet', 'busy', 'directional'))]
        self.spec = dict(network_sha256='network', limits={})
        self.cases = {case: (self.spec, None, None) for case in ('valid_t2', 'valid_x3')}
        self.models = [dict(seed=seed, path=str(self.directory / f'model{seed}.json'), sha256=f'model{seed}')
                       for seed in (42, 43, 44)]
        self.baseline = {(scenario['id'], controller): self.metrics(scenario, controller, queue=10)
                         for scenario in self.scenarios for controller in wave.BASELINES}
        self.known = {}
        self.fingerprint = dict(suite_sha256='suite', core_sha256={}, sumo='fakeSUMO', python='fakePython')
        self.calls = []
        self.interrupt_at = None
        self.interrupt_after_complete_at = None
        self.collision = False
        self.warning = ''

    def metrics(self, scenario, controller='smart', queue=8):
        return dict(scenario=scenario['id'], controller=controller, status='completed',
                    mean_queue_vehicles=queue, max_queue_vehicles=12,
                    scheduled_vehicles=2, departed_vehicles=2, arrived_vehicles=2,
                    not_departed_vehicles=0, departed_not_arrived_vehicles=0,
                    running_vehicles_end=0, pending_vehicles_end=0,
                    teleport_events=0, collision_vehicle_events=0, sample_seconds=1,
                    mean_completed_waiting_time_s=3., mean_completed_travel_time_s=10.,
                    mean_completed_time_loss_s=2., p95_completed_waiting_time_s=3.,
                    observed_collision_or_teleport=False)

    def raw(self, directory, scenario, model_hash='model43', metrics=None, variant='queue_guard'):
        directory.mkdir(parents=True, exist_ok=False)
        write_json(directory / 'manifest.json', dict(version=VERSION, status='completed',
                   suite_sha256='suite', controller='smart', model_sha256=model_hash,
                   scenario=scenario, network_sha256='network', limits={},
                   code_sha256={'adaptive.py': 'fixturehash'}, sumo='fakeSUMO', python='fakePython',
                   adaptive_policy=dict(variant=variant, config=VARIANTS[variant])))
        write_json(directory / 'metrics.json', self.metrics(scenario) if metrics is None else metrics)
        trips = ET.Element('tripinfos')
        for index in range(2):
            ET.SubElement(trips, 'tripinfo', id=str(index), arrival='1', waitingTime='3', duration='10', timeLoss='2')
        ET.ElementTree(trips).write(directory / 'tripinfo.xml')
        (directory / 'sumo.log').write_text('', encoding='utf-8')
        (directory / 'sumo-errors.log').write_text(self.warning, encoding='utf-8')

    def fake_run(self, suite, scenario_id, controller, output, model_path=None):
        scenario = next(item for item in self.scenarios if item['id'] == scenario_id)
        policy = wave.runner.SmartSignal(model_path)
        self.calls.append((scenario_id, policy.variant, model_path))
        if len(self.calls) == self.interrupt_at:
            output.mkdir(parents=True)
            (output / 'partial.txt').write_text('preserve interrupted traffic logs', encoding='utf-8')
            raise KeyboardInterrupt()
        model = next(item for item in self.models if item['path'] == model_path)
        queues = dict(queue_guard=9, pressure_ai=8, adaptive_pressure_ai=7)
        metrics = self.metrics(scenario, queue=queues[policy.variant])
        if self.collision:
            metrics['collision_vehicle_events'] = 1
        self.raw(output, scenario, model['sha256'], metrics, policy.variant)
        if len(self.calls) == self.interrupt_after_complete_at:
            raise KeyboardInterrupt()
        return metrics

    def run_fake(self, **kwargs):
        adaptive = types.SimpleNamespace(AdaptiveSmartSignal=FakePolicy, VARIANTS=VARIANTS)
        fixture = (dict(scenarios=self.scenarios), self.cases, self.models,
                   self.baseline, self.known, copy.deepcopy(self.fingerprint))
        with patch.dict(sys.modules, {'transferable_signal.adaptive': adaptive}), \
                patch.object(wave, '_preflight', return_value=fixture), \
                patch.object(wave, 'digest', return_value='fixturehash'), \
                patch.object(wave.runner, 'run', side_effect=self.fake_run), redirect_stdout(io.StringIO()):
            return wave.run_wave(self.output, self.suite_path, model_paths=[], **kwargs)

    def test_pilot_then_full_reuses_selected_seed43_and_never_baselines(self):
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['selected_variant'], 'adaptive_pressure_ai')
        self.assertTrue(result['selected_pilot_eligible'])
        self.assertTrue(result['target_met'])
        # Fixture has six full scenarios: 18 pilots + 18 full - six reused.
        self.assertEqual(len(self.calls), 30)
        self.assertEqual(result['actual_new_runs'], 30)
        self.assertEqual(len({(scenario, variant, path) for scenario, variant, path in self.calls}), 30)
        self.assertEqual(len(result['full_summaries']), 3)
        self.assertEqual(read_json(self.output / 'summary.json')['status'], 'completed')

    def test_unsafe_event_stops_before_second_simulation(self):
        self.collision = True
        with self.assertRaises(wave.SafetyHold):
            self.run_fake()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read_json(self.output / 'manifest.json')['status'], 'safety_hold')
        self.assertTrue(any(self.output.rglob('tripinfo.xml')))

    def test_new_emergency_braking_recorded_and_does_not_stop_alone(self):
        self.warning = "Warning: Vehicle 'veh_1' performs emergency braking on lane ':j_0' with decel=7.00, time=1.00.\n"
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(all(record['row']['new_emergency_braking'] == 1 for record in result['records']))
        self.assertTrue(all(record['new_warning_details'] == [self.warning.strip()] for record in result['records']))

    def test_interrupt_resume_preserves_completed_and_partial_runs(self):
        self.interrupt_at = 2
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        before = read_json(self.output / 'manifest.json')
        self.assertEqual(before['status'], 'interrupted')
        self.assertEqual(len(before['records']), 1)
        original = self.output / before['records'][0]['directory'] / 'metrics.json'
        original_bytes = original.read_bytes()
        self.interrupt_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['actual_new_runs'], 30)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertEqual(len(list(self.output.rglob('partial.txt'))), 1)
        self.assertTrue(any('_retry01' in record['directory'] for record in result['records']))

    def test_existing_output_requires_resume_and_resume_does_not_rerun(self):
        first = self.run_fake()
        self.calls.clear()
        with self.assertRaises(FileExistsError):
            self.run_fake()
        again = self.run_fake(resume=True)
        self.assertEqual(again['actual_new_runs'], first['actual_new_runs'])
        self.assertEqual(self.calls, [])

    def test_completed_orphan_recovered_without_rerun_and_counter_matches(self):
        self.interrupt_after_complete_at = 2
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        before = read_json(self.output / 'manifest.json')
        self.assertEqual(len(before['records']), 1)
        self.assertEqual(len(list(self.output.rglob('metrics.json'))), 2)
        self.interrupt_after_complete_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(len(self.calls), 30)
        self.assertEqual(result['actual_new_runs'], len(result['records']))
        self.assertEqual(result['actual_new_runs'], 30)

    def test_corrupted_saved_row_and_labels_block_resume(self):
        self.run_fake()
        path = self.output / 'manifest.json'
        original = read_json(path)
        for key, corrupt in (('mean_queue_vehicles', 999), ('profile', 'busy'), ('model_label', 'wrong'), ('sample', 2)):
            value = copy.deepcopy(original)
            value['records'][0]['row'][key] = corrupt
            write_json(path, value)
            self.calls.clear()
            with self.assertRaisesRegex(ValueError, 'Saved wave'):
                self.run_fake(resume=True)
            self.assertEqual(self.calls, [])

    def test_changed_adaptive_provenance_and_runtime_block_resume(self):
        result = self.run_fake()
        path = self.output / result['records'][0]['directory'] / 'manifest.json'
        original = read_json(path)
        for key, corrupt in (('code_sha256', {'adaptive.py': 'changed'}), ('sumo', 'differentSUMO'),
                             ('adaptive_policy', dict(variant='pressure_ai', config=VARIANTS['pressure_ai']))):
            value = copy.deepcopy(original)
            value[key] = corrupt
            write_json(path, value)
            self.calls.clear()
            with self.assertRaises(ValueError):
                self.run_fake(resume=True)
            self.assertEqual(self.calls, [])

    def test_walltime_checkpoint_occurs_between_completed_runs(self):
        # One run completes, then the next run observes the bounded deadline.
        with patch.object(wave.time, 'monotonic', side_effect=[0, 0, 100]):
            result = self.run_fake(max_wall_seconds=1)
        self.assertEqual(result['status'], 'checkpoint_walltime')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(self.run_fake(resume=True)['status'], 'completed')

    def test_failed_pilot_gate_still_completes_diagnostic_wave(self):
        for metrics in self.baseline.values():
            metrics['mean_queue_vehicles'] = 1
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertFalse(result['selected_pilot_eligible'])
        self.assertFalse(result['target_met'])
        self.assertEqual(len(self.calls), 30)

    def test_raw_count_mismatch_rejected(self):
        output = self.directory / 'raw'
        metrics = self.metrics(self.scenarios[0])
        metrics['arrived_vehicles'] = 1
        self.raw(output, self.scenarios[0], metrics=metrics)
        with self.assertRaisesRegex(ValueError, 'accounting differs'):
            wave.audit_run(output, self.scenarios[0], self.spec, 'suite', 'smart', 'model43')

    def test_trip_statistic_mismatch_and_nonfinite_rejected(self):
        output = self.directory / 'raw'
        metrics = self.metrics(self.scenarios[0])
        metrics['mean_completed_waiting_time_s'] = 99
        self.raw(output, self.scenarios[0], metrics=metrics)
        with self.assertRaisesRegex(ValueError, 'statistic differs'):
            wave.audit_run(output, self.scenarios[0], self.spec, 'suite', 'smart', 'model43')
        path = output / 'metrics.json'
        text = path.read_text(encoding='utf-8').replace('"mean_queue_vehicles": 8', '"mean_queue_vehicles": NaN')
        path.write_text(text, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Nonfinite metric'):
            wave.audit_run(output, self.scenarios[0], self.spec, 'suite', 'smart', 'model43')

    def test_fatal_log_and_new_unknown_warning_stop(self):
        for text in ('Error: broken route', 'Warning: Teleporting vehicle jam'):
            path = self.directory / 'error.log'
            path.write_text(text, encoding='utf-8')
            with self.assertRaises(wave.SafetyHold):
                wave.warning_signatures(path)


if __name__ == '__main__':
    unittest.main()
