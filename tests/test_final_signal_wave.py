"""Selection boundaries and preservation of the frozen research context."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import final_signal_wave as wave, common_rule_wave as helper
from transferable_signal.deployment import validated_policy


def records(candidate_queue=9.7, candidate_arrived=100):
    return [dict(row=dict(scenario=f'{c}_{p}', case=c, profile=p, policy_label=l,
        mean_queue_vehicles=q, arrived_vehicles=a, scheduled_vehicles=100))
        for c in ('t1', 'x3') for p in helper.PROFILES
        for l, q, a in (('fixed', 12, 95), ('v2', 10, 100), ('load_aware30', candidate_queue, candidate_arrived))]


class Tests(unittest.TestCase):
    def test_original_two_percent_boundary_is_not_relaxed(self):
        with wave.context():
            result = wave.selection(records(9.81))
            self.assertEqual(result['chosen'], 'v2')
            self.assertIsNone(result['candidate_chosen'])
            self.assertTrue(result['fallback_used'])
            self.assertEqual(wave.selection(records())['chosen'], 'load_aware30')

    def test_throughput_loss_rejects_even_large_queue_improvement(self):
        with wave.context():
            self.assertEqual(wave.selection(records(8, 99))['chosen'], 'v2')

    def test_geometry_regression_rejects_good_overall_mean(self):
        rows = records(5)
        for r in rows:
            if r['row']['case'] == 'x3' and r['row']['policy_label'] == 'load_aware30':
                r['row']['mean_queue_vehicles'] = 11.1
        with wave.context():
            self.assertEqual(wave.selection(rows)['chosen'], 'v2')

    def test_selection_does_not_mutate_records(self):
        rows = records(); before = deepcopy(rows)
        with wave.context(): wave.selection(rows)
        self.assertEqual(rows, before)

    def test_context_restores_original_on_error(self):
        before = (helper.INPUT, helper.OUTPUT, helper.preflight, helper.plan, helper.policy, helper.VARIANTS)
        with self.assertRaises(RuntimeError):
            with wave.context():
                self.assertEqual(helper.plan()['validation_samples'], 4)
                self.assertEqual(set(helper.VARIANTS), {'load_aware30'})
                raise RuntimeError('verification interruption')
        self.assertEqual((helper.INPUT, helper.OUTPUT, helper.preflight, helper.plan, helper.policy, helper.VARIANTS), before)

    def test_fallback_final_has_four_distinct_controllers(self):
        with patch.object(wave, 'read_json', return_value=dict(chosen='v2')):
            self.assertEqual(wave.labels_for('final'), ['fixed', 'webster', 'max_pressure', 'v2'])
        with patch.object(wave, 'read_json', return_value=dict(chosen='load_aware30')):
            labels = wave.labels_for('final')
            self.assertEqual(len(labels), len(set(labels)))
            self.assertEqual(len(labels), 5)

    def test_final_failure_does_not_reselect(self):
        rows = [r for r in records() if r['row']['policy_label'] != 'load_aware30']
        for r in list(rows):
            if r['row']['policy_label'] == 'fixed':
                for label in ('webster', 'max_pressure'):
                    copy = deepcopy(r); copy['row']['policy_label'] = label
                    copy['row']['mean_queue_vehicles'] = 5; copy['row']['arrived_vehicles'] = 100
                    rows.append(copy)
        result = wave.final_summary(rows, 'v2')
        self.assertFalse(result['target_met'])
        self.assertEqual(result['chosen'], 'v2')
        self.assertFalse(result['final_reselection'])

    def test_current_model_and_config_remain_the_baseline(self):
        before = validated_policy()[2]
        with wave.context():
            obj, path, identity = helper.policy('v2')
            self.assertEqual(identity, before)
            candidate = helper.policy('load_aware30')
            self.assertEqual(candidate[1], path)
            self.assertEqual(candidate[2]['model_sha256'], before['model_sha256'])
        self.assertEqual(validated_policy()[2], before)


if __name__ == '__main__': unittest.main()
