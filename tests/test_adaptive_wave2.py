"""Bounded second-wave orchestration with synthetic runs; no SUMO traffic."""
from contextlib import redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_adaptive_wave2 as wave
from transferable_signal.spec import read_json, write_json

# Reuse the first wave's synthetic raw-trip fixture without modifying it.
loader = importlib.util.spec_from_file_location('first_wave_test_fixtures', ROOT / 'tests/test_adaptive_wave.py')
fixtures = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fixtures)
NAMES = ('load_gate_pressure_wait40', 'load_gate_fixed_wait40', 'load_gate_pressure_wait35')
VARIANTS = {name: dict(version='adaptive-v2', kind=name) for name in NAMES}


class FakePolicy:
    def __init__(self, path, variant):
        self.variant = variant


class Wave2Tests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WaveTests('test_raw_count_mismatch_rejected')
        self.fixture.setUp()
        self.fixture.fingerprint['first_wave'] = dict(old_policy_sha256='fixturehash')
        self.addCleanup(self.fixture.doCleanups)
        self.output = self.fixture.directory / 'wave2'
        self.calls = []
        self.interrupt_at = None
        self.complete_interrupt_at = None
        self.collision = False
        self.warning = ''
        self.queues = dict(zip(NAMES, (9., 8., 7.)))
        self.seed_queue_overrides = {}
        self.references = {(scenario['id'], model['seed']): self.fixture.metrics(scenario, queue=9)
                           for scenario in self.fixture.scenarios for model in self.fixture.models}

    def fake_run(self, suite, scenario_id, controller, output, model_path=None):
        scenario = next(item for item in self.fixture.scenarios if item['id'] == scenario_id)
        policy = wave.runner.SmartSignal(model_path)
        self.calls.append((scenario_id, policy.variant, model_path))
        if len(self.calls) == self.interrupt_at:
            output.mkdir(parents=True)
            (output / 'partial.txt').write_text('preserve interruption', encoding='utf-8')
            raise KeyboardInterrupt()
        model = next(item for item in self.fixture.models if item['path'] == model_path)
        metrics = self.fixture.metrics(scenario, queue=self.seed_queue_overrides.get((policy.variant, model['seed']), self.queues[policy.variant]))
        if self.collision:
            metrics['collision_vehicle_events'] = 1
        self.fixture.warning = self.warning
        self.fixture.raw(output, scenario, model['sha256'], metrics)
        manifest_path = output / 'manifest.json'
        manifest = read_json(manifest_path)
        manifest['code_sha256']['adaptive_v2.py'] = 'fixturehash'
        manifest['adaptive_policy'] = dict(version='adaptive-v2', variant=policy.variant, config=VARIANTS[policy.variant])
        write_json(manifest_path, manifest)
        if len(self.calls) == self.complete_interrupt_at:
            raise KeyboardInterrupt()
        return metrics

    def run_fake(self, **kwargs):
        adaptive = types.SimpleNamespace(AdaptiveSmartSignalV2=FakePolicy, VARIANTS_V2=VARIANTS)
        fixture = (dict(scenarios=self.fixture.scenarios), self.fixture.cases, self.fixture.models,
                   self.fixture.baseline, self.fixture.known, copy.deepcopy(self.fixture.fingerprint),
                   self.references, self.references)
        with patch.dict(sys.modules, {'transferable_signal.adaptive_v2': adaptive}), \
                patch.object(wave, '_preflight', return_value=fixture), \
                patch.object(wave, 'digest', return_value='fixturehash'), \
                patch.object(wave.runner, 'run', side_effect=self.fake_run), redirect_stdout(io.StringIO()):
            return wave.run_wave2(self.output, self.fixture.suite_path, model_paths=[], **kwargs)

    def test_three_seed_pilot_pass_then_paired_full_reuses_all_pilot_seeds(self):
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['selected_variant'], NAMES[2])
        self.assertTrue(result['target_met'])
        self.assertEqual(result['actual_new_runs'], 54)
        self.assertEqual(len(self.calls), 54)
        self.assertEqual({Path(path).stem for _, _, path in self.calls[:18]}, {'model42', 'model43', 'model44'})
        self.assertEqual(len(set(self.calls)), 54)
        self.assertEqual(len(result['full_summaries']), 3)
        self.assertTrue(all(len(summary['case_summaries']) == 2 and len(summary['case_profile_summaries']) == 6
                            for summary in result['full_summaries']))
        self.assertTrue(all('wave1_queue_guard' in summary['queue_change'] and 'original_smart' in summary['queue_change']
                            for summary in result['full_summaries']))

    def test_rejected_pilot_stops_after_fifty_four_without_full(self):
        self.queues = {name: 11 for name in NAMES}
        result = self.run_fake()
        self.assertEqual(result['status'], 'pilot_rejected')
        self.assertIsNone(result['selected_variant'])
        self.assertFalse(result['target_met'])
        self.assertEqual(len(self.calls), 54)
        self.assertNotIn('full_summaries', result)
        self.assertTrue(all(summary['rejection_reasons'] for summary in result['pilot_summaries'].values()))

    def test_requires_three_percent_improvement_over_wave1(self):
        self.queues = {name: 9 for name in NAMES}
        result = self.run_fake()
        self.assertEqual(result['status'], 'pilot_rejected')
        self.assertTrue(all(summary['rejection_reasons'] == ['seed44:queue_improvement_wave1_below_3pct']
                            for summary in result['pilot_summaries'].values()))

    def test_thirty_conditions_produce_exactly_126_unique_runs(self):
        expanded = []
        for scenario in self.fixture.scenarios:
            for sample in range(5):
                value = dict(scenario, id=scenario['id'][:-1] + str(sample), sample=sample,
                             seed=scenario['seed'] + sample * 100)
                expanded.append(value)
        self.fixture.scenarios = expanded
        self.fixture.baseline = {(scenario['id'], controller): self.fixture.metrics(scenario, controller, queue=10)
                                 for scenario in expanded for controller in wave.previous.BASELINES}
        self.references = {(scenario['id'], model['seed']): self.fixture.metrics(scenario, queue=9)
                           for scenario in expanded for model in self.fixture.models}
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['actual_new_runs'], 126)
        self.assertEqual(result['expected_max_new_runs'], 126)
        self.assertEqual(result['full_comparison_count'], 90)
        self.assertEqual(len(set(self.calls)), 126)
        self.assertEqual(result['baseline_reused_runs'], 90)

    def test_all_seed_gates_and_worst_seed_ordering(self):
        self.seed_queue_overrides.update({(NAMES[0], 42): 3, (NAMES[0], 43): 3, (NAMES[0], 44): 8,
                                          (NAMES[1], 42): 7, (NAMES[1], 43): 7, (NAMES[1], 44): 7,
                                          (NAMES[2], 42): 9.6})
        result = self.run_fake()
        self.assertEqual(result['selected_variant'], NAMES[1])
        best_mean = result['pilot_summaries'][NAMES[0]]
        selected = result['pilot_summaries'][NAMES[1]]
        self.assertLess(best_mean['overall']['mean_queue_vehicles'], selected['overall']['mean_queue_vehicles'])
        self.assertGreater(best_mean['worst_seed_queue_change'], selected['worst_seed_queue_change'])
        rejected = result['pilot_summaries'][NAMES[2]]
        self.assertFalse(rejected['pilot_eligible'])
        self.assertIn('seed42:queue_improvement_fixed_below_5pct', rejected['rejection_reasons'])

    def test_preflight_failure_occurs_before_output_and_any_simulation(self):
        adaptive = types.SimpleNamespace(AdaptiveSmartSignalV2=FakePolicy, VARIANTS_V2=VARIANTS)
        with patch.dict(sys.modules, {'transferable_signal.adaptive_v2': adaptive}), \
                patch.object(wave, '_preflight', side_effect=ValueError('incomplete source cache')), \
                patch.object(wave.runner, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'source cache'):
                wave.run_wave2(self.output)
            run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_collision_halts_before_next_run_and_preserves_raw(self):
        self.collision = True
        with self.assertRaises(wave.previous.SafetyHold):
            self.run_fake()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read_json(self.output / 'manifest.json')['status'], 'safety_hold')
        self.assertTrue(any(self.output.rglob('tripinfo.xml')))

    def test_interrupt_partial_preserved_and_completed_runs_not_repeated(self):
        self.interrupt_at = 2
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        before = read_json(self.output / 'manifest.json')
        path = self.output / before['records'][0]['directory'] / 'metrics.json'
        content = path.read_bytes()
        self.interrupt_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['actual_new_runs'], 54)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(len(list(self.output.rglob('partial.txt'))), 1)

    def test_completed_orphan_recovers_and_counts_without_repeat(self):
        self.complete_interrupt_at = 2
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        self.complete_interrupt_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['actual_new_runs'], len(result['records']))
        self.assertEqual(result['actual_new_runs'], 54)
        self.assertEqual(len(self.calls), 54)

    def test_changed_saved_results_provenance_and_warning_details_rejected(self):
        result = self.run_fake()
        path = self.output / 'manifest.json'
        original = read_json(path)
        for key, corrupt in (('mean_queue_vehicles', 999), ('profile', 'busy'), ('sample', 2), ('new_emergency_braking', 4)):
            value = copy.deepcopy(original)
            value['records'][0]['row'][key] = corrupt
            write_json(path, value)
            self.calls.clear()
            with self.assertRaises(ValueError):
                self.run_fake(resume=True)
            self.assertEqual(self.calls, [])
        write_json(path, original)
        raw = self.output / result['records'][0]['directory'] / 'manifest.json'
        manifest = read_json(raw)
        manifest['code_sha256']['adaptive_v2.py'] = 'changed'
        write_json(raw, manifest)
        with self.assertRaisesRegex(ValueError, 'code differs'):
            self.run_fake(resume=True)

    def test_new_emergency_warning_recorded_without_stopping(self):
        self.warning = "Warning: Vehicle 'veh_1' performs emergency braking on lane ':j_0' with decel=7.00, time=1.00.\n"
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(all(record['row']['new_emergency_braking'] == 1 for record in result['records']))

    def test_walltime_boundary_has_resume_support(self):
        with patch.object(wave.time, 'monotonic', side_effect=[0, 0, 100]):
            result = self.run_fake(max_wall_seconds=1)
        self.assertEqual(result['status'], 'checkpoint_walltime')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.run_fake(resume=True)['status'], 'completed')

    def test_existing_output_and_changed_criteria_do_not_execute(self):
        self.run_fake()
        self.calls.clear()
        with self.assertRaises(FileExistsError):
            self.run_fake()
        with patch.dict(wave.CRITERIA, {'queue_ratio_fixed': .90}):
            with self.assertRaisesRegex(ValueError, 'criteria'):
                self.run_fake(resume=True)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
