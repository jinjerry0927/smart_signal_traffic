"""No live simulation: safety, data isolation, inference and synthetic RL checks."""
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import FEATURES
from transferable_signal.control import SignalClock
from transferable_signal.controllers import Baseline, webster_greens
from transferable_signal.environment import Observations, SignalEnvironment
from transferable_signal.learning import PhaseScorer, export_model, update_batch, train
from transferable_signal.model import SmartSignal, inputs
from transferable_signal.spec import DEFAULT_LIMITS, make_spec, validate_limits, write_json
from transferable_signal.suite import load_suite, catalog, demand
from transferable_signal.runner import run, compare

SUITE = ROOT / 'network/transferable_v1/suite.json'


class TransferableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.suite, cls.cases = load_suite(SUITE)
        cls.spec = cls.cases['train_x2'][0]

    def clock(self, count=4):
        phases = [dict(state=''.join('G' if i==j else 'r' for i in range(count))) for j in range(count)]
        return SignalClock(phases, DEFAULT_LIMITS)

    def test_minimum_maximum_and_exact_clearance(self):
        clock = self.clock()
        with self.assertRaises(ValueError):
            clock.request(1)
        for _ in range(15): clock.tick()
        clock.request(1)
        states = []
        for _ in range(5):
            states.append(clock.state())
            clock.tick()
        self.assertEqual(states, ['yrrr'] * 3 + ['rrrr'] * 2)
        self.assertEqual(clock.state(), 'rGrr')
        self.assertEqual(clock.elapsed, 0)
        for _ in range(60): clock.tick()
        self.assertFalse(clock.mask()[1])
        with self.assertRaises(ValueError): clock.request(1)

    def test_starvation_priority_and_illegal_clearance_jump(self):
        clock = self.clock(3)
        clock.elapsed = 15
        clock.red_age[2] = 180
        self.assertEqual(clock.mask().tolist(), [False, False, True])
        clock.request(2)
        with self.assertRaises(ValueError): clock.request(1)
        self.assertEqual(clock.mask().tolist(), [False, False, True])

    def test_limits_reject_unsafe_or_unaligned_values(self):
        for key, value in [('yellow_s', 0), ('all_red_s', 1), ('max_green_s', 14), ('decision_s', float('nan')), ('decision_s', 5.0)]:
            with self.assertRaises(ValueError):
                validate_limits(dict(DEFAULT_LIMITS, **{key: value}))

    def test_all_networks_and_split_separation(self):
        self.assertEqual(len(self.cases), 10)
        self.assertEqual(len(self.suite['scenarios']), 30)
        sets = {split: {s['network_sha256'] for s, _, c in self.cases.values() if c['split']==split}
                for split in ('train', 'validation', 'test')}
        self.assertFalse(sets['train'] & sets['validation'] or sets['train'] & sets['test'])
        self.assertEqual(self.cases['gyeongju'][2]['split'], 'test')
        self.assertEqual({len(j['phases']) for s, _, _ in self.cases.values() for j in s['junctions']}, {3,4})
        for spec, network, case in self.cases.values():
            self.assertEqual(make_spec(network, spec['limits']), spec)
            self.assertTrue(catalog(network))
            if case['id'].startswith(('train_t', 'train_x', 'valid_t', 'valid_x')):
                root = ET.parse(network).getroot()
                receiving = {f"{c.get('to')}_{c.get('toLane')}" for c in root.findall('connection')}
                for edge in root.findall('edge'):
                    if edge.get('from') == 'center':
                        self.assertTrue(all(lane.get('id') in receiving for lane in edge.findall('lane')))

    def test_demand_frozen_reproducible_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ('train_t3', 'gyeongju'):
                routes = catalog(self.cases[name][1])
                path = Path(directory) / (name+'.xml')
                scenario = next(s for s in self.suite['scenarios'] if s['case']==name and s['profile']=='busy')
                count = demand(routes, scenario['profile'], scenario['duration_s'], scenario['seed'], path)
                self.assertEqual(path.read_bytes(), (SUITE.parent/scenario['routes']).read_bytes())
                self.assertEqual(count, scenario['scheduled'])
                departures = [float(v.get('depart')) for v in ET.parse(path).findall('vehicle')]
                self.assertEqual(departures, sorted(departures))
                self.assertTrue(all(0 <= d < scenario['duration_s'] for d in departures))

    def test_display_labels_have_no_whitespace_in_ids(self):
        from tools.build_gyeongju_simple import build
        with tempfile.TemporaryDirectory() as directory:
            build(SUITE.parent/'train_chain/layout.json', Path(directory))
            generated = ET.parse(Path(directory)/'labels.add.xml').getroot()
            self.assertEqual([p.get('id') for p in generated], ['Synthetic_0', 'Synthetic_1', 'Synthetic_2'])
        for _, network, _ in self.cases.values():
            labels = network.parent/'labels.add.xml'
            if labels.exists():
                ids = [p.get('id') for p in ET.parse(labels).getroot()]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(not any(c.isspace() for c in identifier) for identifier in ids))

    def test_decorative_labels_loaded_only_with_gui(self):
        spec, network, _ = self.cases['train_chain']
        scenario = next(s for s in self.suite['scenarios'] if s['id']=='train_chain_quiet')
        with tempfile.TemporaryDirectory() as directory:
            for gui in (False, True):
                env = SignalEnvironment(spec, network, SUITE.parent/scenario['routes'], Path(directory)/str(gui), 600, gui=gui)
                with patch('transferable_signal.environment.traci.start') as start, \
                     patch('transferable_signal.environment.traci.getConnection'), \
                     patch.object(env.observer, 'sample'), patch.object(env, 'observe', return_value={}):
                    try:
                        env.reset()
                        command = start.call_args.args[0]
                        self.assertEqual('--additional-files' in command, gui)
                    finally:
                        env.close()

    def test_phase_conflicts_rejected(self):
        _, net, _ = self.cases['train_x2']
        tree = ET.parse(net)
        first = tree.find('.//tlLogic/phase')
        first.set('state', 'G'*len(first.get('state')))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'bad.net.xml'
            tree.write(path)
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                make_spec(path)

    def test_baselines_with_variable_phase_counts(self):
        for count in (3,4):
            limits = DEFAULT_LIMITS
            for ratios in ([0]*count, [.1]*count, [.9]*count):
                greens = webster_greens(ratios, limits)
                self.assertEqual(len(greens), count)
                self.assertTrue(all(15 <= g <= 60 and g%5==0 for g in greens))
            for name in ('fixed','webster','max_pressure'):
                clock = self.clock(count)
                policy = Baseline(name, limits)
                for _ in range(100):
                    obs = dict(mask=clock.mask(), pressure=np.arange(count), critical_ratio=np.ones(count)*.1)
                    action = policy.action('junction', obs, clock)
                    self.assertTrue(clock.mask()[action])
                    clock.request(action)
                    for _ in range(5): clock.tick()

    def test_shared_model_permutation_and_numpy_parity(self):
        torch.manual_seed(3)
        model = PhaseScorer()
        features = np.random.default_rng(8).random((4,len(FEATURES)), dtype=np.float32)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'test-model.json'
            export_model(model, path, dict(updates=1, synthetic_unit_test=True))
            policy = SmartSignal(path)
            with torch.no_grad():
                expected = model(torch.from_numpy(inputs(features))).numpy()
            np.testing.assert_allclose(policy.scores(features), expected, atol=1e-6)
            order = [2,0,3,1]
            np.testing.assert_allclose(policy.scores(features[order]), expected[order], atol=1e-6)
            self.assertEqual(len(policy.scores(features[:3])), 3)
            export_model(model, path, dict(updates=0))
            with self.assertRaises(ValueError): SmartSignal(path)

    def test_ragged_double_dqn_update_on_synthetic_arrays(self):
        model, target = PhaseScorer(), PhaseScorer()
        target.load_state_dict(model.state_dict())
        optimizer = torch.optim.Adam(model.parameters(), lr=.001)
        batch = []
        for n in (3,4,3,4):
            state = np.ones((n,len(FEATURES)), dtype=np.float32)*.2
            mask = np.zeros(n, dtype=bool)
            mask[-1] = True
            batch.append((state, 0, -.2, state.copy(), mask, n==3))
        before = [p.detach().clone() for p in model.parameters()]
        loss = update_batch(model, target, optimizer, batch)
        self.assertTrue(np.isfinite(loss))
        self.assertTrue(any(not torch.equal(a,b) for a,b in zip(before,model.parameters())))

    def test_observation_counts_shared_lanes_once(self):
        observer = Observations(self.spec)
        for lid, data in self.spec['lanes'].items():
            observer.samples[lid] = dict(queue=2, vehicles=3)
            observer.rates[data['edge']] = 600
        j = self.spec['junctions'][0]
        value = observer.junction(j, SignalClock(j['phases'], self.spec['limits']))
        self.assertEqual(value['features'].shape, (4,len(FEATURES)))
        self.assertTrue(np.isfinite(value['features']).all())
        self.assertTrue(np.all(value['pressure']==0))
        self.assertAlmostEqual(value['critical_ratio'][0], 600/2/1800)

    def test_training_dispatch_never_uses_held_out_cases(self):
        seen = []
        class FakeEnvironment:
            def __init__(self, spec, network, routes, output, horizon_s, seed):
                self.output = Path(output)
                self.output.mkdir(parents=True)
                seen.append(Path(network).parent.name)
                self.n = len(spec['junctions'][0]['phases'])
            def reset(self):
                return {'j': dict(features=np.ones((self.n,len(FEATURES)), dtype=np.float32)*.2, mask=np.ones(self.n, dtype=bool))}
            def step(self, actions): return self.reset(), {'j': -.2}, True
            def finish(self): return {'synthetic_test': True}
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory, patch('transferable_signal.learning.SignalEnvironment', FakeEnvironment):
            result = train(SUITE, Path(directory)/'fake-run', episodes=22, batch_size=1)
            self.assertGreater(result['updates'], 0)
            self.assertTrue(all(name.startswith('train_') for name in seen))
            self.assertIn('train_chain', seen)
            self.assertEqual(result['status'], 'completed')
            failed = Path(directory)/'fake-failure'
            with patch.object(FakeEnvironment, 'reset', side_effect=RuntimeError('synthetic failure')):
                with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                    train(SUITE, failed, episodes=1, batch_size=1)
            manifest = json.loads((failed/'manifest.json').read_text())
            self.assertEqual(manifest['status'], 'failed')
            self.assertIn('synthetic failure', manifest['error'])

    def test_lane_change_is_not_counted_as_new_arrival(self):
        observer = Observations(self.spec)
        source = self.spec['junctions'][0]['phases'][0]['incoming']
        now = [0]
        ids = {lid: [] for lid in self.spec['lanes']}
        ids[source[0]] = ['existing']
        connection = SimpleNamespace(
            simulation=SimpleNamespace(getTime=lambda: now[0]),
            lane=SimpleNamespace(getLastStepHaltingNumber=lambda lid: 0,
                                 getLastStepVehicleNumber=lambda lid: len(ids[lid]),
                                 getLastStepVehicleIDs=lambda lid: ids[lid]))
        observer.sample(connection)
        now[0] = 1
        ids[source[0]], ids[source[1]] = [], ['existing']
        observer.sample(connection)
        edge = self.spec['lanes'][source[0]]['edge']
        self.assertEqual(observer.rates[edge], 0)
        now[0] = 2
        ids[source[0]] = ['new']
        observer.sample(connection)
        self.assertEqual(observer.rates[edge], 1800)

    def test_runner_records_results_and_refuses_overwrite(self):
        class FakeEnvironment:
            def __init__(self, spec, network, routes, output, horizon_s, seed, gui=False):
                self.output = Path(output)
                self.clocks = {j['id']: SignalClock(j['phases'], spec['limits']) for j in spec['junctions']}
            def reset(self):
                return {jid: dict(mask=clock.mask()) for jid, clock in self.clocks.items()}
            def step(self, actions): return self.reset(), {}, True
            def finish(self):
                (self.output/'tripinfo.xml').write_text('<tripinfos><tripinfo arrival="10" waitingTime="4"/></tripinfos>')
                return dict(collision_vehicle_events=0, teleport_events=1)
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory, patch('transferable_signal.runner.SignalEnvironment', FakeEnvironment):
            output = Path(directory)/'run'
            result = run(SUITE, 'train_x2_quiet', 'fixed', output)
            self.assertEqual(result['p95_completed_waiting_time_s'], 4)
            self.assertTrue(result['observed_collision_or_teleport'])
            self.assertGreaterEqual(result['mean_decision_ms_per_junction'], 0)
            self.assertEqual(json.loads((output/'manifest.json').read_text())['status'], 'completed')
            with self.assertRaises(FileExistsError):
                run(SUITE, 'train_x2_quiet', 'fixed', output)

    def test_final_test_contamination_rejected_before_any_run(self):
        with tempfile.TemporaryDirectory() as directory, patch('transferable_signal.runner.SignalEnvironment') as env:
            root = Path(directory)
            model = root/'contaminated.json'
            export_model(PhaseScorer(), model, dict(updates=1, training_network_sha256=[self.cases['gyeongju'][0]['network_sha256']]))
            with self.assertRaisesRegex(ValueError, 'Final-test'):
                compare(SUITE, 'test', root/'rejected', model)
            with self.assertRaisesRegex(ValueError, 'Final-test'):
                run(SUITE, 'gyeongju_quiet', 'smart', root/'rejected', model)
            env.assert_not_called()
            self.assertFalse((root/'rejected').exists())


if __name__ == '__main__':
    unittest.main()
