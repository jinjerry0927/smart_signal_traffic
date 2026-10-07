"""Episode-boundary resume checks with fake environments; no live SUMO steps."""
import copy
import csv
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import VERSION, FEATURES
from transferable_signal.learning import train
from transferable_signal.spec import digest, write_json


class FakeEnvironment:
    actions = []
    fail_seed = None

    def __init__(self, spec, network, routes, output, horizon_s, seed):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        self.seed, self.n, self.step_number, self.previous = seed, spec['phase_count'], 0, 0
        self.base = np.random.default_rng(seed).random((self.n, len(FEATURES)), dtype=np.float32)

    def observe(self):
        mask = np.ones(self.n, dtype=bool)
        mask[self.step_number % self.n] = False
        state = self.base + np.float32((self.step_number + self.previous) / 20)
        return {'synthetic': dict(features=state, mask=mask)}

    def reset(self):
        return self.observe()

    def step(self, actions):
        FakeEnvironment.actions.append((self.seed, self.step_number, actions['synthetic']))
        self.previous = actions['synthetic']
        self.step_number += 1
        # Deliberate RNG use verifies torch RNG restoration as well as random.Random.
        reward = -float(torch.rand(())) - self.previous / 10
        if self.seed == FakeEnvironment.fail_seed and self.step_number == 2:
            (self.output / 'partial.txt').write_text('preserve interrupted attempt')
            raise KeyboardInterrupt('synthetic interruption')
        return self.observe(), {'synthetic': reward}, self.step_number == 4

    def finish(self):
        return {'synthetic_test': True}

    def close(self):
        pass


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.suite_path = self.root / 'suite.json'
        self.suite_path.write_text('{"synthetic": true}')
        self.suite = dict(scenarios=[dict(id=f'train_{n}', case=f'case_{n}', split='train',
                                           routes=f'route_{n}.xml', duration_s=4) for n in (3, 4)])
        self.cases = {f'case_{n}': (dict(phase_count=n, network_sha256=f'network_{n}'),
                                     self.root / f'network_{n}.xml', {}) for n in (3, 4)}
        FakeEnvironment.actions, FakeEnvironment.fail_seed = [], None
        for target, replacement in [('transferable_signal.learning.SignalEnvironment', FakeEnvironment),
                                    ('transferable_signal.learning.load_suite', self.load),
                                    ('transferable_signal.runner.provenance', self.provenance)]:
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.stdout = redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)

    def load(self, path):
        return copy.deepcopy(self.suite), copy.deepcopy(self.cases)

    def provenance(self, path, suite, controller, model):
        files = ('learning.py', 'model.py', 'environment.py', 'control.py', 'spec.py')
        return dict(version=VERSION, suite_sha256=digest(path), frozen_demand={'synthetic': 'route-hash'},
                    code_sha256={name: digest(ROOT / 'transferable_signal' / name) for name in files},
                    sumo='synthetic-no-sumo')

    def train(self, name, episodes=30, seed=42, **kwargs):
        return train(self.suite_path, self.root / name, episodes=episodes, seed=seed, batch_size=2, **kwargs)

    def checkpoint(self, name):
        return torch.load(self.root / name / 'checkpoint.pt', map_location='cpu', weights_only=True)

    def assert_state_equal(self, a, b):
        if isinstance(a, torch.Tensor):
            self.assertTrue(torch.equal(a, b))
        elif isinstance(a, dict):
            self.assertEqual(a.keys(), b.keys())
            for key in a:
                self.assert_state_equal(a[key], b[key])
        elif isinstance(a, (tuple, list)):
            self.assertEqual(len(a), len(b))
            for left, right in zip(a, b):
                self.assert_state_equal(left, right)
        else:
            self.assertEqual(a, b)

    def test_paused_resume_matches_uninterrupted_weights_replay_actions_and_rng(self):
        self.train('whole')
        expected_actions = FakeEnvironment.actions[:]
        expected = self.checkpoint('whole')
        FakeEnvironment.actions = []
        paused = self.train('split', stop_after=11)
        self.assertEqual(paused['status'], 'paused')
        self.assertEqual(paused['completed_episodes'], 11)
        self.assertTrue((self.root / 'split' / 'model.json').exists())
        completed = self.train('split', resume=self.root / 'split' / 'checkpoint.pt')
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(FakeEnvironment.actions, expected_actions)
        actual = self.checkpoint('split')
        for key in ('online', 'target', 'optimizer', 'replay', 'random_state', 'torch_rng_state', 'rows'):
            self.assert_state_equal(expected[key], actual[key])
        self.assertGreater(actual['metadata']['updates'], 100)

    def test_target_extension_preserves_saved_exploration_horizon(self):
        self.train('extended', episodes=4, stop_after=2)
        result = self.train('extended', episodes=7, resume=self.root / 'extended' / 'checkpoint.pt')
        self.assertEqual(result['episodes'], 7)
        self.assertEqual(result['exploration_episodes'], 4)
        rows = self.checkpoint('extended')['rows']
        self.assertEqual(rows[2][3], max(.05, 1 - 2 / (4 * .8)))
        self.assertEqual(rows[-1][3], .05)

    def test_keyboard_interrupt_replays_last_episode_and_preserves_partial_files(self):
        self.train('whole', episodes=5)
        expected = self.checkpoint('whole')
        FakeEnvironment.actions = []
        FakeEnvironment.fail_seed = 44
        with self.assertRaises(KeyboardInterrupt):
            self.train('interrupted', episodes=5)
        output = self.root / 'interrupted'
        manifest = json.loads((output / 'manifest.json').read_text())
        self.assertEqual(manifest['status'], 'paused')
        self.assertEqual(manifest['completed_episodes'], 2)
        self.assertEqual(self.checkpoint('interrupted')['metadata']['completed_episodes'], 2)
        original = (output / 'episode_0002' / 'partial.txt').read_bytes()
        FakeEnvironment.fail_seed = None
        self.train('interrupted', episodes=5, resume=output / 'checkpoint.pt')
        self.assertEqual((output / 'episode_0002' / 'partial.txt').read_bytes(), original)
        self.assertTrue((output / 'episode_0002_retry_001' / 'metrics.json').exists())
        actual = self.checkpoint('interrupted')
        for key in ('online', 'target', 'optimizer', 'replay', 'random_state', 'torch_rng_state', 'rows'):
            self.assert_state_equal(expected[key], actual[key])

    def test_resume_refuses_wrong_seed_suite_algorithm_batch_output_and_json(self):
        self.train('safe', episodes=4, stop_after=2)
        output = self.root / 'safe'
        path = output / 'checkpoint.pt'
        initial = {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()}
        for kwargs in (dict(seed=9), dict(batch_size=3), dict(episodes=3)):
            call = dict(episodes=4, seed=42, batch_size=2, resume=path)
            call.update(kwargs)
            with self.assertRaises(ValueError):
                train(self.suite_path, output, **call)
        self.suite_path.write_text('{"changed": true}')
        with self.assertRaises(ValueError):
            self.train('safe', episodes=4, resume=path)
        self.suite_path.write_text('{"synthetic": true}')
        with patch('transferable_signal.learning._training_contract', return_value={'algorithm': 'changed'}):
            with self.assertRaises(ValueError):
                self.train('safe', episodes=4, resume=path)
        copy_output = self.root / 'copy'
        shutil.copytree(output, copy_output)
        with self.assertRaises(ValueError):
            self.train('copy', episodes=4, resume=copy_output / 'checkpoint.pt')
        with self.assertRaises(ValueError):
            self.train('safe', episodes=4, resume=output / 'model.json')
        with self.assertRaises(FileExistsError):
            self.train('safe', episodes=4)
        self.assertEqual(initial, {p.name: p.read_bytes() for p in output.iterdir() if p.is_file()})

    def test_csv_tampering_is_rejected_without_overwrite(self):
        self.train('safe', episodes=4, stop_after=2)
        output = self.root / 'safe'
        csv_path = output / 'training.csv'
        csv_path.write_text(csv_path.read_text().replace('train_', 'tampered_'))
        before = csv_path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'training.csv differs'):
            self.train('safe', episodes=4, resume=output / 'checkpoint.pt')
        self.assertEqual(csv_path.read_bytes(), before)

    def test_atomic_csv_old_prefix_can_be_recovered_from_checkpoint(self):
        self.train('safe', episodes=4, stop_after=2)
        output = self.root / 'safe'
        csv_path = output / 'training.csv'
        with csv_path.open(newline='') as stream:
            rows = list(csv.reader(stream))
        with csv_path.open('w', newline='') as stream:
            csv.writer(stream).writerows(rows[:2])
        self.train('safe', episodes=4, resume=output / 'checkpoint.pt')
        with csv_path.open(newline='') as stream:
            self.assertEqual(len(list(csv.reader(stream))), 5)

    def test_malformed_replay_and_non_checkpoint_models_fail_before_output_changes(self):
        self.train('safe', episodes=4, stop_after=2)
        output = self.root / 'safe'
        path = output / 'checkpoint.pt'
        saved = self.checkpoint('safe')
        saved['replay']['states'][0, 0, 0] = float('nan')
        torch.save(saved, path)
        original = (output / 'manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'replay'):
            self.train('safe', episodes=4, resume=path)
        self.assertEqual((output / 'manifest.json').read_bytes(), original)
        path.write_text('{"old_inference_only": true}')
        with self.assertRaisesRegex(ValueError, 'Cannot read'):
            self.train('safe', episodes=4, resume=path)
        self.assertEqual((output / 'manifest.json').read_bytes(), original)

    def test_nonfinite_weights_and_changed_optimizer_are_rejected(self):
        self.train('safe', episodes=4, stop_after=2)
        output = self.root / 'safe'
        path = output / 'checkpoint.pt'
        original = path.read_bytes()
        manifest = (output / 'manifest.json').read_bytes()
        saved = self.checkpoint('safe')
        saved['optimizer']['param_groups'][0]['lr'] = .5
        torch.save(saved, path)
        with self.assertRaisesRegex(ValueError, 'optimizer settings'):
            self.train('safe', episodes=4, resume=path)
        path.write_bytes(original)
        saved = self.checkpoint('safe')
        next(iter(saved['online'].values())).flatten()[0] = float('nan')
        torch.save(saved, path)
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            self.train('safe', episodes=4, resume=path)
        self.assertEqual((output / 'manifest.json').read_bytes(), manifest)

    def test_interrupted_manifest_write_preserves_checkpoint_and_previous_manifest(self):
        self.train('whole', episodes=5)
        expected = self.checkpoint('whole')
        self.train('safe', episodes=5, stop_after=2)
        output = self.root / 'safe'
        path = output / 'checkpoint.pt'
        before = {name: (output / name).read_bytes()
                  for name in ('checkpoint.pt', 'manifest.json', 'training.csv')}

        def interrupt_partial_write(destination, data):
            destination = Path(destination)
            if destination.name == 'manifest.json.tmp':
                destination.write_text('{"status": "start')
                raise KeyboardInterrupt('synthetic interrupted manifest write')
            write_json(destination, data)

        with patch('transferable_signal.learning.write_json', side_effect=interrupt_partial_write):
            with self.assertRaisesRegex(KeyboardInterrupt, 'manifest write'):
                self.train('safe', episodes=5, resume=path)
        self.assertEqual(before, {name: (output / name).read_bytes() for name in before})
        self.assertFalse((output / 'manifest.json.tmp').exists())
        self.assertEqual(json.loads((output / 'manifest.json').read_text())['status'], 'paused')
        self.train('safe', episodes=5, resume=path)
        actual = self.checkpoint('safe')
        for key in ('online', 'target', 'optimizer', 'replay', 'random_state', 'torch_rng_state', 'rows'):
            self.assert_state_equal(expected[key], actual[key])


if __name__ == '__main__':
    unittest.main()
