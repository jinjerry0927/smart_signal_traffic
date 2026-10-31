"""Topology, observation/control parity, integrity, review and summary checks."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import unseen_transfer_build as build
from tools import unseen_transfer_experiment as experiment
from tools.unseen_transfer_runtime import controller
from tools.verify_unseen_transfer import ReplayClock, replay_action, model_scores
import numpy as np
from transferable_signal.control import SignalClock
from transferable_signal.spec import read_json, digest
from transferable_signal.suite import demand


class UnseenTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.inputs = Path(cls.temporary.name) / 'inputs'
        with contextlib.redirect_stdout(io.StringIO()):
            cls.suite = build.prepare(cls.inputs)
            _, cls.cases = build.preflight(cls.inputs / 'suite.json')
        cls.plan = read_json(build.BUNDLE / 'protocol.json')
        cls.model = read_json(ROOT / build.audit.RELEASE / 'model.json')

    def test_three_preregistered_networks_and_all_approach_lanes_served(self):
        self.assertEqual(len(self.cases), 3)
        self.assertEqual(len(self.suite['scenarios']), 27)
        for target in self.plan['targets']:
            spec, network, _ = self.cases[target['id']]
            descriptor = build.check_novelty(target, network, read_json(build.BUNDLE / 'history.json'), self.plan)
            self.assertEqual(len(spec['junctions']), target['signal_count'])
            actual = ET.parse(network).getroot()
            for j in spec['junctions']:
                incoming_edges = {spec['lanes'][m['incoming']]['edge'] for m in j['movements']}
                all_lanes = {l.get('id') for e in actual.findall('edge') if e.get('id') in incoming_edges for l in e.findall('lane')}
                self.assertEqual(all_lanes, {m['incoming'] for m in j['movements']})
                for phase in j['phases']:
                    self.assertTrue(set(phase['state']) <= {'G', 'r'})
            self.assertEqual(descriptor, self.suite['novelty'][target['id']])

    def test_demand_reproducibility_and_frozen_schedule(self):
        scenario = self.suite['scenarios'][10]
        folder = self.inputs / scenario['case']
        routes = read_json(folder / 'route-catalog.json')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'demand.rou.xml'
            scheduled = demand(routes, scenario['profile'], 600, scenario['seed'], path)
            self.assertEqual(digest(path), scenario['routes_sha256'])
            self.assertEqual(scheduled, scenario['scheduled'])
        self.assertEqual(len({s['seed'] for s in self.suite['scenarios']}), 27)

    def test_changed_input_is_rejected_and_existing_input_not_overwritten(self):
        path = self.inputs / self.suite['scenarios'][0]['routes']
        original = path.read_bytes()
        try:
            path.write_bytes(original + b'\n')
            with self.assertRaisesRegex(ValueError, 'Frozen new input changed'):
                build.preflight(self.inputs / 'suite.json')
        finally:
            path.write_bytes(original)
        with self.assertRaises(FileExistsError):
            build.prepare(self.inputs)

    def test_independent_clock_and_six_policy_replays(self):
        rng = np.random.default_rng(20261028)
        for count in [3, 4]:
            phases = [dict(state=''.join('G' if i == j else 'r' for j in range(count))) for i in range(count)]
            for name in self.plan['execution']['run_order']:
                production = SignalClock(phases, self.plan['limits'])
                independent = ReplayClock(phases, self.plan['limits'])
                policy, plans = controller(name, self.plan['limits']), {}
                for index in range(90):
                    features = rng.uniform(0, .5, size=(count, 7)).astype(np.float32)
                    features[:, 1] = [.05, .1, .10000001, .2][index % 4]
                    features[:, 4] = np.asarray(independent.red_age) / 180
                    features[:, 5:] = 0
                    if independent.stage == 'green':
                        features[independent.current, 5:] = [independent.elapsed / 60, 1]
                    pressure = rng.integers(-3, 8, size=count).astype(float)
                    critical = rng.uniform(0, .5, size=count)
                    observation = dict(features=features, pressure=pressure, critical_ratio=critical, mask=production.mask())
                    expected = replay_action(name, features, pressure, critical, independent, self.model,
                                             self.plan['policy_config'], plans, 'test')
                    actual = policy.action('test', observation, production)
                    self.assertEqual(actual, expected, (count, name, index))
                    self.assertEqual(production.mask().tolist(), independent.mask())
                    production.request(actual)
                    independent.request(expected)
                    for _ in range(5):
                        production.tick()
                        independent.tick()
                        self.assertEqual(production.state(), independent.signal())
                        self.assertEqual(production.red_age.tolist(), independent.red_age)

    def test_pressure_diagnostic_does_not_call_learned_forward(self):
        phases = [dict(state=s) for s in ['Grr', 'rGr', 'rrG']]
        clock = SignalClock(phases, self.plan['limits'])
        clock.elapsed = 35
        policy = controller('rules_pressure', self.plan['limits'])
        observation = dict(features=np.full((3, 7), .2, dtype=np.float32), pressure=np.array([1., 5., 2.]), mask=clock.mask())
        with patch('transferable_signal.model.SmartSignal.scores', side_effect=AssertionError('learned scorer must not run')):
            self.assertEqual(policy.action('test', observation, clock), 1)
        self.assertIsNone(policy.diagnostic_pressure)

    def test_webster_numpy_scalar_rounding_at_five_second_ceil_boundary(self):
        phases = [dict(state=''.join('G' if i == j else 'r' for j in range(4))) for i in range(4)]
        production, independent = SignalClock(phases, self.plan['limits']), ReplayClock(phases, self.plan['limits'])
        critical = [.1, .12222222222222222, .16666666666666666, .1111111111111111]
        policy, plans = controller('webster', self.plan['limits']), {}
        features, pressure = np.zeros((4, 7)), np.zeros(4)
        for i in range(0, 100, 5):
            observation = dict(features=features, pressure=pressure, critical_ratio=np.asarray(critical), mask=production.mask())
            actual = policy.action('j', observation, production)
            expected = replay_action('webster', features, pressure, critical, independent, self.model, self.plan['policy_config'], plans, 'j')
            self.assertEqual(actual, expected)
            self.assertEqual(policy.plans['j'], plans['j'])
            self.assertEqual(plans['j'], [15, 15, 25, 15])
            production.request(actual); independent.request(expected)
            for _ in range(5):
                production.tick(); independent.tick()

    def test_final_cannot_run_before_separate_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            evaluation = folder / 'evaluation.json'
            evaluation.write_text(json.dumps(dict(fingerprint={'frozen': True})), encoding='utf-8')
            with patch.object(experiment, 'EVALUATION', evaluation), patch.object(experiment, 'REVIEW', folder / 'absent.json'), \
                 patch.object(experiment, 'fingerprint', return_value={'frozen': True}), patch.object(experiment, 'run_one') as run:
                with self.assertRaises(FileNotFoundError):
                    experiment.run_batch('final', self.inputs, folder / 'output')
                run.assert_not_called()
                self.assertFalse((folder / 'output').exists())

    def test_completed_raw_tampering_and_wrong_controller_pair_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            network, routes = folder / 'net.xml', folder / 'routes.xml'
            network.write_text('<net/>', encoding='utf-8')
            routes.write_text('<routes/>', encoding='utf-8')
            scenario = dict(id='s', case='c', profile='quiet', routes_sha256=digest(routes))
            path = folder / 's/fixed'
            path.mkdir(parents=True)
            raw = path / 'raw.txt'
            raw.write_text('original trace', encoding='utf-8')
            metadata = dict(status='completed', fingerprint={'frozen': True}, scenario=scenario, controller='fixed',
                            network_sha256=digest(network), routes_sha256=digest(routes), files_sha256={'raw.txt': digest(raw)})
            (path / 'manifest.json').write_text(json.dumps(metadata), encoding='utf-8')
            verification = dict(status='PASS', verification_source_sha256=digest(ROOT / 'tools/verify_unseen_transfer.py'),
                                recomputed_metrics={'mean_queue_vehicles': 1}, warnings=[])
            (path / 'verification.json').write_text(json.dumps(verification), encoding='utf-8')
            record = dict(id='s/fixed', scenario='s', case='c', profile='quiet', controller='fixed',
                          manifest_sha256=digest(path / 'manifest.json'), verification_sha256=digest(path / 'verification.json'),
                          metrics=verification['recomputed_metrics'], warnings=[])
            job = (scenario, 'fixed', ({}, network, {}), routes)
            experiment.verify_completed(record, job, folder, {'frozen': True})
            raw.write_text('changed trace', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'raw output changed'):
                experiment.verify_completed(record, job, folder, {'frozen': True})
            with self.assertRaisesRegex(ValueError, 'planned condition/controller'):
                experiment.verify_completed(record, (scenario, 'webster', ({}, network, {}), routes), folder, {'frozen': True})

    def test_summary_separates_primary_success_and_learning_contribution(self):
        values = dict(fixed=12., webster=12., max_pressure=10.5, smart_v2=10., raw_smart=11., rules_pressure=10.1)
        records = [dict(id=f's{i}/{name}', scenario=f's{i}', case=f'c{i // 9}', profile=['quiet', 'busy', 'directional'][i % 3],
                        controller=name, warnings=[], raw_seconds=900, actions=180,
                        metrics=dict(mean_queue_vehicles=value, arrived_vehicles=100, scheduled_vehicles=100,
                                     not_departed_vehicles=0, departed_not_arrived_vehicles=0))
                   for i in range(27) for name, value in values.items()]
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'final').mkdir()
            (folder / 'final' / experiment.BATCH_NAME).write_text('{}', encoding='utf-8')
            with patch.object(experiment, 'verify_batch', return_value={'records': records}), contextlib.redirect_stdout(io.StringIO()):
                result = experiment.summary(self.inputs, folder)
            self.assertTrue(result['primary_target_met'])
            self.assertFalse(result['learned_contribution_target_met'])
            self.assertEqual(result['actual_final_runs'], 162)
        self.assertEqual(experiment.change(0, 0), 0)
        self.assertIsNone(experiment.change(1, 0))


if __name__ == '__main__':
    unittest.main()
