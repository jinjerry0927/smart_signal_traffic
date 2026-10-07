"""Frozen adaptive policy checks using synthetic observations, without SUMO."""
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
from transferable_signal.adaptive import AdaptiveSmartSignal, VARIANTS, _normalize_legal
from transferable_signal.control import SignalClock
from transferable_signal.model import SmartSignal
from transferable_signal.spec import DEFAULT_LIMITS


class AdaptiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = Path(cls.directory.name) / 'synthetic-model.json'
        weights = [0.] * (2 * len(FEATURES))
        weights[6] = 1.  # Select the existing green in these synthetic observations.
        cls.path.write_text(json.dumps(dict(
            version=VERSION, features=list(FEATURES),
            training=dict(updates=1, synthetic_unit_test=True),
            layers=[dict(weights=[weights], bias=[0.]),
                    dict(weights=[[1.]], bias=[0.]),
                    dict(weights=[[1.]], bias=[0.])])), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def clock(self, count=3, elapsed=25):
        phases = [dict(state=''.join('G' if i == j else 'r' for i in range(count)))
                  for j in range(count)]
        clock = SignalClock(phases, DEFAULT_LIMITS)
        clock.elapsed = elapsed
        return clock

    def observation(self, clock, density=.05):
        count = len(clock.phases)
        features = np.zeros((count, len(FEATURES)))
        features[:, 0] = np.linspace(.1, .3, count)
        features[:, 1] = density
        features[clock.current, 6] = 1.
        return dict(features=features, mask=clock.mask(), pressure=np.arange(count, dtype=float))

    def policy(self, variant='adaptive_pressure_ai', scores=None):
        policy = AdaptiveSmartSignal(self.path, variant)
        if scores is not None:
            policy.scores = lambda _: np.asarray(scores, dtype=float).copy()
        return policy

    def test_variant_configs_are_frozen_and_serializable_as_copies(self):
        self.assertEqual(set(VARIANTS), {'queue_guard', 'pressure_ai', 'adaptive_pressure_ai'})
        with self.assertRaises(TypeError):
            VARIANTS['new'] = {}
        with self.assertRaises(TypeError):
            VARIANTS['queue_guard']['queue_margin'] = 1.
        for config in VARIANTS.values():
            self.assertEqual(json.loads(json.dumps(dict(config)))['version'], 'adaptive-v1')
        with self.assertRaisesRegex(ValueError, 'Unknown adaptive variant'):
            self.policy('not_a_variant')

    def test_legal_normalization_ignores_illegal_extremes_and_preserves_inputs(self):
        mask = np.array([True, False, True, True])
        values = np.array([2., 1e200, 4., 8.])
        original, original_mask = values.copy(), mask.copy()
        norm = _normalize_legal(values, mask)
        np.testing.assert_allclose(norm, [-.8, 0., -.2, 1.])
        np.testing.assert_allclose(_normalize_legal(values * 17 + 300, mask), norm)
        np.testing.assert_array_equal(values, original)
        np.testing.assert_array_equal(mask, original_mask)
        np.testing.assert_array_equal(_normalize_legal(np.ones(4) * 9, mask), np.zeros(4))
        np.testing.assert_allclose(_normalize_legal(np.array([-1e308, 1e308]),
                                                   np.ones(2, dtype=bool)), [-1., 1.])

    def test_queue_guard_is_distinct_from_original_ai_and_logs_trigger(self):
        clock = self.clock()
        obs = self.observation(clock)
        self.assertEqual(SmartSignal(self.path).action('any', obs, clock), 0)
        policy = self.policy('queue_guard')
        self.assertEqual(policy.action('any', obs, clock), 2)
        self.assertTrue(policy.last_decision['queue_guard_triggered'])
        self.assertEqual(policy.last_decision['raw_ai_action'], 0)

    def test_queue_guard_requires_eligible_green_time_and_low_density(self):
        for elapsed, stage, density, expected in (
                (24, 'green', .05, 0), (25, 'green', .12, 2),
                (25, 'green', .121, 0), (25, 'yellow', .05, 0),
                (25, 'all_red', .05, 0)):
            with self.subTest(elapsed=elapsed, stage=stage, density=density):
                clock = self.clock(elapsed=elapsed)
                clock.stage = stage
                obs = self.observation(clock, density)
                self.assertEqual(self.policy('queue_guard').action('x', obs, clock), expected)

    def test_queue_guard_only_checks_legal_other_queues_and_original_ai_stay(self):
        clock = self.clock()
        obs = self.observation(clock)
        obs['mask'][:] = [True, False, True]
        obs['features'][:, 0] = [.1, .9, .105]
        policy = self.policy('queue_guard')
        self.assertEqual(policy.action('x', obs, clock), 0)
        self.assertFalse(policy.last_decision['queue_guard_triggered'])
        obs['features'][2, 0] = .2
        switch_policy = self.policy('queue_guard', scores=[0., 0., 1.])
        self.assertEqual(switch_policy.action('x', obs, clock), 2)
        self.assertFalse(switch_policy.last_decision['queue_guard_triggered'])

    def test_queue_guard_may_keep_green_when_downstream_pressure_favors_it(self):
        clock = self.clock()
        obs = self.observation(clock)
        obs['pressure'][:] = [5., 1., 0.]
        policy = self.policy('queue_guard')
        self.assertEqual(policy.action('x', obs, clock), 0)
        self.assertTrue(policy.last_decision['queue_guard_triggered'])

    def test_pressure_ai_uses_quarter_ai_weight_with_legal_normalization(self):
        clock = self.clock()
        obs = self.observation(clock)
        obs['pressure'][:] = [1., 0., .9]
        policy = self.policy('pressure_ai', scores=[0., 0., 1.])
        self.assertEqual(policy.action('x', obs, clock), 2)
        self.assertEqual(policy.last_decision['ai_weight'], .25)

    def test_adaptive_blend_density_endpoints_and_midpoint(self):
        clock = self.clock()
        policy = self.policy(scores=[0., 0., 1.])
        for density, expected_weight, expected_action in (
                (0., 0., 0), (.10, 0., 0), (.175, .4, 0),
                (.25, .8, 2), (.6, .8, 2)):
            with self.subTest(density=density):
                obs = self.observation(clock, density)
                obs['pressure'][:] = [1., 0., 0.]
                self.assertEqual(policy.action('x', obs, clock), expected_action)
                self.assertAlmostEqual(policy.last_decision['ai_weight'], expected_weight)

    def test_blends_invariant_to_independent_positive_score_scaling_and_shift(self):
        clock = self.clock()
        obs = self.observation(clock, .2)
        obs['mask'][:] = [True, False, True]
        obs['pressure'][:] = [1., 1000., 0.]
        q = np.array([0., -1000., 1.])
        for variant in ('pressure_ai', 'adaptive_pressure_ai'):
            expected = self.policy(variant, q).action('x', obs, clock)
            changed = copy.deepcopy(obs)
            changed['pressure'] = obs['pressure'] * 31 + 42
            self.assertEqual(self.policy(variant, q * 19 - 12).action('x', changed, clock), expected)

    def test_all_variants_obey_min_max_clearance_and_starvation_masks(self):
        for count in (3, 4):
            for variant in VARIANTS:
                with self.subTest(count=count, variant=variant):
                    clock = self.clock(count=count, elapsed=0)
                    policy = self.policy(variant, np.arange(count))
                    obs = self.observation(clock)
                    self.assertEqual(policy.action('x', obs, clock), 0)
                    clock.elapsed = 60
                    obs = self.observation(clock)
                    self.assertNotEqual(policy.action('x', obs, clock), 0)
                    clock.elapsed = 25
                    clock.red_age[1] = 180
                    obs = self.observation(clock)
                    self.assertEqual(policy.action('x', obs, clock), 1)
                    clock.request(1)
                    for _ in range(5):
                        obs = self.observation(clock)
                        self.assertEqual(policy.action('x', obs, clock), 1)
                        clock.tick()

    def test_decisions_do_not_mutate_observations_or_clock(self):
        clock = self.clock()
        obs = self.observation(clock)
        old = copy.deepcopy(obs)
        clock_state = (clock.current, clock.pending, clock.stage, clock.elapsed,
                       clock.remaining, clock.red_age.copy())
        for variant in VARIANTS:
            self.policy(variant).action('different_junction_id', obs, clock)
        for key in obs:
            np.testing.assert_array_equal(obs[key], old[key])
        self.assertEqual((clock.current, clock.pending, clock.stage, clock.elapsed,
                          clock.remaining), clock_state[:5])
        np.testing.assert_array_equal(clock.red_age, clock_state[-1])

    def test_invalid_shapes_nonfinite_masks_scores_and_current_are_rejected(self):
        clock = self.clock()
        valid = self.observation(clock)
        mutations = [
            ('features', np.zeros((3, len(FEATURES) + 1))),
            ('features', np.full((3, len(FEATURES)), np.nan)),
            ('features', np.zeros((0, len(FEATURES)))),
            ('mask', np.ones((3, 1))), ('mask', [True, False]),
            ('mask', [True, np.nan, False]), ('mask', [True, 2, False]),
            ('mask', [False, False, False]),
            ('pressure', [1., np.inf, 2.]), ('pressure', [1., 2.]),
        ]
        for variant in VARIANTS:
            for key, value in mutations:
                with self.subTest(variant=variant, key=key, value=str(value)[:30]):
                    obs = copy.deepcopy(valid)
                    obs[key] = value
                    with self.assertRaises(ValueError):
                        self.policy(variant).action('x', obs, clock)
            for scores in ([0., np.nan, 1.], [1., 2.]):
                with self.assertRaisesRegex(ValueError, 'AI scores'):
                    self.policy(variant, scores).action('x', valid, clock)
            for current in (-1, 3, .5, True):
                clock.current = current
                with self.assertRaisesRegex(ValueError, 'current phase'):
                    self.policy(variant).action('x', valid, clock)
            clock.current = 0


if __name__ == '__main__':
    unittest.main()
