"""Guards for a reviewed suffix and honest reporting of partial failures."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import unseen_transfer_followup as followup
from transferable_signal.spec import digest


class ReviewedSuffixTests(unittest.TestCase):
    def test_suffix_rejects_retry_duplicate_skip_and_extra_job(self):
        jobs = [(dict(id='s'), name, None, None) for name in ['fixed', 'webster', 'smart_v2']]
        self.assertEqual(followup.suffix(jobs, ['s/fixed', 's/webster']), jobs[2:])
        for attempted in [['s/webster'], ['s/fixed', 's/fixed'], ['s/fixed', 's/webster', 's/smart_v2', 's/extra']]:
            with self.assertRaisesRegex(ValueError, 'prefix'):
                followup.suffix(jobs, attempted)

    def test_changed_review_is_rejected_before_any_traffic(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'followup-round-01.json').write_text(json.dumps(dict(status='Codex_separate_review_PASS', runner_sha256='changed')), encoding='utf-8')
            with patch.object(followup.build, 'BUNDLE', folder), patch.object(followup, 'ledger') as ledger, \
                 patch.object(followup.trial, 'run_one') as traffic:
                with self.assertRaisesRegex(ValueError, 'review'):
                    followup.checked_plan(1)
                ledger.assert_not_called()
                traffic.assert_not_called()

    def test_failed_suffix_cannot_be_resumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            output = folder / 'output'
            (output / 'followup_round01').mkdir(parents=True)
            plan_file = folder / 'followup-round-01.json'
            plan_file.write_text('{}', encoding='utf-8')
            (output / 'followup_round01' / followup.trial.BATCH_NAME).write_text(json.dumps(dict(status='failed', plan_sha256=digest(plan_file), fingerprint={})), encoding='utf-8')
            with patch.object(followup, 'OUTPUT', output), patch.object(followup.build, 'BUNDLE', folder), \
                 patch.object(followup, 'checked_plan', return_value=(dict(fingerprint={}), [])), \
                 patch.object(followup.trial, 'run_one') as traffic:
                with self.assertRaisesRegex(ValueError, 'cannot be automatically resumed'):
                    followup.run(1, resume=True)
                traffic.assert_not_called()

    def test_failure_review_raw_digest_is_pinned(self):
        failure = dict(id='s/webster', outcome='failed_teleport', files_sha256={'frames.jsonl': 'changed'})
        jobs = [(dict(id='s'), name, None, None) for name in ['webster', 'smart_v2']]
        plan = dict(status='Codex_separate_review_PASS', runner_sha256=digest(Path(followup.__file__)), review_note='README.md',
                    review_note_sha256=digest(ROOT / 'README.md'), fingerprint={}, parents=[], expected_jobs=['s/smart_v2'],
                    attempted_before=1, reviewed_failure={**failure, 'files_sha256': {'frames.jsonl': 'original'}})
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'followup-round-01.json').write_text(json.dumps(plan), encoding='utf-8')
            with patch.object(followup.build, 'BUNDLE', folder), patch.object(followup, 'ledger', return_value=({}, jobs, [failure], [])):
                with self.assertRaisesRegex(ValueError, 'provenance'):
                    followup.checked_plan(1)

    def test_report_preserves_failure_and_uses_common_completed_pairs(self):
        names = ['fixed', 'webster', 'max_pressure', 'smart_v2', 'raw_smart', 'rules_pressure']
        attempts = []
        for index in range(27):
            for name in names:
                identity = f's{index}/{name}'
                if index == 26 and name == 'webster':
                    attempts.append(dict(id=identity, outcome='failed_teleport', path=identity, completed_metrics=None))
                else:
                    metrics = dict(mean_queue_vehicles=10., arrived_vehicles=100, scheduled_vehicles=100, not_departed_vehicles=0, departed_not_arrived_vehicles=0)
                    record = dict(id=identity, scenario=f's{index}', case=f'c{index//9}', profile=['quiet','busy','directional'][index%3], controller=name, metrics=metrics, warnings=[])
                    attempts.append(dict(id=identity, outcome='completed', path=identity, record=record))
        jobs = [(dict(id=f's{i}'), name, None, None) for i in range(27) for name in names]
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with patch.object(followup, 'OUTPUT', folder), patch.object(followup, 'ledger', return_value=({}, jobs, attempts, [])):
                followup.report(1)
            result = json.loads((folder / 'reviewed-followup-summary.json').read_text(encoding='utf-8'))
            self.assertEqual((result['completed_runs'], result['failed_runs'], result['unattempted_runs']), (161, 1, 0))
            self.assertIsNone(result['formal_primary_target_met'])
            self.assertFalse(result['formal_primary_target_evaluable'])
            self.assertEqual(result['common_complete_conditions'], 26)
            self.assertEqual(result['paired_smart_comparisons']['webster']['paired_conditions'], 26)
            self.assertEqual(result['paired_smart_comparisons']['fixed']['paired_conditions'], 27)
            self.assertEqual(result['controller_coverage']['webster'], dict(completed=26, failed=1))
            self.assertFalse(result['failed_partial_metrics_in_averages'])
            rows = (folder / 'reviewed-followup-comparison.csv').read_text(encoding='utf-8-sig').splitlines()
            self.assertEqual(len(rows), 163)
            self.assertIn('s26/webster,failed_teleport,,,,,s26/webster', rows)


if __name__ == '__main__':
    unittest.main()
