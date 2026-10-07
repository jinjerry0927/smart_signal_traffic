"""Fake bounded Gyeongju comparisons; never start live SUMO or training."""
from contextlib import redirect_stdout
import copy
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import run_adaptive_gyeongju_wave as wave
from transferable_signal.spec import read_json, write_json

loader = importlib.util.spec_from_file_location('gyeongju_holdout_fixtures', ROOT / 'tests/test_adaptive_holdout.py')
fixtures = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fixtures)


class GyeongjuWaveTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HoldoutTests('test_exactly_48_paired_runs_without_test_based_reselection')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.scenarios = [dict(scenario, case='gyeongju', id=f"gyeongju_{scenario['profile']}_s{scenario['sample']:02d}")
                                 for scenario in self.fixture.scenarios if scenario['case'] == 'valid_t2']
        self.cases = dict(gyeongju=(self.fixture.fixture.spec, None, None))
        self.output = self.fixture.fixture.directory / 'gyeongju'

    def run_fake(self, **kwargs):
        data = dict(scenarios=self.fixture.scenarios), self.cases, copy.deepcopy(self.fixture.freeze), {}
        with patch.object(wave, '_preflight', return_value=data), \
                patch.object(wave, 'digest', return_value='fixturehash'), \
                patch.object(wave.runner, 'run', side_effect=self.fixture.fake_run), redirect_stdout(io.StringIO()):
            return wave.run_wave(self.output, self.fixture.fixture.suite_path, **kwargs)

    def test_exactly_24_unique_paired_runs_and_frozen_model(self):
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['actual_new_runs'], 24)
        self.assertEqual(result['expected_runs'], 24)
        self.assertEqual(len(set(self.fixture.calls)), 24)
        self.assertTrue(result['target_met'])
        self.assertEqual(len(result['summary']['scenario_summaries']), 6)
        smart = [call for call in self.fixture.calls if call[1] == 'smart']
        self.assertEqual(len(smart), 6)
        self.assertTrue(all(call[2] == self.fixture.freeze['model']['path'] for call in smart))
        self.assertTrue(all(call[2] is None for call in self.fixture.calls if call[1] != 'smart'))

    def test_partial_preserved_and_resume_does_not_repeat_completed(self):
        self.fixture.interrupt_at = 3
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        before = read_json(self.output / 'manifest.json')
        self.assertEqual(before['status'], 'interrupted')
        path = self.output / before['records'][0]['directory'] / 'metrics.json'
        original = path.read_bytes()
        self.fixture.interrupt_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['actual_new_runs'], 24)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(list(self.output.rglob('partial.txt'))), 1)

    def test_completed_orphan_recovers_without_repeat(self):
        self.fixture.complete_interrupt_at = 3
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        self.fixture.complete_interrupt_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['actual_new_runs'], 24)
        self.assertEqual(len(self.fixture.calls), 24)

    def test_collision_halts_before_second_run(self):
        self.fixture.collision = True
        with self.assertRaises(wave.common.SafetyHold):
            self.run_fake()
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(read_json(self.output / 'manifest.json')['status'], 'safety_hold')
        self.assertTrue(any(self.output.rglob('tripinfo.xml')))

    def test_changed_hash_and_controller_plan_refused_before_new_run(self):
        self.run_fake()
        self.fixture.calls.clear()
        with self.assertRaises(FileExistsError):
            self.run_fake()
        path = self.output / 'manifest.json'
        original = read_json(path)
        altered = copy.deepcopy(original)
        altered['controllers'] = ['smart']
        write_json(path, altered)
        with self.assertRaisesRegex(ValueError, 'cardinality/controller'):
            self.run_fake(resume=True)
        write_json(path, original)
        self.fixture.freeze['model']['sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'frozen model/policy changed'):
            self.run_fake(resume=True)
        self.assertEqual(self.fixture.calls, [])

    def test_saved_metrics_mismatch_recorded_as_safety_hold(self):
        self.run_fake()
        path = self.output / 'manifest.json'
        value = read_json(path)
        value['records'][0]['row']['mean_queue_vehicles'] = 999
        write_json(path, value)
        self.fixture.calls.clear()
        with self.assertRaisesRegex(ValueError, 'metrics/labels'):
            self.run_fake(resume=True)
        self.assertEqual(self.fixture.calls, [])
        self.assertEqual(read_json(path)['status'], 'safety_hold')

    def test_walltime_checkpoint_resume_and_existing_complete_resume(self):
        with patch.object(wave.time, 'monotonic', side_effect=[0, 0, 100]):
            result = self.run_fake(max_wall_seconds=1)
        self.assertEqual(result['status'], 'checkpoint_walltime')
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(self.run_fake(resume=True)['status'], 'completed')
        self.fixture.calls.clear()
        self.assertEqual(self.run_fake(resume=True)['status'], 'completed')
        self.assertEqual(self.fixture.calls, [])

    def test_emergency_braking_recorded_without_stop_or_reselection(self):
        self.fixture.warning = "Warning: Vehicle 'veh_1' performs emergency braking on lane ':j_0' with decel=7.00, time=1.00.\n"
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['emergency_braking_warnings'], 24)
        self.assertTrue(all(record['row']['emergency_braking_warnings'] == 1 for record in result['records']))

    def test_preflight_error_creates_no_output_or_traffic(self):
        with patch.object(wave, '_preflight', side_effect=ValueError('wrong frozen preparation')), \
                patch.object(wave.runner, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'preparation'):
                wave.run_wave(self.output)
            run.assert_not_called()
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
