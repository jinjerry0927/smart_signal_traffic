"""Read-only adaptive diagnostic checks against fake TraCI objects."""
import csv
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.diagnose_adaptive_signal import (
    AdaptiveDiagnosticEnvironment, AdaptiveDiagnosticPolicy, compare_metrics)
from tools.run_adaptive_wave import SafetyHold
from transferable_signal import FEATURES
from transferable_signal.adaptive import VARIANTS
from transferable_signal.control import SignalClock
from transferable_signal.environment import SignalEnvironment
from transferable_signal.spec import DEFAULT_LIMITS


class AdaptiveDiagnosticTests(unittest.TestCase):
    def fake_env(self, directory, now=145.):
        phases = [dict(state=''.join('G' if i == j else 'r' for i in range(3)),
                       incoming=[f'lane_{j}']) for j in range(3)]
        clock = SignalClock(phases, DEFAULT_LIMITS)
        clock.elapsed = 25
        features = np.zeros((3, len(FEATURES)), dtype=np.float32)
        features[:, 0] = [.1, .2, .3]
        features[:, 1] = .05
        features[0, 6] = 1.
        obs = dict(junction=dict(features=features, mask=clock.mask(),
                   pressure=np.arange(3, dtype=float), critical_ratio=np.zeros(3), queue_ratio=.2))
        env = object.__new__(AdaptiveDiagnosticEnvironment)
        env.output = Path(directory)
        env.spec = dict(junctions=[dict(id='junction', phases=phases)])
        env.clocks = dict(junction=clock)
        env.observer = SimpleNamespace(samples={f'lane_{i}': dict(queue=i, vehicles=i + 1) for i in range(3)})
        env.metrics = SimpleNamespace(teleports=0, collisions=0)
        env.diagnostic_stream = env.decision_stream = env.focus_stream = None
        env.last_diagnostic_time = None
        env.checked_seconds = env.decision_count = env.focus_count = 0
        env.warning_details = set()
        env.focus_vehicle = None
        env.focus_start, env.focus_end = 145., 175.
        timer = [now]
        env.connection = SimpleNamespace(
            simulation=SimpleNamespace(getTime=lambda: timer[0]),
            trafficlight=SimpleNamespace(getRedYellowGreenState=lambda _: clock.state()),
            vehicle=SimpleNamespace(getIDList=lambda: ['focus', 'leader'],
                                    getLeader=lambda vid, dist: ('leader', 4.5),
                                    getLaneID=lambda _: ':center_2_0',
                                    getSpeed=lambda vid: 3. if vid == 'focus' else 2.,
                                    getAcceleration=lambda _: -1.,
                                    getLanePosition=lambda _: 8.))
        return env, obs, timer

    def close_streams(self, env):
        for stream in (env.diagnostic_stream, env.decision_stream, env.focus_stream):
            if stream is not None:
                stream.close()

    def test_logs_exact_observation_once_without_mutating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            env, obs, _ = self.fake_env(directory)
            features = obs['junction']['features'].copy()
            try:
                with patch.object(SignalEnvironment, 'observe', return_value=obs):
                    self.assertIs(env.observe(), obs)
                    self.assertIs(env.observe(), obs)
                with (Path(directory) / 'diagnostic_seconds.csv').open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(len(rows), 3)
                self.assertEqual(env.checked_seconds, 1)
                self.assertEqual(rows[0]['actual_state'], 'Grr')
                for feature in FEATURES:
                    self.assertIn(feature, rows[0])
                np.testing.assert_array_equal(obs['junction']['features'], features)
            finally:
                self.close_streams(env)

    def test_emergency_warning_retained_while_other_warnings_stop_after_trace(self):
        for warning, allowed in (
                ("Warning: Vehicle 'focus' performs emergency braking on lane ':center_2_0' time=145.00.", True),
                ("Warning: Teleporting vehicle 'focus'; waited too long (jam).", False),
                ('Error: Bad connection', False)):
            with self.subTest(warning=warning), tempfile.TemporaryDirectory() as directory:
                env, obs, _ = self.fake_env(directory)
                (Path(directory) / 'sumo-errors.log').write_text(warning, encoding='utf-8')
                try:
                    with patch.object(SignalEnvironment, 'observe', return_value=obs):
                        if allowed:
                            env.observe()
                            self.assertIn(warning, env.warning_details)
                        else:
                            with self.assertRaises(SafetyHold):
                                env.observe()
                    with (Path(directory) / 'diagnostic_seconds.csv').open() as stream:
                        self.assertEqual(len(list(csv.DictReader(stream))), 3)
                finally:
                    self.close_streams(env)

    def test_collision_and_teleport_stop_with_current_trace_preserved(self):
        for event in ('collisions', 'teleports'):
            with tempfile.TemporaryDirectory() as directory:
                env, obs, _ = self.fake_env(directory)
                setattr(env.metrics, event, 1)
                try:
                    with patch.object(SignalEnvironment, 'observe', return_value=obs):
                        with self.assertRaisesRegex(SafetyHold, 'Collision or teleport'):
                            env.observe()
                    self.assertEqual(env.checked_seconds, 1)
                finally:
                    self.close_streams(env)

    def test_focus_reads_installed_traci_getters_and_handles_absent_vehicle(self):
        with tempfile.TemporaryDirectory() as directory:
            env, obs, timer = self.fake_env(directory)
            env.focus_vehicle = 'focus'
            try:
                with patch.object(SignalEnvironment, 'observe', return_value=obs):
                    env.observe()
                    timer[0] = 146.
                    env.connection.vehicle.getIDList = lambda: []
                    env.observe()
                with (Path(directory) / 'vehicle_focus.jsonl').open() as stream:
                    records = [json.loads(line) for line in stream]
                self.assertEqual(records[0]['lane'], ':center_2_0')
                self.assertEqual(records[0]['speed'], 3.)
                self.assertEqual(records[0]['acceleration'], -1.)
                self.assertEqual(records[0]['leader_id'], 'leader')
                self.assertEqual(records[0]['leader_speed'], 2.)
                self.assertEqual(records[0]['leader_gap_m'], 4.5)
                self.assertFalse(records[1]['present'])
                self.assertEqual(env.focus_count, 2)
            finally:
                self.close_streams(env)

    def test_mismatched_signal_invalid_mask_and_nonfinite_features_stop(self):
        for problem in ('signal', 'mask', 'feature'):
            with self.subTest(problem=problem), tempfile.TemporaryDirectory() as directory:
                env, obs, _ = self.fake_env(directory)
                if problem == 'signal':
                    env.connection.trafficlight.getRedYellowGreenState = lambda _: 'rrr'
                elif problem == 'mask':
                    obs['junction']['mask'][:] = [True, False, False]
                else:
                    obs['junction']['features'][0, 0] = np.nan
                try:
                    with patch.object(SignalEnvironment, 'observe', return_value=obs):
                        with self.assertRaises(SafetyHold):
                            env.observe()
                finally:
                    self.close_streams(env)

    def test_policy_logs_original_ai_adaptive_decision_and_all_features(self):
        with tempfile.TemporaryDirectory() as directory:
            env, obs, _ = self.fake_env(directory)
            policy = object.__new__(AdaptiveDiagnosticPolicy)
            policy.variant, policy.config = 'queue_guard', VARIANTS['queue_guard']
            policy.last_decision = None
            policy.scores = lambda _: np.array([1., 0., 0.])
            try:
                with patch.object(AdaptiveDiagnosticEnvironment, 'active', env):
                    chosen = policy.action('junction', obs['junction'], env.clocks['junction'])
                record = json.loads((Path(directory) / 'diagnostic_decisions.jsonl').read_text())
                self.assertEqual(chosen, 2)
                self.assertEqual(record['raw_ai'], 0)
                self.assertTrue(record['last_decision']['queue_guard_triggered'])
                self.assertEqual(record['features'], obs['junction']['features'].tolist())
                self.assertEqual(env.decision_count, 1)
            finally:
                self.close_streams(env)

    def test_exact_metric_comparison_excludes_only_logging_latency(self):
        original = dict(queue=4.5, arrived=100, status='completed',
                        mean_decision_ms_per_junction=.1, p95_decision_ms_per_junction=.2)
        actual = dict(original, mean_decision_ms_per_junction=3., p95_decision_ms_per_junction=5.)
        compare_metrics(actual, original)
        with self.assertRaisesRegex(ValueError, 'queue'):
            compare_metrics(dict(actual, queue=5.), original)
        with self.assertRaisesRegex(ValueError, 'keys differ'):
            compare_metrics(dict(actual, extra=1), original)


if __name__ == '__main__':
    unittest.main()
