"""Synthetic demand invariants and bounded sequencing; no live SUMO."""
from contextlib import redirect_stdout
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import diversity_wave as wave
from transferable_signal.spec import read_json, write_json
from transferable_signal.suite import catalog, load_suite


class DemandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        cls.suite = wave.prepare(cls.directory / 'prepared')
        cls.path = cls.directory / 'prepared/suite.json'
        cls.spec, cls.net, _ = load_suite(cls.path)[1]['gyeongju']

    def test_twelve_isolated_conditions_use_unchanged_network_and_model(self):
        suite, cases, fingerprint = wave.preflight(self.path)
        self.assertEqual(len(suite['scenarios']), 12)
        self.assertEqual({s['profile'] for s in suite['scenarios']}, set(wave.FAMILIES))
        self.assertEqual(len({s['seed'] for s in suite['scenarios']}), 12)
        self.assertEqual(len({s['routes_sha256'] for s in suite['scenarios']}), 12)
        self.assertEqual(cases['gyeongju'][0]['limits'], wave.DEFAULT_LIMITS)
        self.assertEqual(fingerprint['policy_identity']['seed'], 43)

    def test_explicit_sorted_schedules_and_vehicle_composition(self):
        for scenario in self.suite['scenarios']:
            vehicles = ET.parse(self.path.parent / scenario['routes']).getroot().findall('vehicle')
            times = [float(v.get('depart')) for v in vehicles]
            self.assertEqual(times, sorted(times))
            self.assertTrue(all(0 <= t < 600 for t in times))
            self.assertEqual(len(vehicles), scenario['scheduled'])
            for field in scenario['composition'].values():
                self.assertEqual(sum(field.values()), len(vehicles))
            if scenario['profile'] == 'left_bias':
                self.assertGreater(scenario['composition']['first_turns']['l'] / len(vehicles), .65)
            if scenario['profile'] == 'heavy_mix':
                counts = scenario['composition']['types']
                self.assertGreater((counts['bus'] + counts['truck']) / len(vehicles), .4)
            if scenario['profile'] == 'wave':
                counts = scenario['composition']['segments']
                self.assertGreater(counts['1'] + counts['3'], 2 * (counts['0'] + counts['2']))

    def test_same_seed_reproduces_demand_and_new_seed_changes_it(self):
        paths = [self.directory / f'determinism_{i}.xml' for i in range(3)]
        routes = catalog(self.net)
        for path, seed in zip(paths, (90123, 90123, 90124)):
            wave.make_demand(routes, self.spec, 'reversal', seed, path)
        self.assertEqual(paths[0].read_bytes(), paths[1].read_bytes())
        self.assertNotEqual(paths[0].read_bytes(), paths[2].read_bytes())

    def test_existing_preparation_is_preserved(self):
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            wave.prepare(self.path.parent)
        self.assertEqual(before, self.path.read_bytes())

    def test_changed_route_rejected_before_run_output(self):
        path = self.path.parent / self.suite['scenarios'][0]['routes']
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n')
            output = self.directory / 'rejected'
            with patch.object(wave.runner, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'Frozen demand'):
                    wave.run_wave(self.path, output)
                run.assert_not_called()
            self.assertFalse(output.exists())
        finally:
            path.write_bytes(original)

    def test_changed_deployment_refused_before_run(self):
        policy, model, identity = wave.validated_policy()
        altered = dict(identity, seed=99)
        with patch.object(wave, 'validated_policy', return_value=(policy, model, altered)):
            with self.assertRaisesRegex(ValueError, 'frozen policy'):
                wave.preflight(self.path)


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'run'
        self.suite = dict(evaluation_kind='synthetic', scenarios=[
            dict(id=f'{f}_{i}', profile=f, sample=i, scheduled=20)
            for f in wave.FAMILIES for i in range(2)])
        self.calls = []
        self.fingerprint = dict(test='fingerprint')

    def fake_run(self, suite_path, scenario, controller, directory, **kwargs):
        self.calls.append((scenario, controller, kwargs))
        directory.mkdir(parents=True)

    def fake_verify(self, directory, scenario, spec, fingerprint, controller):
        return dict(scenario=scenario['id'], controller=controller, profile=scenario['profile'],
                    sample=scenario['sample'], mean_queue_vehicles=8 if controller == 'smart' else 10,
                    max_queue_vehicles=15, arrived_vehicles=20, scheduled_vehicles=20,
                    emergency_braking_warnings=0), []

    def run_fake(self, verifier=None, **kwargs):
        with patch.object(wave, 'preflight', return_value=(self.suite, {'gyeongju': ({}, None, {})}, self.fingerprint)), \
                patch.object(wave.runner, 'run', side_effect=self.fake_run), \
                patch.object(wave, 'verify_run', side_effect=verifier or self.fake_verify), \
                redirect_stdout(io.StringIO()):
            return wave.run_wave(Path('fake-suite.json'), self.output, **kwargs)

    def test_exactly_48_paired_runs_use_default_validated_policy(self):
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(self.calls), 48)
        self.assertEqual(len({(s, c) for s, c, _ in self.calls}), 48)
        self.assertTrue(all(k == {'smart_policy': 'validated'} for _, _, k in self.calls))
        self.assertEqual(result['additional_training'], 0)
        self.assertTrue(result['summary']['target_met'])

    def test_safety_event_stops_before_next_simulation(self):
        def failed(*args):
            raise wave.common.SafetyHold('Teleport event')
        with self.assertRaises(wave.common.SafetyHold):
            self.run_fake(verifier=failed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read_json(self.output / 'manifest.json')['status'], 'safety_hold')
        with self.assertRaisesRegex(ValueError, 'failure requires inspection'):
            self.run_fake(resume=True)
        self.assertEqual(len(self.calls), 1)

    def test_completed_resume_rechecks_without_repeating(self):
        self.run_fake()
        self.calls.clear()
        result = self.run_fake(resume=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(result['actual_new_runs'], 48)

    def test_changed_resume_fingerprint_refused(self):
        self.run_fake()
        self.calls.clear()
        self.fingerprint = dict(test='different')
        with self.assertRaisesRegex(ValueError, 'Resume policy'):
            self.run_fake(resume=True)
        self.assertEqual(self.calls, [])

    def test_wall_limit_checkpoint_starts_no_new_run(self):
        with patch.object(wave.time, 'monotonic', side_effect=[0, 100]):
            result = self.run_fake(max_wall_seconds=10)
        self.assertEqual(result['status'], 'checkpoint')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
