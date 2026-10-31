"""Read-only lineage audit and fail-closed protocol integrity checks; no SUMO."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import audit_unseen_transfer as audit


class UnseenTransferAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Later experiment inputs must not be retroactively classified as
        # historical inputs when checking the already sealed preregistration.
        if (audit.DEFAULT_OUT / 'history.json').is_file():
            audit.verify()
            cls.history = audit.read(audit.DEFAULT_OUT / 'history.json')
        else:
            cls.history = audit.build_history()
        cls.plan = audit.protocol(cls.history)

    def test_training_hash_catalog_is_not_the_training_scenario_list(self):
        training = self.history['training']
        self.assertEqual((training['completed_episodes'], training['updates']), (150, 17969))
        self.assertEqual(len(training['case_ids']), 7)
        self.assertEqual(len(training['scenario_ids']), 21)
        self.assertEqual(training['frozen_demand_entries'], 30)
        self.assertNotIn('gyeongju', training['case_ids'])
        self.assertFalse(any(s.startswith(('valid_', 'gyeongju')) for s in training['scenario_ids']))

    def test_showcase_identity_and_real_material_differences(self):
        self.assertEqual(self.history['showcase']['signal_count'], 3)
        approaches = [a for j in self.history['showcase']['junctions'] for a in j['approaches']]
        self.assertEqual({a['lanes'] for a in approaches}, {1, 2})
        self.assertGreater(min(a['length_m'] for a in approaches), 350)
        for target in self.plan['targets']:
            self.assertGreaterEqual(len(set(target['novelty_axes'])), 2)
        self.assertEqual(self.plan['targets'][1]['lanes'], dict(N=3, E=1, S=3, W=1))
        self.assertEqual(self.plan['targets'][2]['signal_count'], 2)
        identities = {d['identity_sha256'] for d in self.history['showcase_manifests']}
        self.assertEqual(identities, {'e904565d2e7138892c445fd661e5ff38b1beffd33ded8dca3b60f72f6e19fa0e'})

    def test_missing_archival_evidence_is_explicit_and_not_revalidated(self):
        evidence = self.history['prior_release_evidence']
        # This works both in a shared checkout with missing logs and a complete original checkout.
        for item in evidence:
            self.assertEqual(item['status'] == 'missing', not (ROOT / item['path']).is_file())
        self.assertIn('no historical raw-run re-audit', self.history['audit_scope'])
        failed = next(s for s in self.history['suites'] if s['path'] == 'network/transferable_holdout_v2_20261004/suite.json')
        self.assertEqual(failed['preparation_status'], 'failed')
        self.assertEqual(failed['cases'], [])

    def test_old_windows_reference_is_rebased_and_hash_checked_without_rewrite(self):
        old = 'C:\\Users\\James\\.codex\\worktrees\\f73e\\sumo_project\\network\\transferable_v1\\suite.json'
        path = ROOT / audit.BASE_SUITE
        before = path.read_bytes()
        good = audit.reference_record(ROOT, ROOT, old, audit.digest(path))
        bad = audit.reference_record(ROOT, ROOT, old, '0' * 64)
        self.assertEqual(good['status'], 'matched')
        self.assertEqual(bad['status'], 'mismatch')
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(ValueError):
            audit.resolve_reference(ROOT, ROOT, '../outside-repository.xml')

    def test_renaming_all_network_ids_does_not_make_a_new_geometry(self):
        source = ROOT / self.history['showcase']['network']
        tree = ET.parse(source)
        # Rename edge, lane, junction and signal references consistently enough
        # for this static descriptor; it must not rely on their spellings.
        for element in tree.iter():
            for key in ('id', 'from', 'to', 'tl'):
                if key in element.attrib:
                    element.set(key, 'renamed_' + element.get(key))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'renamed.net.xml'
            tree.write(path, encoding='utf-8')
            self.assertNotEqual(audit.digest(source), audit.digest(path))
            self.assertEqual(audit.network_description(source)['screening_signature_sha256'],
                             audit.network_description(path)['screening_signature_sha256'])

    def test_shortened_storage_changes_the_geometry_screen(self):
        source = ROOT / self.history['showcase']['network']
        tree = ET.parse(source)
        external = next(e for e in tree.getroot().findall('edge') if e.get('function') != 'internal')
        for lane in external.findall('lane'):
            lane.set('length', '120.0')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'short.net.xml'
            tree.write(path, encoding='utf-8')
            self.assertNotEqual(audit.network_description(source)['screening_signature_sha256'],
                                audit.network_description(path)['screening_signature_sha256'])

    def test_protocol_is_unchanged_showcase_plus_new_test_conditions(self):
        plan = self.plan
        profile = audit.read(ROOT / audit.RELEASE / 'policy.json')
        self.assertEqual(plan['policy_config'], profile['policy_config'])
        self.assertEqual(plan['limits'], profile['limits'])
        self.assertEqual(plan['additional_training'], 0)
        self.assertFalse(plan['target_specific_tuning'])
        seeds = [s for values in plan['demand']['seeds'].values() for row in values.values() for s in row]
        self.assertEqual(len(set(seeds)), 27)
        self.assertFalse(set(seeds) & set(self.history['exclusions']['demand_seeds']))
        self.assertEqual(27 * len(plan['comparators']['primary']), 108)
        self.assertEqual(27 * len(plan['comparators']['diagnostics']), 54)
        self.assertFalse(plan['criteria']['learned_contribution']['affects_primary_pass'])

    def test_freeze_rejects_changed_protocol_and_changed_rule_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rules = root / 'rules.json'
            rules.write_text('{"wait":35}', encoding='utf-8')
            sources = {'rules.json': audit.digest(rules)}
            plan = root / 'protocol.json'
            plan.write_text(json.dumps(dict(frozen_files_sha256=sources)), encoding='utf-8')
            seal = dict(audit_tool_sha256=audit.digest(Path(audit.__file__)),
                        files_sha256={'protocol.json': audit.digest(plan)}, frozen_files_sha256=sources)
            (root / 'freeze.json').write_text(json.dumps(seal), encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                audit.verify(root, root)
            original = plan.read_bytes()
            plan.write_text('{}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'audit/protocol changed'):
                audit.verify(root, root)
            plan.write_bytes(original)
            rules.write_text('{"wait":30}', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'model/rule/source changed'):
                audit.verify(root, root)

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            marker = path / 'user.txt'
            marker.write_text('preserve', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'overwrite'):
                audit.prepare(path)
            self.assertEqual(marker.read_text(encoding='utf-8'), 'preserve')


if __name__ == '__main__':
    unittest.main()
