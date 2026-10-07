"""No live SUMO: default deployment, fail-fast checks, and V2 action parity."""
import contextlib
import copy
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import FEATURES, VERSION
from transferable_signal import deployment, runner
from transferable_signal.__main__ import main
from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2
from transferable_signal.control import SignalClock
from transferable_signal.model import SmartSignal
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json, write_json
from transferable_signal.suite import load_suite


def clock(elapsed=35):
    value = SignalClock([dict(state=state) for state in ('Grr', 'rGr', 'rrG')], dict(DEFAULT_LIMITS))
    value.elapsed = elapsed
    return value


def observation(value, density=.2):
    features = np.zeros((3, len(FEATURES)), dtype=float)
    features[:, 0] = .1
    features[:, 1] = density
    features[value.current, 6] = 1
    return dict(features=features, mask=value.mask(), pressure=np.array([0., 2., 1.]),
                critical_ratio=np.full(3, .1))


class FakeEnvironment:
    def __init__(self, spec, network, routes, output, horizon_s, seed, gui=False):
        self.output = Path(output)
        self.clocks = {'j': clock()}

    def reset(self):
        return {'j': observation(self.clocks['j'])}

    def step(self, actions):
        self.clocks['j'].request(actions['j'])
        return self.reset(), {}, True

    def finish(self):
        (self.output / 'tripinfo.xml').write_text('<tripinfos><tripinfo arrival="10" waitingTime="4"/></tripinfos>')
        return dict(mean_queue_vehicles=2, collision_vehicle_events=0, teleport_events=0)

    def close(self):
        pass


class DefaultCliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.suite_path = self.root / 'suite.json'
        self.suite = dict(limits=dict(DEFAULT_LIMITS), scenarios=[dict(
            id=f's{i}', case=f'c{i}', split='validation', routes=f'r{i}.xml',
            routes_sha256=str(i), duration_s=10, drain_s=0, seed=i) for i in range(2)])
        self.cases = {f'c{i}': (dict(limits=dict(DEFAULT_LIMITS), network_sha256=f'held-out-{i}'),
                              self.root / f'n{i}.xml', dict(split='validation')) for i in range(2)}
        write_json(self.suite_path, self.suite)
        self.env = self.enterContext(patch.object(runner, 'SignalEnvironment', side_effect=FakeEnvironment))
        self.enterContext(patch.object(runner, 'load_suite', return_value=(self.suite, self.cases)))
        self.enterContext(patch.object(runner.subprocess, 'check_output', return_value='SUMO fake\n'))
        self.model = ROOT / 'results/runs/transferable_train_150_seed43/model.json'
        self.raw_model = self.root / 'raw.json'
        weights = [0.] * (2 * len(FEATURES))
        weights[6] = 1.
        write_json(self.raw_model, dict(version=VERSION, features=list(FEATURES),
            training=dict(updates=1, training_network_sha256=['trained-network']),
            layers=[dict(weights=[weights], bias=[0.]),
                    dict(weights=[[1.]], bias=[0.]), dict(weights=[[1.]], bias=[0.])]))

    def invoke(self, *arguments):
        with patch.object(sys, 'argv', ['transferable_signal', *arguments]), contextlib.redirect_stdout(io.StringIO()):
            main()

    def test_cli_default_run_resolves_selected_model_and_records_identity(self):
        output = self.root / 'run'
        self.invoke('run', '--suite', str(self.suite_path), '--scenario', 's0',
                    '--controller', 'smart', '--out', str(output))
        metadata = read_json(output / 'manifest.json')
        identity = metadata['smart_policy']
        self.assertEqual(identity['mode'], 'validated')
        self.assertEqual(identity['seed'], 43)
        self.assertEqual(identity['model_sha256'], digest(self.model))
        self.assertEqual(identity['profile_sha256'], digest(deployment.PROFILE_PATH))
        self.assertEqual(identity['profile_identity_sha256'], deployment.PROFILE_IDENTITY_SHA256)
        self.assertEqual(identity['factory_sha256'], digest(Path(deployment.__file__)))
        self.assertEqual(metadata['adaptive_policy']['variant'], 'load_gate_pressure_wait35')
        self.assertEqual(metadata['adaptive_policy']['config']['wait_green_s'], 35)
        self.assertEqual(metadata['model_sha256'], identity['model_sha256'])
        self.assertEqual(metadata['status'], 'completed')
        self.env.assert_called_once()

    def test_legacy_default_suite_without_global_limits_runs_and_compares(self):
        default_suite = ROOT / 'network/transferable_v1/suite.json'
        legacy_suite, legacy_cases = load_suite(default_suite)
        self.assertNotIn('limits', legacy_suite)
        output = self.root / 'legacy-default'
        with patch.object(runner, 'load_suite', return_value=(legacy_suite, legacy_cases)) as loaded:
            self.invoke('run', '--scenario', 'gyeongju_busy', '--controller', 'smart', '--out', str(output))
            loaded.assert_called_once_with(default_suite.resolve())
        self.assertEqual(read_json(output / 'manifest.json')['smart_policy']['mode'], 'validated')
        self.env.assert_called_once()
        self.suite.pop('limits')
        runner.compare(self.suite_path, 'validation', self.root / 'legacy-compare', smart_policy='validated')
        self.assertEqual(self.env.call_count, 9)
        self.cases['c0'][0]['limits']['min_green_s'] = 20
        self.assert_rejected_before_output(message='limits')
        self.assert_rejected_before_output(message='limits', compare=True)

    def test_cli_compare_default_has_four_controllers_and_unchanged_csv_schema(self):
        output = self.root / 'compare'
        self.invoke('compare', '--suite', str(self.suite_path), '--out', str(output))
        with (output / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 8)
        self.assertEqual([row['controller'] for row in rows[:4]], list(runner.CONTROLLERS))
        baseline = read_json(output / 's0/fixed/metrics.json')
        smart = read_json(output / 's0/smart/metrics.json')
        self.assertEqual(set(baseline), set(smart))
        self.assertEqual(set(rows[0]), set(baseline))
        self.assertNotIn('smart_policy', smart)
        self.assertEqual(read_json(output / 's0/smart/manifest.json')['smart_policy']['mode'], 'validated')
        for name in runner.CONTROLLERS[:-1]:
            metadata = read_json(output / f's0/{name}/manifest.json')
            self.assertIsNone(metadata['model_sha256'])
            self.assertNotIn('smart_policy', metadata)
            self.assertNotIn('adaptive_policy', metadata)

    def test_python_api_and_cli_raw_preserve_original_policy_and_compare_defaults(self):
        with patch.object(runner, 'SmartSignal', wraps=SmartSignal) as raw:
            runner.run(self.suite_path, 's0', 'smart', self.root / 'api', self.raw_model)
            raw.assert_called_once_with(self.raw_model.resolve())
        self.assertEqual(read_json(self.root / 'api/manifest.json')['smart_policy']['mode'], 'raw')
        self.invoke('run', '--suite', str(self.suite_path), '--scenario', 's0', '--controller', 'smart',
                    '--smart-policy', 'raw', '--model', str(self.raw_model), '--out', str(self.root / 'cli-raw'))
        self.assertNotIn('adaptive_policy', read_json(self.root / 'cli-raw/manifest.json'))
        for label, arguments in (('raw-base', {}), ('raw-all', dict(model_path=self.raw_model))):
            output = self.root / label
            runner.compare(self.suite_path, 'validation', output, **arguments)
            with (output / 'comparison.csv').open(encoding='utf-8-sig') as stream:
                controllers = {row['controller'] for row in csv.DictReader(stream)}
            self.assertEqual(controllers, set(runner.CONTROLLERS if arguments else runner.CONTROLLERS[:-1]))

    def test_baseline_only_does_not_load_profile_or_model(self):
        with patch.object(runner, 'validated_policy', side_effect=AssertionError('baseline requested model')):
            runner.run(self.suite_path, 's0', 'fixed', self.root / 'base', smart_policy='validated')
            runner.compare(self.suite_path, 'validation', self.root / 'bases',
                           controllers=['fixed', 'max_pressure'], smart_policy='validated')
        with self.assertRaisesRegex(ValueError, 'only by SmartSignal'):
            runner.run(self.suite_path, 's0', 'fixed', self.root / 'invalid-base', self.raw_model)

    def assert_rejected_before_output(self, *, model_path=None, smart_policy='validated', message='.', compare=False):
        output = self.root / 'rejected'
        self.env.reset_mock()
        with self.assertRaisesRegex(ValueError, message):
            if compare:
                runner.compare(self.suite_path, self.suite['scenarios'][0]['split'], output,
                               model_path=model_path, smart_policy=smart_policy)
            else:
                runner.run(self.suite_path, 's0', 'smart', output, model_path=model_path, smart_policy=smart_policy)
        self.env.assert_not_called()
        self.assertFalse(output.exists())

    def test_missing_raw_model_and_wrong_or_missing_validated_model_fail_fast(self):
        for comparing in (False, True):
            if not comparing:
                self.assert_rejected_before_output(smart_policy='raw', message='--model')
            else:
                with self.assertRaisesRegex(ValueError, '--model'):
                    runner.compare(self.suite_path, 'validation', self.root / 'rejected',
                                   controllers=['smart'], smart_policy='raw')
                self.assertFalse((self.root / 'rejected').exists())
                self.env.assert_not_called()
            self.assert_rejected_before_output(model_path=self.root / 'missing.json', message='missing', compare=comparing)
            self.assert_rejected_before_output(model_path=self.raw_model, message='--smart-policy raw', compare=comparing)
        original_is_file = Path.is_file
        with patch.object(Path, 'is_file', autospec=True, side_effect=lambda path:
                          False if path == self.model else original_is_file(path)):
            for comparing in (False, True):
                self.assert_rejected_before_output(message='model is missing', compare=comparing)

    def test_same_model_copy_allowed_and_default_path_independent_of_working_directory(self):
        model_copy = self.root / 'model-copy.json'
        model_copy.write_bytes(self.model.read_bytes())
        policy, resolved, identity = deployment.validated_policy(model_copy)
        self.assertIsInstance(policy, AdaptiveSmartSignalV2)
        self.assertEqual(resolved, model_copy)
        self.assertEqual(identity['model_sha256'], digest(self.model))
        relocated = self.root / 'relocated'
        location = relocated / 'results/runs/transferable_train_150_seed43/model.json'
        location.parent.mkdir(parents=True)
        location.write_bytes(self.model.read_bytes())
        profile = read_json(deployment.PROFILE_PATH)
        for relative in profile['source_sha256']:
            target = relocated / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        relocated_profile = relocated / 'configs/smart_signal_default.json'
        relocated_profile.parent.mkdir()
        relocated_profile.write_bytes(deployment.PROFILE_PATH.read_bytes())
        with patch.object(deployment, 'ROOT', relocated), patch.object(deployment, 'PROFILE_PATH', relocated_profile):
            _, resolved, _ = deployment.validated_policy()
        self.assertEqual(resolved, location)

    def test_profile_formatting_is_portable_but_changed_selection_is_rejected(self):
        profile = read_json(deployment.PROFILE_PATH)
        profile_path = self.root / 'profile.json'
        profile_path.write_bytes(json.dumps(profile, sort_keys=True, indent=4).replace('\n', '\r\n').encode('utf-8'))
        with patch.object(deployment, 'PROFILE_PATH', profile_path):
            _, _, identity = deployment.validated_policy()
            self.assertEqual(identity['profile_identity_sha256'], deployment.PROFILE_IDENTITY_SHA256)
            self.assertEqual(identity['profile_sha256'], digest(profile_path))
        for field, value in (('variant', 'load_gate_pressure_wait40'),
                             ('policy_config', dict(profile['policy_config'], wait_green_s=40))):
            changed = dict(profile, **{field: value})
            write_json(profile_path, changed)
            with patch.object(deployment, 'PROFILE_PATH', profile_path):
                for comparing in (False, True):
                    self.assert_rejected_before_output(message='profile SHA256', compare=comparing)

    def test_missing_profile_live_config_limits_and_source_drift_fail_fast(self):
        with patch.object(deployment, 'PROFILE_PATH', self.root / 'missing-profile.json'):
            self.assert_rejected_before_output(message='profile is missing')
        variant = 'load_gate_pressure_wait35'
        changed_config = {variant: dict(deployment.VARIANTS_V2[variant], wait_green_s=40)}
        original_digest = digest
        for context, message in (
            (patch.object(deployment, 'VARIANTS_V2', changed_config), 'variant/config'),
            (patch.object(deployment, 'DEFAULT_LIMITS', dict(DEFAULT_LIMITS, max_green_s=65)), 'DEFAULT_LIMITS'),
            (patch.object(deployment, 'digest', side_effect=lambda path: 'changed' if Path(path).name == 'adaptive_v2.py'
                          else original_digest(path)), 'source SHA256')):
            with context:
                for comparing in (False, True):
                    self.assert_rejected_before_output(message=message, compare=comparing)

    def test_all_selected_scenario_limits_are_checked_before_first_baseline(self):
        self.cases['c1'][0]['limits']['max_green_s'] = 65
        self.assert_rejected_before_output(message='limits', compare=True)
        self.cases['c0'][0]['limits']['min_green_s'] = 20
        self.assert_rejected_before_output(message='limits')
        self.cases['c0'][0]['limits'] = dict(DEFAULT_LIMITS)
        self.suite['limits']['max_green_s'] = 65
        self.assert_rejected_before_output(message='limits')

    def test_final_test_leakage_for_selected_later_scenario_fails_before_any_baseline(self):
        trained_network = read_json(self.model)['training']['training_network_sha256'][0]
        for scenario in self.suite['scenarios']:
            scenario['split'] = 'test'
        self.cases['c1'][0]['network_sha256'] = trained_network
        self.assert_rejected_before_output(message='Final-test', compare=True)
        self.cases['c0'][0]['network_sha256'] = trained_network
        self.assert_rejected_before_output(message='Final-test')
        self.cases['c0'][0]['network_sha256'] = 'trained-network'
        self.assert_rejected_before_output(model_path=self.raw_model, smart_policy='raw', message='Final-test')

    def test_unknown_duplicate_empty_controllers_and_existing_output_fail_fast(self):
        for controllers in (['fixed', 'fixed'], ['unknown'], []):
            with self.assertRaisesRegex(ValueError, 'controller'):
                runner.compare(self.suite_path, 'validation', self.root / 'rejected', controllers=controllers,
                               smart_policy='validated')
        self.env.assert_not_called()
        self.assertFalse((self.root / 'rejected').exists())
        output = self.root / 'existing'
        output.mkdir()
        sentinel = output / 'keep.txt'
        sentinel.write_text('keep')
        for comparing in (False, True):
            with self.assertRaises(FileExistsError):
                if comparing:
                    runner.compare(self.suite_path, 'validation', output, smart_policy='validated')
                else:
                    runner.run(self.suite_path, 's0', 'smart', output, smart_policy='validated')
        self.env.assert_not_called()
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_duplicate_scenario_id_fails_before_output_even_across_splits(self):
        self.suite['scenarios'][1]['id'] = 's0'
        for split in ('validation', 'test'):
            self.suite['scenarios'][1]['split'] = split
            self.assert_rejected_before_output(message='Duplicate scenario', compare=True)

    def test_validated_execution_preserves_global_raw_factory_and_specialized_patch(self):
        original = runner.SmartSignal
        runner.run(self.suite_path, 's0', 'smart', self.root / 'validated', smart_policy='validated')
        self.assertIs(runner.SmartSignal, original)
        with patch.object(runner, 'SmartSignal', side_effect=lambda path:
                AdaptiveSmartSignalV2(path, 'load_gate_fixed_wait40')) as specialized:
            runner.run(self.suite_path, 's0', 'smart', self.root / 'specialized', self.model)
            specialized.assert_called_once()
        self.assertIs(runner.SmartSignal, original)

    def test_cli_expected_errors_have_actionable_message_and_runtime_errors_propagate(self):
        with contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as stopped:
                self.invoke('run', '--scenario', 's0', '--controller', 'smart', '--smart-policy', 'raw',
                            '--out', str(self.root / 'bad-cli'))
        self.assertEqual(stopped.exception.code, 2)
        self.assertIn('--model', error.getvalue())
        self.assertNotIn('Traceback', error.getvalue())
        with patch.object(runner, 'run', side_effect=RuntimeError('unexpected')):
            with self.assertRaisesRegex(RuntimeError, 'unexpected'):
                self.invoke('run', '--scenario', 's0', '--out', str(self.root / 'runtime'))

    def test_factory_matches_frozen_v2_low_high_guards_and_legal_transitions(self):
        deployed, _, _ = deployment.validated_policy()
        direct = AdaptiveSmartSignalV2(self.model, 'load_gate_pressure_wait35')
        for policy in (deployed, direct):
            policy.scores = lambda features: features[:, 6]
        observations = []
        value = clock(35)
        observations.append((value, observation(value, .05), 1, 'low_pressure', False, False))
        value = clock(30)
        observations.append((value, observation(value, .2), 0, 'ai_queue_guard', False, False))
        value = clock(30)
        obs = observation(value, .11)
        obs['features'][1, 0] = .2
        observations.append((value, obs, 1, 'ai_queue_guard', True, False))
        value = clock(35)
        obs = observation(value, .2)
        obs['features'][2, 4] = .4
        observations.append((value, obs, 2, 'ai_queue_guard', False, True))
        for value, obs, expected, mode, queue_guard, wait_guard in observations:
            before = copy.deepcopy(obs)
            for policy in (deployed, direct):
                self.assertEqual(policy.action('j', obs, value), expected)
                self.assertEqual(policy.last_decision['mode'], mode)
                self.assertEqual(policy.last_decision['queue_guard_triggered'], queue_guard)
                self.assertEqual(policy.last_decision['wait_guard_triggered'], wait_guard)
            self.assertEqual(deployed.last_decision, direct.last_decision)
            for key in obs:
                np.testing.assert_array_equal(obs[key], before[key])
        value = clock(0)
        for stage in ('minimum', 'maximum', 'starvation', 'clearance'):
            if stage == 'maximum':
                value.elapsed = 60
            elif stage == 'starvation':
                value.elapsed = 25
                value.red_age[2] = 180
            elif stage == 'clearance':
                value.request(2)
            obs = observation(value, .2)
            expected = direct.action('j', obs, value)
            self.assertEqual(deployed.action('j', obs, value), expected)
            self.assertTrue(value.mask()[expected])
        for _ in range(5):
            value.tick()
            obs = observation(value)
            self.assertEqual(deployed.action('j', obs, value), direct.action('j', obs, value))


if __name__ == '__main__':
    unittest.main()
