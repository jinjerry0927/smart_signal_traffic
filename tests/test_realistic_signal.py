"""Offline only: real XML/weights, synthetic lane samples, clocks and mocked TraCI."""
import copy
import csv
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from realistic_signal.build import load_scenarios
from realistic_signal.control import MovementClock
from realistic_signal.environment import MovementObservations, RealisticEnvironment
from realistic_signal.runner import check, audit, run
from realistic_signal.spec import DEFAULT_SPEC, load_spec, validate_state, validate_junction
from signal_demo.runtime import Demo, map_data
from signal_demo.explanation import capture, snapshot
from signal_demo.realistic import explain_constraints
from signal_demo.release import load_release
from transferable_signal.controllers import Baseline
from transferable_signal.spec import digest, load_spec as load_legacy, write_json


class RealisticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, cls.network = load_spec()
        cls.junctions = {j['id']: j for j in cls.spec['junctions']}

    def setUp(self):
        # Any accidental live launch in these tests is an immediate failure.
        self.enterContext(patch('subprocess.Popen', side_effect=AssertionError('Offline tests cannot launch processes')))
        self.enterContext(patch('traci.start', side_effect=AssertionError('SUMO forbidden')))

    def clock(self, jid='gj_yonggang'):
        return MovementClock(self.junctions[jid], self.spec['limits'])

    def advance(self, clock, seconds):
        for _ in range(seconds):
            clock.tick()

    def observer(self):
        observer = MovementObservations(self.spec)
        observer.samples = {lid: dict(queue=3, vehicles=5) for lid in self.spec['lanes']}
        observer.rates = {l['edge']: 600 for l in self.spec['lanes'].values()}
        return observer

    def fake_finish(self, seconds):
        def finish(env):
            (env.output / 'tripinfo.xml').write_text('<tripinfos/>', encoding='utf-8')
            return dict(collision_vehicle_events=0, teleport_events=0, scheduled_vehicles=680,
                        departed_vehicles=0, arrived_vehicles=0, not_departed_vehicles=680,
                        departed_not_arrived_vehicles=0, running_vehicles_end=0, pending_vehicles_end=0,
                        sample_seconds=seconds)
        return finish

    def test_contract_and_routes_check_never_launch_sumo(self):
        result = check()
        self.assertEqual(result['movements'], 36)
        self.assertFalse(result['simulation_run'])
        self.assertEqual(set(result['junctions'].values()), {4, 5})
        self.assertEqual(result['model_compatibility'], 'experimental_transfer_unvalidated')

    def test_group_coverage_shared_lanes_and_real_conflicts(self):
        for j in self.spec['junctions']:
            validate_junction(j)
            for p in j['phases']:
                validate_state(j, p['state'])
            a, b = j['conflicts'][0]
            bad = ['r'] * len(j['movements'])
            bad[a] = bad[b] = 'G'
            with self.assertRaises(ValueError):
                validate_state(j, ''.join(bad))

    def test_shared_lane_split_and_unserved_movement_rejected(self):
        j = self.junctions['gj_yonggang']
        shared = [m for m in j['movements'] if m['incoming'] == j['movements'][0]['incoming']]
        bad = ['r'] * 12
        bad[shared[0]['index']] = 'G'
        with self.assertRaisesRegex(ValueError, 'Shared-lane'):
            validate_state(j, ''.join(bad))
        j = copy.deepcopy(j)
        for p in j['phases']:
            p['state'] = p['state'][:-1] + 'r'
            p['incoming'] = sorted({m['incoming'] for m in j['movements'] if p['state'][m['index']] == 'G'})
        with self.assertRaises(ValueError):
            validate_junction(j)

    def test_inner_main_lanes_are_left_only_and_all_original_od_routes_survive(self):
        old, _ = load_legacy(ROOT / 'network/transferable_v1/gyeongju/intersection.json')
        for j, previous in zip(self.spec['junctions'], old['junctions']):
            self.assertEqual(j['id'], previous['id'])
            for arm in 'NS':
                lane = next(p for p in previous['phases'] if p['id'] == arm + '_green')['incoming'][-1]
                self.assertEqual({m['turn'] for m in j['movements'] if m['incoming'] == lane}, {'l'})
        _, _, scenarios = load_scenarios(DEFAULT_SPEC)
        self.assertEqual(len(scenarios), 3)
        for s in scenarios:
            self.assertEqual(digest(DEFAULT_SPEC.parent / s['routes']),
                             digest(ROOT / 'network/transferable_v1/gyeongju' / s['routes']))

    def test_minimum_green_illegal_actions_and_pending_target(self):
        c = self.clock()
        for value in (True, np.bool_(True), -1, 50, '1', 1.0, 1):
            with self.assertRaises(ValueError):
                c.request(value)
        self.advance(c, 15)
        c.request(1)
        for value in (0, 2):
            with self.assertRaises(ValueError):
                c.request(value)
        c.request(1)
        self.assertEqual(c.remaining, 3)

    def test_exact_partial_clearance_retains_green_and_delays_opposing_through(self):
        c = self.clock()
        self.advance(c, 15)
        before, target = c.greens[0], c.greens[1]
        c.request(1)
        observed = []
        for _ in range(5):
            observed.append(c.state())
            c.tick()
        for i in np.flatnonzero(before & target):
            self.assertEqual([s[i] for s in observed], ['G'] * 5)
            self.assertEqual(c.green_age[i], 20)
        for i in np.flatnonzero(before & ~target):
            self.assertEqual([s[i] for s in observed], ['y'] * 3 + ['r'] * 2)
        for i in np.flatnonzero(~before & target):
            self.assertEqual([s[i] for s in observed], ['r'] * 5)
            self.assertEqual(c.state()[i], 'G')

    def test_north_through_ends_before_south_left_starts_with_south_through_retained(self):
        c = self.clock()
        self.advance(c, 15); c.request(1); self.advance(c, 20)
        c.request(2)
        observed = []
        for _ in range(5):
            observed.append(c.state()); c.tick()
        retained = c.greens[1] & c.greens[2]
        self.assertTrue(retained.any())
        self.assertTrue(all(all(s[i] == 'G' for s in observed) for i in np.flatnonzero(retained)))
        self.assertEqual(c.state(), c.phases[2]['state'])

    def test_retained_green_cannot_reset_maximum_and_timeout_fails_safe(self):
        c = self.clock()
        self.advance(c, 15); c.request(1); self.advance(c, 35)
        # North through has already had 50s; a new group cannot retain it for 5+15s.
        self.assertFalse(c.mask()[0])
        self.advance(c, 10)
        self.assertEqual(c.forced_changes, 1)
        self.assertEqual(c.stage, 'yellow')
        self.assertLessEqual(max(c.green_age), 60)

    def test_oldest_actual_movement_preempts_a_phase_that_partly_serves_it(self):
        c = self.clock()
        self.advance(c, 15)
        last = next(m['index'] for m in c.junction['movements'] if m['incoming'].startswith('yonggang_west_in'))
        c.movement_red_age[last] = 160
        allowed = np.flatnonzero(c.mask())
        self.assertTrue(all(c.greens[i, last] for i in allowed))

    def test_all_pairs_of_safe_transitions_and_adversarial_long_run_clocks(self):
        rng = random.Random(17)
        for jid in self.junctions:
            for source in range(len(self.junctions[jid]['phases'])):
                for target in range(len(self.junctions[jid]['phases'])):
                    c = self.clock(jid)
                    if source:
                        self.advance(c, 15); c.request(source); self.advance(c, 5)
                    self.advance(c, 15)
                    if c.mask()[target]:
                        c.request(target)
                        for _ in range(5):
                            validate_state(c.junction, c.state()); c.tick()
            for strategy in ('hold', 'random'):
                c = self.clock(jid)
                for second in range(1200):
                    if second % 5 == 0:
                        legal = np.flatnonzero(c.mask()).tolist()
                        c.request(c.current if strategy == 'hold' and c.mask()[c.current] else rng.choice(legal))
                    c.tick()
                    self.assertLessEqual(max(c.green_age), 60)
                    self.assertLessEqual(max(c.movement_red_age), 180)

    def test_frozen_ai_and_three_baselines_use_the_same_v2_mask_without_retraining(self):
        observer = self.observer()
        for jid, junction in self.junctions.items():
            for controller in ('smart', 'fixed', 'webster', 'max_pressure'):
                policy = load_release()[0] if controller == 'smart' else Baseline(controller, self.spec['limits'])
                c = self.clock(jid)
                for _ in range(24):
                    value = observer.junction(junction, c)
                    action = policy.action(jid, value, c)
                    self.assertTrue(c.mask()[action])
                    self.assertEqual(value['features'].shape, (len(c.phases), 7))
                    if controller == 'smart':
                        detail = snapshot(value, c, action, capture(policy, value, action), 0, 'local', observer.samples)
                        explain_constraints(detail, c)
                        self.assertEqual(detail['model_compatibility'], 'experimental_transfer_unvalidated')
                    c.request(action); self.advance(c, 5)

    def test_network_and_spec_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            shutil.copyfile(self.network, target / self.network.name)
            spec = copy.deepcopy(self.spec)
            spec['junctions'][0]['conflicts'] = []
            write_json(target / 'intersection.json', spec)
            with self.assertRaises(ValueError):
                load_spec(target / 'intersection.json')
            write_json(target / 'intersection.json', self.spec)
            with (target / self.network.name).open('a', encoding='utf-8') as stream:
                stream.write('\n')
            with self.assertRaises(ValueError):
                load_spec(target / 'intersection.json')

    def test_web_selects_actual_version_and_blocks_legacy_pi_for_v2(self):
        d = Demo(realistic=True)
        self.addCleanup(d.close)
        self.assertEqual(d.snapshot()['network'], 'realistic_v2')
        self.assertIsNone(d.snapshot()['applied_signal_system'])
        self.assertEqual(d.boot()['networks']['realistic_v2']['signal_system'], 'realistic-v2')
        with self.assertRaises(ValueError):
            d.start(dict(scenario='realistic_v2_busy', mode='edge'))
        with patch.object(threading.Thread, 'start'):
            state = d.start(dict(scenario='realistic_v2_busy', horizon_s=60))
        self.assertEqual(state['signal_system'], 'realistic-v2')
        self.assertIsNone(state['applied_signal_system'])
        legacy = Demo()
        self.addCleanup(legacy.close)
        self.assertNotIn('realistic_v2', legacy.boot()['networks'])

    def test_map_exposes_real_movement_indices_for_the_existing_renderer(self):
        data = map_data(self.network, self.spec)
        for j in self.spec['junctions']:
            for m in j['movements']:
                matches = [c for c in data['connections'] if c['junction'] == j['id'] and c['index'] == m['index']]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]['incoming'], m['incoming'])
                self.assertEqual(matches[0]['outgoing'], m['outgoing'])

    def test_v2_web_worker_applies_v2_and_saves_replayable_manifest_with_mocked_traci(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Demo(directory, realistic=True)
            self.addCleanup(d.close)
            connection = Mock()
            now = [0]
            connection.simulation.getTime.side_effect = lambda: now[0]
            connection.simulationStep.side_effect = lambda: now.__setitem__(0, now[0] + 1)
            connection.vehicle.getIDList.return_value = []
            connection.vehicle.getAllSubscriptionResults.return_value = {}
            connection.lane.getLastStepHaltingNumber.return_value = 0
            connection.lane.getLastStepVehicleNumber.return_value = 0
            connection.lane.getLastStepVehicleIDs.return_value = []
            d.update(speed=60)
            with patch('traci.start'), patch('traci.getConnection', return_value=connection), \
                 patch('sumolib.checkBinary', return_value='sumo'), patch('evaluation_support.EpisodeMetrics.sample'), \
                 patch.object(RealisticEnvironment, 'finish', self.fake_finish(30)):
                d._run('realistic_v2_busy', 'smart', 'local', '', 60, 30)
            state = d.snapshot()
            self.assertEqual(state['status'], 'completed', state['error'])
            self.assertEqual(state['applied_signal_system'], 'realistic-v2')
            self.assertEqual(len(state['junctions']), 3)
            output = Path(state['output'])
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(manifest['model_compatibility'], 'experimental_transfer_unvalidated')
            self.assertEqual(audit(output)['rows'], 90)

    def test_mocked_traci_adapter_logs_each_applied_second_and_audit_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'run'
            connection = Mock()
            now = [0]
            connection.simulation.getTime.side_effect = lambda: now[0]
            connection.simulationStep.side_effect = lambda: now.__setitem__(0, now[0] + 1)
            connection.vehicle.getIDCount.return_value = 0
            connection.simulation.getPendingVehicles.return_value = []
            connection.lane.getLastStepHaltingNumber.return_value = 0
            connection.lane.getLastStepVehicleNumber.return_value = 0
            connection.lane.getLastStepVehicleIDs.return_value = []
            # A pure-Python fake connection advances an integer; no SUMO process.
            with patch('traci.start') as start, patch('traci.getConnection', return_value=connection), \
                 patch('sumolib.checkBinary', return_value='sumo'), \
                 patch('evaluation_support.EpisodeMetrics.sample'), \
                 patch.object(RealisticEnvironment, 'finish', self.fake_finish(100)):
                run(output, controller='fixed', horizon=100)
                start.assert_called_once()
            result = audit(output)
            self.assertEqual(result['rows'], 300)
            self.assertFalse(result['simulation_run'])
            metrics = json.loads((output / 'metrics.json').read_text(encoding='utf-8'))
            bad = dict(metrics, not_departed_vehicles=679)
            write_json(output / 'metrics.json', bad)
            with self.assertRaisesRegex(ValueError, 'Vehicle accounting'):
                audit(output)
            write_json(output / 'metrics.json', metrics)
            with (output / 'signals.csv').open(newline='', encoding='utf-8') as stream:
                reader = csv.DictReader(stream); fields = reader.fieldnames; rows = list(reader)
            rows[55]['state'] = 'G' * 12
            with (output / 'signals.csv').open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, 'replay mismatch'):
                audit(output)


if __name__ == '__main__':
    unittest.main()
