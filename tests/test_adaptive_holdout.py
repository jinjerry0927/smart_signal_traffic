"""Synthetic holdout preparation/execution; no SUMO, netconvert, or training."""
from contextlib import redirect_stdout
import copy
import csv
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import build_adaptive_holdout as builder
from tools import run_adaptive_holdout as holdout
from transferable_signal.spec import DEFAULT_LIMITS, digest, make_spec, read_json, write_json

loader = importlib.util.spec_from_file_location('first_wave_holdout_fixtures', ROOT / 'tests/test_adaptive_wave.py')
fixtures = importlib.util.module_from_spec(loader)
loader.loader.exec_module(fixtures)


class HoldoutTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.WaveTests('test_raw_count_mismatch_rejected')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.scenarios = [dict(item, id=item['id'][:-1] + str(sample), split='test', sample=sample)
                          for item in self.fixture.scenarios for sample in (0, 1)]
        self.output = self.fixture.directory / 'holdout'
        self.freeze = dict(variant='load_gate_pressure_wait40', policy_config=dict(version='adaptive-v2'),
                           model=self.fixture.models[1], source_models=self.fixture.models,
                           core_sha256={}, adaptive_v1_sha256='fixturehash', adaptive_v2_sha256='fixturehash',
                           python='fakePython', sumo='fakeSUMO')
        self.calls = []
        self.interrupt_at = None
        self.complete_interrupt_at = None
        self.collision = False
        self.warning = ''

    def fake_run(self, suite, scenario_id, controller, output, model_path=None):
        scenario = next(item for item in self.scenarios if item['id'] == scenario_id)
        self.calls.append((scenario_id, controller, model_path))
        if len(self.calls) == self.interrupt_at:
            output.mkdir(parents=True)
            (output / 'partial.txt').write_text('preserve interrupted run', encoding='utf-8')
            raise KeyboardInterrupt()
        metrics = self.fixture.metrics(scenario, controller, queue=7 if controller == 'smart' else 10)
        if self.collision:
            metrics['collision_vehicle_events'] = 1
        self.fixture.warning = self.warning
        self.fixture.raw(output, scenario, self.freeze['model']['sha256'], metrics)
        manifest_path = output / 'manifest.json'
        manifest = read_json(manifest_path)
        manifest['suite_sha256'] = 'fixturehash'
        manifest['controller'] = controller
        manifest['model_sha256'] = self.freeze['model']['sha256'] if controller == 'smart' else None
        manifest['code_sha256']['adaptive_v2.py'] = 'fixturehash'
        if controller == 'smart':
            manifest['adaptive_policy'] = dict(version='adaptive-v2', variant=self.freeze['variant'], config=self.freeze['policy_config'])
        else:
            manifest.pop('adaptive_policy')
        write_json(manifest_path, manifest)
        if len(self.calls) == self.complete_interrupt_at:
            raise KeyboardInterrupt()
        return metrics

    def run_fake(self, **kwargs):
        data = dict(scenarios=self.scenarios), self.fixture.cases, copy.deepcopy(self.freeze)
        with patch.object(holdout, '_preflight', return_value=data), \
                patch.object(holdout, 'digest', return_value='fixturehash'), \
                patch.object(holdout.runner, 'run', side_effect=self.fake_run), redirect_stdout(io.StringIO()):
            return holdout.run_holdout(self.output, self.fixture.suite_path, **kwargs)

    def test_exactly_48_paired_runs_without_test_based_reselection(self):
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['actual_new_runs'], 48)
        self.assertEqual(len(set(self.calls)), 48)
        self.assertEqual(len(result['summary']['case_profile_summaries']), 6)
        smart = [call for call in self.calls if call[1] == 'smart']
        self.assertEqual(len(smart), 12)
        self.assertTrue(all(call[2] == self.freeze['model']['path'] for call in smart))
        self.assertTrue(all(call[2] is None for call in self.calls if call[1] != 'smart'))
        self.assertTrue(result['independent_target_met'])

    def test_interrupt_preserves_partial_and_completed_and_resume(self):
        self.interrupt_at = 3
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        saved = read_json(self.output / 'manifest.json')
        path = self.output / saved['records'][0]['directory'] / 'metrics.json'
        content = path.read_bytes()
        self.interrupt_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['actual_new_runs'], 48)
        self.assertEqual(path.read_bytes(), content)
        self.assertEqual(len(list(self.output.rglob('partial.txt'))), 1)

    def test_completed_orphan_recovers_without_repeat(self):
        self.complete_interrupt_at = 3
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake()
        self.complete_interrupt_at = None
        result = self.run_fake(resume=True)
        self.assertEqual(result['actual_new_runs'], 48)
        self.assertEqual(len(self.calls), 48)

    def test_collision_stops_before_second_run(self):
        self.collision = True
        with self.assertRaises(holdout.common.SafetyHold):
            self.run_fake()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(read_json(self.output / 'manifest.json')['status'], 'safety_hold')

    def test_changed_frozen_model_and_saved_metric_block_resume(self):
        self.run_fake()
        manifest_path = self.output / 'manifest.json'
        original = read_json(manifest_path)
        value = copy.deepcopy(original)
        value['records'][0]['row']['mean_queue_vehicles'] = 999
        write_json(manifest_path, value)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'Saved holdout result'):
            self.run_fake(resume=True)
        self.assertEqual(self.calls, [])
        write_json(manifest_path, original)
        self.freeze['model']['sha256'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'frozen policy/model changed'):
            self.run_fake(resume=True)

    def test_walltime_checkpoint_and_resume(self):
        with patch.object(holdout.time, 'monotonic', side_effect=[0, 0, 100]):
            result = self.run_fake(max_wall_seconds=1)
        self.assertEqual(result['status'], 'checkpoint_walltime')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.run_fake(resume=True)['status'], 'completed')

    def test_emergency_warning_recorded_and_does_not_reselect(self):
        self.warning = "Warning: Vehicle 'veh_1' performs emergency braking on lane ':j_0' with decel=7.00, time=1.00.\n"
        result = self.run_fake()
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(all(record['row']['emergency_braking_warnings'] == 1 for record in result['records']))

    def test_unsuccessful_validation_refused_before_holdout_output(self):
        selection = self.fixture.directory / 'selection'
        selection.mkdir()
        for status, target in (('running', False), ('completed', False)):
            write_json(selection / 'manifest.json', dict(status=status, target_met=target))
            with patch.object(holdout.wave2, '_preflight') as source:
                with self.assertRaisesRegex(ValueError, 'target_met=true'):
                    holdout.freeze_selection(selection)
                source.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'target_met=true'):
                builder.prepare_holdout(self.output, selection)
            self.assertFalse(self.output.exists())

    def test_fake_builder_freezes_before_twelve_test_schedules(self):
        seen = []
        def fake_build(case, directory, limits):
            self.assertEqual(read_json(self.output / 'suite.json')['selection_freeze'], self.freeze)
            seen.append(case['id'])
            network = directory / 'network.net.xml'
            network.write_text(case['id'], encoding='utf-8')
            spec = dict(network_sha256=digest(network), limits=limits)
            write_json(directory / 'intersection.json', spec)
            return network, spec
        def fake_demand(routes, profile, duration, seed, path):
            root = ET.Element('routes')
            ET.SubElement(root, 'vehicle', id=f'veh_{seed}', depart='1')
            builder.xml_write(path, root)
            return 1
        with patch.object(holdout, 'freeze_selection', return_value=self.freeze), \
                patch.object(builder, 'excluded_hashes', return_value=({'seen-net'}, {'seen-route'}, {1})), \
                patch.object(builder, 'build_skew', side_effect=fake_build), \
                patch.object(builder, 'catalog', return_value=[]), \
                patch.object(builder, 'demand', side_effect=fake_demand), \
                patch.object(builder, 'load_suite'):
            result = builder.prepare_holdout(self.output)
        self.assertEqual(result['holdout_status'], 'completed')
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(result['scenarios']), 12)
        self.assertTrue(all(scenario['split'] == 'test' for scenario in result['scenarios']))
        self.assertEqual(len({scenario['routes_sha256'] for scenario in result['scenarios']}), 12)
        self.assertEqual(len({scenario['seed'] for scenario in result['scenarios']}), 12)

    def test_successful_validation_selects_lowest_validation_queue_and_rechecks_gates(self):
        from transferable_signal.adaptive_v2 import VARIANTS_V2
        scenarios = [dict(item, id=item['id'][:-1] + str(sample), sample=sample)
                     for item in self.fixture.scenarios for sample in range(5)]
        models = self.fixture.models
        baseline = {(scenario['id'], name): self.fixture.metrics(scenario, name, queue=10)
                    for scenario in scenarios for name in holdout.common.BASELINES}
        references = {(scenario['id'], model['seed']): self.fixture.metrics(scenario, queue=9)
                      for scenario in scenarios for model in models}
        names = list(VARIANTS_V2)
        chosen_variant = names[0]
        pilots = [scenario for scenario in scenarios if scenario['sample'] == 0]
        records, indexed = [], {}
        def record(scenario, variant, model):
            queue = 6 if variant == chosen_variant and model['seed'] == 43 else 7 if variant == chosen_variant else 8.5
            row = dict(self.fixture.metrics(scenario, queue=queue), **holdout.wave2._labels(scenario, variant, model),
                       known_emergency_braking=0, new_emergency_braking=0)
            item = dict(directory=f"fake/{len(records)}", row=row, known_warning_details=[], new_warning_details=[])
            records.append(item)
            indexed[(scenario['id'], variant, model['seed'])] = row
        for variant in names:
            for model in models:
                for scenario in pilots:
                    record(scenario, variant, model)
        for model in models:
            for scenario in scenarios:
                if scenario['sample']:
                    record(scenario, chosen_variant, model)
        pilot_summaries = {}
        for variant in names:
            rows, seeds = [], []
            for model in models:
                chosen = [indexed[(scenario['id'], variant, model['seed'])] for scenario in pilots]
                rows.extend(chosen)
                seeds.append(dict(model_seed=model['seed'], **holdout.wave2._summary(chosen, baseline, references, references)))
            pilot_summaries[variant] = dict(holdout.wave2._summary(rows, baseline, references, references), seed_summaries=seeds)
        self.assertEqual(holdout.wave2._select_candidate(pilot_summaries), chosen_variant)
        full = []
        for model in models:
            rows = [indexed[(scenario['id'], chosen_variant, model['seed'])] for scenario in scenarios]
            summary = dict(model_seed=model['seed'], **holdout.wave2._summary(rows, baseline, references, references))
            summary['gate_failure_reasons'] = holdout.wave2._seed_gate_reasons(summary)
            summary['gate_met'] = not summary['gate_failure_reasons']
            full.append(summary)
        source_fp = dict(suite_sha256='source', source_comparison_sha256='comparison', models=models,
                         core_sha256={}, sumo='fakeSUMO', python='fakePython',
                         first_wave=dict(old_policy_sha256='fixturehash'))
        saved_fp = dict(source_fp, variants={name: dict(VARIANTS_V2[name]) for name in names},
                        criteria=dict(holdout.wave2.CRITERIA), adaptive_v2_sha256='fixturehash', wave2_driver_sha256='fixturehash')
        selection = self.fixture.directory / 'successful_selection'
        selection.mkdir()
        metadata = dict(status='completed', target_met=True, fingerprint=saved_fp, records=records,
                        actual_new_runs=len(records), selected_variant=chosen_variant,
                        pilot_summaries=pilot_summaries, full_summaries=full)
        write_json(selection / 'manifest.json', metadata)
        with (selection / 'comparison.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]['row']))
            writer.writeheader()
            writer.writerows(item['row'] for item in records)
        preflight = (dict(scenarios=scenarios), self.fixture.cases, models, baseline, {}, source_fp, references, references)
        with patch.object(holdout.wave2, '_preflight', return_value=preflight), \
                patch.object(holdout, 'digest', return_value='fixturehash'), \
                patch.object(holdout.wave2, '_record_audit', side_effect=lambda item, *args: (
                    item['row']['scenario'], item['row']['variant'], item['row']['model_seed'])):
            freeze = holdout.freeze_selection(selection)
            self.assertEqual(freeze['model']['seed'], 43)
            self.assertEqual(freeze['variant'], chosen_variant)
            self.assertEqual(freeze['validation_mean_queue_vehicles'], 6)
            metadata['full_summaries'][0]['overall']['mean_queue_vehicles'] = 99
            write_json(selection / 'manifest.json', metadata)
            with self.assertRaisesRegex(ValueError, 'full comparison'):
                holdout.freeze_selection(selection)

    def signal_fixture(self):
        connections = ET.Element('connections')
        for index, source in enumerate(('N_in', 'E_in', 'S_in')):
            ET.SubElement(connections, 'connection', {'from': source, 'to': 'out', 'fromLane': '0',
                                                    'toLane': str(index), 'tl': 'center', 'linkIndex': str(index)})
        signals = ET.Element('tlLogics')
        program = ET.SubElement(signals, 'tlLogic', id='center', programID='0')
        for color, duration in (('G', '25'), ('y', '3'), ('r', '2')):
            for index, arm in enumerate('NES'):
                ET.SubElement(program, 'phase', name=f'{arm}_{color}', duration=duration,
                              state=''.join(color if item == index else 'r' for item in range(3)))
        network = ET.Element('net')
        network.append(copy.deepcopy(program))
        for item, new_index in zip(connections, (2, 0, 1)):
            imported = copy.deepcopy(item)
            imported.set('linkIndex', str(new_index))
            network.append(imported)
        junction = ET.SubElement(network, 'junction', id='center')
        ET.SubElement(junction, 'request', index='0', foes='010', response='010')
        return connections, signals, network

    def test_signal_reordering_preserves_movement_colors_and_foes(self):
        connections, signals, network = self.signal_fixture()
        requests_before = ET.tostring(network.find('junction'))
        colors_before = [item.get('state') for item in signals.findall('./tlLogic/phase')]
        mapping = builder.align_signal_indices(connections, signals, network)
        self.assertEqual(mapping, {0: 2, 1: 0, 2: 1})
        self.assertEqual([item.get('linkIndex') for item in connections], ['2', '0', '1'])
        colors_after = [item.get('state') for item in network.findall('./tlLogic/phase')]
        self.assertEqual(colors_after, [item.get('state') for item in signals.findall('./tlLogic/phase')])
        for before, after in zip(colors_before, colors_after):
            self.assertTrue(all(before[old] == after[new] for old, new in mapping.items()))
        self.assertEqual(ET.tostring(network.find('junction')), requests_before)

    def test_signal_reordering_rejects_missing_duplicate_and_invalid_links_atomically(self):
        for kind in ('missing', 'duplicate_movement', 'duplicate_index', 'noncontiguous', 'phase_changed'):
            connections, signals, network = self.signal_fixture()
            imported = network.findall('connection')
            if kind == 'missing':
                network.remove(imported[0])
            elif kind == 'duplicate_movement':
                network.append(copy.deepcopy(imported[0]))
            elif kind == 'duplicate_index':
                imported[0].set('linkIndex', imported[1].get('linkIndex'))
            elif kind == 'noncontiguous':
                imported[0].set('linkIndex', '9')
            else:
                network.find('./tlLogic/phase').set('duration', '40')
            before = [ET.tostring(item) for item in (connections, signals, network)]
            with self.assertRaises(ValueError, msg=kind):
                builder.align_signal_indices(connections, signals, network)
            self.assertEqual(before, [ET.tostring(item) for item in (connections, signals, network)], kind)

    def test_preserved_failed_skew_counterexample_stays_unsafe_until_signal_remap(self):
        case = ROOT / 'network/transferable_holdout_v2_20261004/holdout_t3_skew'
        if not (case / 'network.net.xml').exists():
            self.skipTest('Preserved local skew failure is unavailable in this checkout')
        original_path = case / 'network.net.xml'
        original_hash = digest(original_path)
        with self.assertRaisesRegex(ValueError, 'Conflicting protected greens'):
            make_spec(original_path, DEFAULT_LIMITS)
        connections = ET.parse(case / 'turns.con.xml').getroot()
        signals = ET.parse(case / 'signals.tll.xml').getroot()
        network = ET.parse(original_path).getroot()
        requests_before = [ET.tostring(item) for item in network.findall('./junction/request')]
        mapping = builder.align_signal_indices(connections, signals, network)
        self.assertEqual(mapping, {0: 7, 1: 8, 2: 9, 3: 10, 4: 11, 5: 0, 6: 1, 7: 2, 8: 3, 9: 4, 10: 5, 11: 6})
        self.assertEqual(requests_before, [ET.tostring(item) for item in network.findall('./junction/request')])
        with tempfile.TemporaryDirectory() as directory:
            copy_path = Path(directory) / 'network.net.xml'
            builder.xml_write(copy_path, network)
            safe = make_spec(copy_path, DEFAULT_LIMITS)
            self.assertEqual(len(safe['junctions'][0]['phases']), 3)
            self.assertEqual([item['id'] for item in safe['junctions'][0]['phases']], ['N_G', 'E_G', 'S_G'])
        self.assertEqual(digest(original_path), original_hash)


if __name__ == '__main__':
    unittest.main()
