"""No live SUMO: isolate low-load modes and high-load waiting protection."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import FEATURES, VERSION
from transferable_signal.adaptive import AdaptiveSmartSignal
from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2, VARIANTS_V2
from transferable_signal.control import SignalClock
from transferable_signal.spec import DEFAULT_LIMITS


class AdaptiveV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / 'synthetic-model.json'
        weights = [0.] * (2 * len(FEATURES))
        weights[6] = 1.
        cls.path.write_text(json.dumps(dict(
            version=VERSION, features=list(FEATURES), training=dict(updates=1),
            layers=[dict(weights=[weights], bias=[0.]),
                    dict(weights=[[1.]], bias=[0.]), dict(weights=[[1.]], bias=[0.])])), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def clock(self, elapsed=40, count=3):
        phases = [dict(state=''.join('G' if i == j else 'r' for i in range(count)))
                  for j in range(count)]
        clock = SignalClock(phases, DEFAULT_LIMITS)
        clock.elapsed = elapsed
        return clock

    def observation(self, clock, density=.2):
        features = np.zeros((len(clock.phases), len(FEATURES)), dtype=float)
        features[:, 0] = .1
        features[:, 1] = density
        features[clock.current, 6] = 1.
        return dict(features=features, mask=clock.mask(), pressure=np.zeros(len(features)))

    def policy(self, variant='load_gate_pressure_wait40'):
        return AdaptiveSmartSignalV2(self.path, variant)

    def test_configs_frozen_and_named_as_agreed(self):
        self.assertEqual(set(VARIANTS_V2), {'load_gate_pressure_wait40',
                         'load_gate_fixed_wait40', 'load_gate_pressure_wait35'})
        with self.assertRaises(TypeError):
            VARIANTS_V2['new'] = {}
        with self.assertRaises(TypeError):
            VARIANTS_V2['load_gate_fixed_wait40']['density_threshold'] = 1.
        for config in VARIANTS_V2.values():
            self.assertTrue(config['wait_high_load_only'])
            self.assertEqual(json.loads(json.dumps(dict(config)))['version'], 'adaptive-v2')
        with self.assertRaisesRegex(ValueError, 'Unknown adaptive v2 variant'):
            self.policy('invalid')

    def test_low_load_pressure_remains_pure_pressure_even_with_long_waits(self):
        for variant in ('load_gate_pressure_wait40', 'load_gate_pressure_wait35'):
            clock = self.clock(elapsed=45)
            obs = self.observation(clock, .10)
            obs['features'][1, 4] = .9
            obs['pressure'][:] = [5., -1., 0.]
            policy = self.policy(variant)
            self.assertEqual(policy.action('x', obs, clock), 0)
            self.assertEqual(policy.last_decision['mode'], 'low_pressure')
            self.assertFalse(policy.last_decision['wait_guard_triggered'])
            obs['pressure'][:] = [0., 2., 1.]
            self.assertEqual(policy.action('x', obs, clock), 1)

    def test_low_load_fixed_has_exact_twenty_five_second_transition(self):
        policy = self.policy('load_gate_fixed_wait40')
        for elapsed, expected in ((15, 0), (24, 0), (25, 1), (45, 1)):
            clock = self.clock(elapsed=elapsed)
            obs = self.observation(clock, .05)
            obs['features'][2, 4] = .9
            obs['pressure'][:] = [0., -100., 100.]
            self.assertEqual(policy.action('x', obs, clock), expected)
            self.assertEqual(policy.last_decision['mode'], 'low_fixed')
            self.assertFalse(policy.last_decision['wait_guard_triggered'])
        clock.red_age[2] = 180
        obs = self.observation(clock, .05)
        self.assertEqual(policy.action('x', obs, clock), 2)

    def test_high_load_keeps_v1_behavior_before_wait_threshold(self):
        for density in (.1001, .11, .2):
            clock = self.clock(elapsed=30)
            obs = self.observation(clock, density)
            obs['features'][:, 0] = [.1, .3, .2]
            obs['pressure'][:] = [0., 1., 2.]
            expected = AdaptiveSmartSignal(self.path, 'queue_guard').action('x', obs, clock)
            for variant in VARIANTS_V2:
                policy = self.policy(variant)
                self.assertEqual(policy.action('x', obs, clock), expected)
                self.assertEqual(policy.last_decision['mode'], 'ai_queue_guard')
                self.assertFalse(policy.last_decision['wait_guard_triggered'])

    def test_high_wait_guard_limits_pressure_choice_to_eligible_waiting_phases(self):
        clock = self.clock(elapsed=40)
        obs = self.observation(clock)
        obs['features'][:, 0] = [.1, .01, .5]
        obs['features'][:, 4] = [0., .5, .49]
        obs['pressure'][:] = [100., -5., 1000.]
        policy = self.policy()
        self.assertEqual(policy.action('x', obs, clock), 1)
        self.assertTrue(policy.last_decision['wait_guard_triggered'])
        self.assertEqual(policy.last_decision['candidate_mask'], [False, True, False])
        obs['features'][1, 0] = .009
        self.assertEqual(policy.action('x', obs, clock), 0)
        self.assertFalse(policy.last_decision['wait_guard_triggered'])

    def test_guard_never_selects_an_illegal_waiting_phase(self):
        clock = self.clock()
        obs = self.observation(clock)
        obs['mask'][:] = [True, False, True]
        obs['features'][1, 4] = 1.
        policy = self.policy()
        self.assertEqual(policy.action('x', obs, clock), 0)
        self.assertEqual(policy.last_decision['candidate_mask'], [False, False, False])

    def test_thirty_five_variant_uses_seventy_two_second_feature_threshold(self):
        clock = self.clock(elapsed=35)
        obs = self.observation(clock)
        obs['features'][1, 4] = .4
        self.assertEqual(self.policy('load_gate_pressure_wait35').action('x', obs, clock), 1)
        self.assertEqual(self.policy('load_gate_pressure_wait40').action('x', obs, clock), 0)
        clock.elapsed = 34
        self.assertEqual(self.policy('load_gate_pressure_wait35').action('x', obs, clock), 0)

    def test_v1_already_switching_is_never_replaced_by_wait_guard(self):
        clock = self.clock()
        obs = self.observation(clock, .11)
        obs['features'][:, 0] = [.1, .2, .3]
        obs['features'][1, 4] = .9
        obs['pressure'][:] = [0., 1., 5.]
        policy = self.policy()
        self.assertEqual(policy.action('x', obs, clock), 2)
        self.assertTrue(policy.last_decision['queue_guard_triggered'])
        self.assertFalse(policy.last_decision['wait_guard_triggered'])

    def test_wait_guard_only_operates_in_green_stage(self):
        clock = self.clock()
        obs = self.observation(clock)
        obs['features'][1, 4] = .9
        for stage in ('yellow', 'all_red'):
            clock.stage = stage
            policy = self.policy()
            self.assertEqual(policy.action('x', obs, clock), 0)
            self.assertFalse(policy.last_decision['wait_guard_triggered'])

    def test_all_variants_obey_minimum_maximum_starvation_and_clearance(self):
        for count in (3, 4):
            for variant in VARIANTS_V2:
                for density in (.05, .2):
                    clock = self.clock(elapsed=0, count=count)
                    policy = self.policy(variant)
                    obs = self.observation(clock, density)
                    self.assertEqual(policy.action('x', obs, clock), 0)
                    clock.elapsed = 60
                    obs = self.observation(clock, density)
                    self.assertNotEqual(policy.action('x', obs, clock), 0)
                    clock.elapsed = 25
                    clock.red_age[1] = 180
                    obs = self.observation(clock, density)
                    self.assertEqual(policy.action('x', obs, clock), 1)
                    clock.request(1)
                    for _ in range(5):
                        self.assertEqual(policy.action('x', self.observation(clock, density), clock), 1)
                        clock.tick()

    def test_invalid_values_rejected_and_observation_remains_unchanged(self):
        clock = self.clock()
        obs = self.observation(clock)
        before = copy.deepcopy(obs)
        for variant in VARIANTS_V2:
            policy = self.policy(variant)
            first = policy.action('junction_a', obs, clock)
            self.assertEqual(policy.action('unseen_junction', obs, clock), first)
            for key in obs:
                np.testing.assert_array_equal(obs[key], before[key])
            for key, invalid in (('pressure', [0., np.inf, 1.]),
                                 ('mask', [True, np.nan, False]),
                                 ('features', np.zeros((3, 8)))):
                changed = copy.deepcopy(obs)
                changed[key] = invalid
                with self.assertRaises(ValueError):
                    policy.action('x', changed, clock)
            policy.scores = lambda _: np.array([0., np.nan, 1.])
            with self.assertRaisesRegex(ValueError, 'AI scores'):
                policy.action('x', obs, clock)


if __name__ == '__main__':
    unittest.main()
