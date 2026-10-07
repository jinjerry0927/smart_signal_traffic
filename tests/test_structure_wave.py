"""Build real inputs without advancing SUMO; test bounded sequencing with fakes."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import structure_wave as wave
from transferable_signal.spec import read_json, write_json, digest
from transferable_signal.suite import catalog, demand


class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        # Reuse the pre-live exclusion snapshot when rerunning after production
        # preparation, so this test does not mistake its own batch for prior data.
        production = ROOT / 'network/structure_wave6_20261007/suite.json'
        excluded = (read_json(production)['exclusion'] if production.exists()
                    else wave.exclusions(wave.validated_policy()[0]))
        with patch.object(wave, 'exclusions', return_value=excluded):
            cls.suite = wave.prepare(cls.directory / 'prepared')
        cls.path = cls.directory / 'prepared/suite.json'
        _, cls.cases, cls.fingerprint = wave.preflight(cls.path)

    def test_six_new_safe_geometries_and_36_isolated_schedules(self):
        suite, cases, fp = wave.preflight(self.path)
        self.assertEqual(fp['policy_identity']['seed'], 43)
        self.assertEqual(len(cases), 6)
        self.assertEqual(len(suite['scenarios']), 36)
        excluded = set(suite['exclusion']['network_sha256'])
        for geometry in wave.GEOMETRIES:
            spec, network, _ = cases[geometry['id']]
            self.assertNotIn(digest(network), excluded)
            self.assertEqual(spec['limits'], wave.DEFAULT_LIMITS)
            self.assertEqual(len(spec['junctions'][0]['phases']), len(geometry['arms']))
            self.assertEqual({len(p['incoming']) for p in spec['junctions'][0]['phases']}, {geometry['lanes']})
        self.assertEqual(len({s['seed'] for s in suite['scenarios']}), 36)
        self.assertEqual(len({s['routes_sha256'] for s in suite['scenarios']}), 36)

    def test_explicit_ordered_demand_is_reproducible(self):
        for scenario in self.suite['scenarios']:
            vehicles = ET.parse(self.path.parent / scenario['routes']).getroot().findall('vehicle')
            times = [float(v.get('depart')) for v in vehicles]
            self.assertEqual(times, sorted(times))
            self.assertTrue(all(0 <= t < 600 for t in times))
            self.assertEqual(len(vehicles), scenario['scheduled'])
        scenario = self.suite['scenarios'][0]
        network = self.cases[scenario['case']][1]
        replay = self.directory / 'replayed.xml'
        demand(catalog(network), scenario['profile'], 600, scenario['seed'], replay)
        self.assertEqual(digest(replay), scenario['routes_sha256'])

    def test_existing_preparation_preserved(self):
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            wave.prepare(self.path.parent)
        self.assertEqual(before, self.path.read_bytes())

    def test_changed_network_and_route_refused_before_output(self):
        scenario = self.suite['scenarios'][0]
        for path in (self.cases[scenario['case']][1], self.path.parent / scenario['routes']):
            original = path.read_bytes()
            try:
                path.write_bytes(original + b'\n')
                output = self.directory / 'rejected'
                with patch.object(wave.runner, 'run') as run:
                    with self.assertRaises(ValueError):
                        wave.run_wave(self.path, output)
                    run.assert_not_called()
                self.assertFalse(output.exists())
            finally:
                path.write_bytes(original)

    def test_changed_plan_or_source_refused(self):
        original = self.path.read_bytes()
        try:
            for field in ('plan', 'preparer_sha256'):
                changed = read_json(self.path)
                changed[field] = 'changed'
                write_json(self.path, changed)
                with self.assertRaisesRegex(ValueError, 'changed'):
                    wave.preflight(self.path)
                self.path.write_bytes(original)
        finally:
            self.path.write_bytes(original)

    def test_changed_policy_refused(self):
        policy, model, identity = wave.validated_policy()
        with patch.object(wave, 'validated_policy', return_value=(policy, model, dict(identity, seed=99))):
            with self.assertRaisesRegex(ValueError, 'changed'):
                wave.preflight(self.path)


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'runs'
        self.suite = dict(scenarios=[dict(id=f"{g['id']}_{p}_s{i:02d}", case=g['id'], profile=p, sample=i)
                         for g in wave.GEOMETRIES for p in wave.PROFILES for i in range(2)])
        self.cases = {g['id']: ({}, None, {}) for g in wave.GEOMETRIES}
        self.fp = dict(test='unchanged')
        self.calls = []

    def fake_run(self, suite, scenario, controller, directory, **kwargs):
        self.calls.append((scenario, controller, kwargs))
        directory.mkdir(parents=True)

    def fake_verify(self, directory, scenario, spec, fingerprint, controller):
        return dict(scenario=scenario['id'], controller=controller, profile=scenario['profile'],
                    sample=scenario['sample'], mean_queue_vehicles=8 if controller == 'smart' else 10,
                    max_queue_vehicles=15, arrived_vehicles=20, scheduled_vehicles=20,
                    emergency_braking_warnings=0), []

    def run_fake(self, verifier=None, **kwargs):
        with patch.object(wave, 'preflight', return_value=(self.suite, self.cases, self.fp)), \
             patch.object(wave.runner, 'run', side_effect=self.fake_run), \
             patch.object(wave.diversity_wave, 'verify_run', side_effect=verifier or self.fake_verify), \
             redirect_stdout(io.StringIO()):
            return wave.run_wave(Path('fake-suite.json'), self.output, **kwargs)

    def test_144_paired_runs_use_validated_policy_without_training(self):
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len({(s,c) for s,c,_ in self.calls}), 144)
        self.assertEqual(len(self.calls), 144)
        self.assertTrue(all(k == {'smart_policy': 'validated'} for _,_,k in self.calls))
        self.assertEqual(result['additional_training'], 0)
        self.assertTrue(result['summary']['target_met'])

    def test_safety_failure_stops_before_second_run_and_refuses_retry(self):
        def failed(*args):
            raise wave.common.SafetyHold('Teleport')
        with self.assertRaises(wave.common.SafetyHold):
            self.run_fake(verifier=failed)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read_json(self.output / 'manifest.json')['status'], 'safety_hold')
        with self.assertRaisesRegex(ValueError, 'failure needs inspection'):
            self.run_fake(resume=True)
        self.assertEqual(len(self.calls), 1)

    def test_completed_resume_reaudits_without_new_runs(self):
        self.run_fake()
        self.calls.clear()
        self.assertEqual(self.run_fake(resume=True)['actual_new_runs'], 144)
        self.assertEqual(self.calls, [])

    def test_changed_resume_rejected_before_live_call(self):
        self.run_fake()
        self.calls.clear()
        self.fp = dict(test='changed')
        with self.assertRaisesRegex(ValueError, 'Resume'):
            self.run_fake(resume=True)
        self.assertEqual(self.calls, [])

    def test_existing_output_refused_without_resume(self):
        self.run_fake()
        self.calls.clear()
        with self.assertRaises(FileExistsError):
            self.run_fake()
        self.assertEqual(self.calls, [])

    def test_wall_boundary_checkpoint_launches_no_new_run(self):
        with patch.object(wave.time, 'monotonic', side_effect=[0,100]):
            result = self.run_fake(max_wall_seconds=10)
        self.assertEqual(result['status'], 'checkpoint')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
