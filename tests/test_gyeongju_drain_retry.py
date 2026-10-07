"""Audit-boundary repair and immutable-copy recovery; never run live SUMO."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import diagnose_gyeongju_drain_retry as retry
from transferable_signal.spec import digest, read_json, write_json

loader = importlib.util.spec_from_file_location('drain_retry_fixtures', ROOT / 'tests/test_gyeongju_drain.py')
fixtures = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fixtures)


class TraceCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DrainBoundaryTests('test_arrival900_future_row_excluded_by_observed_id_set')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.auditor = retry.corrected_auditor()
        for row in self.fixture.rows[1:601]:
            row.update(departed=1, running=1 if row['time_s'] < 500 else 0,
                       queued=1 if row['time_s'] < 500 else 0)
        fixtures.csv_write(self.fixture.output / 'seconds.csv', list(self.fixture.rows[0]), self.fixture.rows)
        lanes = [dict(time_s=t, lane=lid, edge=data['edge'], queued=0, vehicles=0,
                      storage_capacity_proxy=10, density_proxy=0)
                 for t in range(1801) for lid, data in self.fixture.spec['lanes'].items()]
        nodes = [dict(time_s=t, junction=j['id'], phase=0, stage='green', green_elapsed_s=25,
                      incoming_queued=0, incoming_vehicles=0, downstream_storage_pressure_lanes='[]')
                 for t in range(1801) for j in self.fixture.spec['junctions']]
        fixtures.csv_write(self.fixture.output / 'lanes.csv', list(lanes[0]), lanes)
        fixtures.csv_write(self.fixture.output / 'junctions.csv', list(nodes[0]), nodes)

    def test_t600_unattempted_insertion_allowed_and_original_code_unchanged(self):
        before = digest(retry.original.__file__)
        with self.assertRaisesRegex(ValueError, 'vehicle accounting'):
            retry.original._audit_trace(self.fixture.output, self.fixture.spec, 2)
        rows, _ = self.auditor(self.fixture.output, self.fixture.spec, 2)
        self.assertEqual(len(rows), 1801)
        self.assertEqual(before, digest(retry.original.__file__))
        self.assertEqual(before, retry.LOGGER_SHA256)

    def test_t601_missing_vehicle_still_rejected(self):
        self.fixture.rows[601].update(departed=1, running=0, queued=0)
        fixtures.csv_write(self.fixture.output / 'seconds.csv', list(self.fixture.rows[0]), self.fixture.rows)
        with self.assertRaisesRegex(ValueError, 'vehicle accounting'):
            self.auditor(self.fixture.output, self.fixture.spec, 2)

    def test_all_time_upper_bound_and_departure_accounting_stay_strict(self):
        for changed in (dict(pending=3), dict(departed=2)):
            original = self.fixture.rows[100].copy()
            self.fixture.rows[100].update(changed)
            fixtures.csv_write(self.fixture.output / 'seconds.csv', list(self.fixture.rows[0]), self.fixture.rows)
            with self.assertRaisesRegex(ValueError, 'vehicle accounting'):
                self.auditor(self.fixture.output, self.fixture.spec, 2)
            self.fixture.rows[100] = original


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.source, self.output = self.path / 'failed', self.path / 'retry'
        self.source.mkdir()
        (self.source / 'prepared_input').mkdir()
        write_json(self.source / 'prepared_input/suite.json', {})
        self.raw = self.source / retry.RECOVERED
        self.raw.mkdir(parents=True)
        write_json(self.raw / 'manifest.json', dict(status='completed', diagnostic_tool_sha256=retry.LOGGER_SHA256))
        (self.raw / 'tripinfo.xml').write_text('<tripinfos/>', encoding='utf-8')
        self.base = dict(diagnostic_tool_sha256=retry.LOGGER_SHA256)
        self.metadata = dict(fingerprint=self.base, status='safety_hold', error='ValueError: Per-second vehicle accounting changed',
            records=[], active_run=dict(scenario='gyeongju_busy_s00', controller='fixed'), started_run_attempts=1, expected_runs=16)
        write_json(self.source / 'manifest.json', self.metadata)
        self.scenario = dict(id='gyeongju_busy_s00', drain_s=300)
        self.data = {}, {}, {}, [self.scenario], {(self.scenario['id'], 'fixed'): self.path / 'reference'}, self.base

    def test_first_copy_preserves_every_byte_and_counts15_new16_verified(self):
        source_bytes = {p: p.read_bytes() for p in self.source.rglob('*') if p.is_file()}
        def prepare(output, *_):
            (output / 'prepared_input').mkdir()
            destination = output / 'prepared_input/suite.json'
            destination.write_bytes((self.source / 'prepared_input/suite.json').read_bytes())
            return destination
        def audit(directory, *args):
            for name, expected in retry.recovery_provenance(self.base)['raw_sha256'].items():
                self.assertEqual(digest(directory / name), expected)
            return dict(diagnostic_directory=str(retry.RECOVERED), prefix900=dict(arrived_vehicles=494))
        def fake_remaining(output, resume, max_wall_seconds):
            self.assertTrue(resume)
            value = read_json(output / 'manifest.json')
            self.assertEqual(len(value['records']), 1)
            value['records'].extend(dict(scenario=f'fake{i}') for i in range(15))
            value.update(status='completed', actual_new_runs=16, started_run_attempts=15)
            write_json(output / 'summary.json', dict(status='completed', actual_new_runs=16))
            return value
        with patch.object(retry.original, 'DEFAULT_OUTPUT', self.source), \
                patch.object(retry.original, '_preflight', return_value=copy.deepcopy(self.data)), \
                patch.object(retry.original, '_prepare_input', side_effect=prepare), \
                patch.object(retry.original, 'audit_drain_run', side_effect=audit), \
                patch.object(retry.original, 'run_drain_wave', side_effect=fake_remaining):
            result = retry.run_retry(self.output)
        self.assertEqual((result['actual_new_runs'], result['verified_runs'], result['reused_runs']), (15, 16, 1))
        self.assertEqual(read_json(self.output / 'summary.json')['actual_new_runs'], 15)
        self.assertTrue(all(path.read_bytes() == value for path, value in source_bytes.items()))

    def test_unknown_failure_attempt_or_logging_hash_cannot_be_recovered(self):
        for changed in (dict(status='interrupted'), dict(started_run_attempts=2), dict(expected_runs=17),
                        dict(error='another failure')):
            write_json(self.source / 'manifest.json', dict(self.metadata, **changed))
            with patch.object(retry.original, 'DEFAULT_OUTPUT', self.source), self.assertRaisesRegex(ValueError, 'reviewed first'):
                retry.recovery_provenance(self.base)
        write_json(self.source / 'manifest.json', self.metadata)
        write_json(self.raw / 'manifest.json', dict(status='completed', diagnostic_tool_sha256='wrong'))
        with patch.object(retry.original, 'DEFAULT_OUTPUT', self.source), self.assertRaisesRegex(ValueError, 'logging provenance'):
            retry.recovery_provenance(self.base)


if __name__ == '__main__':
    unittest.main()
