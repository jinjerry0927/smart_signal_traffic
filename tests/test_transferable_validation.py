"""Frozen validation data and fake multi-model runs; no live traffic/training."""
import csv
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import FEATURES, VERSION
from transferable_signal.spec import digest, read_json, write_json
from transferable_signal.suite import load_suite
from transferable_signal.validation import compare_models, prepare_validation

SOURCE = ROOT / 'network/transferable_v1/suite.json'


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.expanded = cls.directory / 'expanded'
        cls.source, cls.source_cases = load_suite(SOURCE)
        cls.manifest = prepare_validation(SOURCE, cls.expanded)
        cls.suite_path = cls.expanded / 'suite.json'
        cls.manifest, cls.cases = load_suite(cls.suite_path)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def model(self, directory, seed=42, **training_overrides):
        """Inference-compatible synthetic weights, used only with a fake runner."""
        training = dict(seed=seed, updates=1, completed_episodes=150,
                        training_network_sha256=[self.source_cases['train_x2'][0]['network_sha256']])
        training.update(training_overrides)
        path = Path(directory) / f'model_{seed}_{len(list(Path(directory).glob("model*.json")))}.json'
        write_json(path, dict(version=VERSION, features=list(FEATURES), training=training,
                              layers=[dict(weights=[[0.] * (2 * len(FEATURES))], bias=[0.]),
                                      dict(weights=[[0.]], bias=[0.]),
                                      dict(weights=[[0.]], bias=[float(seed)])]))
        return path

    def test_only_two_validation_networks_exact_copy_and_thirty_schedules(self):
        self.assertEqual(set(self.cases), {'valid_t2', 'valid_x3'})
        self.assertEqual(len(self.manifest['scenarios']), 30)
        self.assertEqual(self.manifest['validation_expansion']['source_suite_sha256'], digest(SOURCE))
        self.assertEqual(self.manifest['validation_expansion']['status'], 'completed')
        self.assertTrue(all(s['split'] == 'validation' for s in self.manifest['scenarios']))
        self.assertFalse((self.expanded / 'gyeongju').exists())
        for case_id, (spec, network, case) in self.cases.items():
            original_spec, original_network, original_case = self.source_cases[case_id]
            self.assertEqual(spec, original_spec)
            self.assertEqual(network.read_bytes(), original_network.read_bytes())
            self.assertEqual((self.expanded / case['spec']).read_bytes(), (SOURCE.parent / original_case['spec']).read_bytes())

    def test_original_schedules_preserved_diverse_samples_and_valid_configs(self):
        source_map = {s['id']: s for s in self.source['scenarios']}
        seed_set, hash_groups = set(), {}
        original_seeds = {s['seed'] for s in self.source['scenarios']}
        for scenario in self.manifest['scenarios']:
            self.assertNotIn(scenario['seed'], seed_set)
            seed_set.add(scenario['seed'])
            route_file = self.expanded / scenario['routes']
            root = ET.parse(route_file).getroot()
            departures = [float(v.get('depart')) for v in root.findall('vehicle')]
            self.assertEqual(len(departures), scenario['scheduled'])
            self.assertEqual(departures, sorted(departures))
            self.assertTrue(all(0 <= value < scenario['duration_s'] for value in departures))
            hash_groups.setdefault(scenario['source_scenario'], set()).add(digest(route_file))
            if scenario['sample'] == 0:
                original = source_map[scenario['source_scenario']]
                self.assertEqual(scenario['seed'], original['seed'])
                self.assertEqual(route_file.read_bytes(), (SOURCE.parent / original['routes']).read_bytes())
            else:
                self.assertNotIn(scenario['seed'], original_seeds)
            configuration = ET.parse(route_file.with_name(route_file.name.replace('.rou.xml', '.sumocfg')))
            self.assertEqual(configuration.find('./input/net-file').get('value'), 'network.net.xml')
            self.assertEqual(configuration.find('./input/route-files').get('value'), route_file.name)
            self.assertEqual(int(configuration.find('./time/end').get('value')), scenario['duration_s'] + scenario['drain_s'])
        self.assertEqual(len(hash_groups), 6)
        self.assertTrue(all(len(hashes) == 5 for hashes in hash_groups.values()))

    def test_reproducible_without_changing_source(self):
        source_files = [SOURCE]
        source_files.extend(SOURCE.parent / s['routes'] for s in self.source['scenarios'])
        source_files.extend(path for _, network, case in self.source_cases.values()
                            for path in (network, SOURCE.parent / case['spec']))
        before = {str(path): digest(path) for path in source_files}
        with tempfile.TemporaryDirectory() as directory:
            second = Path(directory) / 'repeat'
            prepare_validation(SOURCE, second)
            self.assertEqual((self.expanded / 'suite.json').read_bytes(), (second / 'suite.json').read_bytes())
            for scenario in self.manifest['scenarios']:
                self.assertEqual((self.expanded / scenario['routes']).read_bytes(), (second / scenario['routes']).read_bytes())
        self.assertEqual(before, {str(path): digest(path) for path in source_files})

    def test_invalid_samples_and_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            for samples in (0, -1, 5.0, True):
                output = Path(directory) / f'invalid_{samples}'
                with self.assertRaises(ValueError):
                    prepare_validation(SOURCE, output, samples)
                self.assertFalse(output.exists())
        with self.assertRaises(FileExistsError):
            prepare_validation(SOURCE, self.expanded)

    def test_all_models_checked_before_any_run_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = self.model(directory)
            output = Path(directory) / 'results'
            with patch('transferable_signal.validation.run') as fake:
                with self.assertRaises(FileNotFoundError):
                    compare_models(self.suite_path, [valid, Path(directory) / 'missing.json'], output)
                fake.assert_not_called()
            self.assertFalse(output.exists())

    def test_mixed_split_refused_before_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.model(directory)
            output = Path(directory) / 'results'
            with patch('transferable_signal.validation.run') as fake:
                with self.assertRaisesRegex(ValueError, 'validation-only'):
                    compare_models(SOURCE, [model], output)
                fake.assert_not_called()
            self.assertFalse(output.exists())

    def test_incomplete_or_inconsistent_validation_data_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.model(directory)
            mutations = [lambda m: m['validation_expansion'].update(status='interrupted'),
                         lambda m: m['validation_expansion'].update(samples_per_profile=6),
                         lambda m: m['scenarios'][0].update(scheduled=-1),
                         lambda m: m['scenarios'][1].update(seed=m['scenarios'][0]['seed'])]
            for index, mutate in enumerate(mutations):
                changed = read_json(self.suite_path)
                mutate(changed)
                path = self.expanded / f'invalid_{index}.json'
                write_json(path, changed)
                output = Path(directory) / f'results_{index}'
                with patch('transferable_signal.validation.run') as fake:
                    with self.assertRaises(ValueError):
                        compare_models(path, [model], output)
                    fake.assert_not_called()
                self.assertFalse(output.exists())

    def test_duplicate_model_paths_and_training_seeds_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.model(directory), self.model(directory)
            for models in ([first, first], [first, second]):
                output = Path(directory) / 'results'
                with patch('transferable_signal.validation.run') as fake:
                    with self.assertRaises(ValueError):
                        compare_models(self.suite_path, models, output)
                    fake.assert_not_called()
                self.assertFalse(output.exists())

    def test_untrained_incompatible_or_validation_trained_models_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = [self.model(directory, seed=1, updates=0),
                       self.model(directory, seed=2, training_network_sha256=[]),
                       self.model(directory, seed=3, training_network_sha256=[self.cases['valid_t2'][0]['network_sha256']]),
                       self.model(directory, seed=4)]
            altered = read_json(invalid[-1])
            altered['features'] = ['old_feature']
            write_json(invalid[-1], altered)
            for model in invalid:
                output = Path(directory) / 'results'
                with patch('transferable_signal.validation.run') as fake:
                    with self.assertRaises(ValueError):
                        compare_models(self.suite_path, [model], output)
                    fake.assert_not_called()
                self.assertFalse(output.exists())

    def test_paired_comparison_runs_baselines_once_and_each_seed_model(self):
        with tempfile.TemporaryDirectory() as directory:
            models = [self.model(directory, seed) for seed in (42, 43, 44)]
            output = Path(directory) / 'results'
            with patch('transferable_signal.validation.run', return_value=dict(status='completed', mean_queue_vehicles=1., teleport_events=0)) as fake:
                metadata = compare_models(self.suite_path, models, output)
            self.assertEqual(fake.call_count, 180)
            self.assertEqual(metadata['expected_runs'], 180)
            self.assertEqual(metadata['completed_runs'], 180)
            self.assertEqual(metadata['status'], 'completed')
            with (output / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 180)
            for controller in ('fixed', 'webster', 'max_pressure'):
                selected = [row for row in rows if row['controller'] == controller]
                self.assertEqual(len(selected), 30)
                self.assertEqual(len({row['scenario'] for row in selected}), 30)
                self.assertTrue(all(row['model_seed'] == '' for row in selected))
            for seed in (42, 43, 44):
                selected = [row for row in rows if row['model_label'] == f'smart_seed{seed}']
                self.assertEqual(len(selected), 30)
                self.assertTrue(all(row['model_seed'] == str(seed) for row in selected))
            self.assertEqual({model['sha256'] for model in metadata['models']}, {digest(path) for path in models})
            self.assertEqual(read_json(output / 'manifest.json')['status'], 'completed')
            with patch('transferable_signal.validation.run') as fake:
                with self.assertRaises(FileExistsError):
                    compare_models(self.suite_path, models, output)
                fake.assert_not_called()

    def test_interruption_preserves_completed_rows_and_truthful_status(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.model(directory)
            output = Path(directory) / 'results'
            calls = [dict(status='completed', mean_queue_vehicles=1.), dict(status='completed', mean_queue_vehicles=2.), KeyboardInterrupt()]
            with patch('transferable_signal.validation.run', side_effect=calls):
                with self.assertRaises(KeyboardInterrupt):
                    compare_models(self.suite_path, [model], output)
            metadata = read_json(output / 'manifest.json')
            self.assertEqual(metadata['status'], 'interrupted')
            self.assertEqual(metadata['completed_runs'], 2)
            self.assertEqual(metadata['active_run']['controller'], 'max_pressure')
            with (output / 'comparison.csv').open(encoding='utf-8-sig', newline='') as stream:
                self.assertEqual(len(list(csv.DictReader(stream))), 2)

    def test_runner_failure_recorded_without_overwriting_previous_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            model = self.model(directory)
            output = Path(directory) / 'results'
            with patch('transferable_signal.validation.run', side_effect=RuntimeError('fake failure')):
                with self.assertRaises(RuntimeError):
                    compare_models(self.suite_path, [model], output)
            metadata = read_json(output / 'manifest.json')
            self.assertEqual(metadata['status'], 'failed')
            self.assertEqual(metadata['completed_runs'], 0)
            self.assertIn('fake failure', metadata['error'])


if __name__ == '__main__':
    unittest.main()
