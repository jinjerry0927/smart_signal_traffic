"""Bridge contract checks, HTTP controls, and portable Pi import checks."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error, request

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal.control import SignalClock
from transferable_signal.spec import DEFAULT_LIMITS
from signal_demo.bundle import export
from signal_demo.edge import make_server as edge_server
from signal_demo.protocol import EdgeClient, infer, json_bytes, pack_frame, strict_json, unpack_junction
from signal_demo.release import DEFAULT_RELEASE, load_release
from signal_demo.runtime import Demo, StopRequested
from signal_demo.web import make_server as web_server
from signal_demo.probe import probe, diagnostics_match
from signal_demo.explanation import capture, snapshot as decision_snapshot


def sample(elapsed=35, stage='green'):
    clocks, observations = {}, {}
    for count in (3, 4, 5):
        jid = 'j' + str(count)
        clock = SignalClock([dict(state='r' * i + 'G' + 'r' * (count - i - 1)) for i in range(count)], dict(DEFAULT_LIMITS))
        clock.current, clock.elapsed = 1, elapsed
        clock.red_age = np.arange(count, dtype=float) * 25
        clock.red_age[1] = 0
        if stage != 'green':
            clock.request(2)
            if stage == 'all_red':
                for _ in range(3):
                    clock.tick()
        f = np.zeros((count, 7), dtype=np.float32)
        f[:, 0] = np.linspace(.01, .3, count)
        f[:, 1] = np.linspace(.12, .3, count)
        f[:, 2] = .2
        f[:, 3] = .08
        f[:, 4] = clock.red_age / 180
        if clock.stage == 'green':
            f[clock.current, 5:] = [clock.elapsed / 60, 1]
        observations[jid] = dict(features=f, mask=clock.mask(), pressure=np.arange(count, dtype=float),
                                 critical_ratio=np.full(count, .2), queue_ratio=.1)
        clocks[jid] = clock
    return observations, clocks


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy, cls.identity = load_release()

    def payload(self, stage='green'):
        obs, clocks = sample(stage=stage)
        return dict(request_id='test', identity_sha256=self.identity['identity_sha256'],
                    junctions=pack_frame(obs, clocks)), obs, clocks

    def test_all_three_transition_stages_match_local(self):
        for stage in ('green', 'yellow', 'all_red'):
            payload, obs, clocks = self.payload(stage)
            reply = infer(self.policy, self.identity, strict_json(json_bytes(payload)))
            expected = {j: self.policy.action(j, o, clocks[j]) for j, o in obs.items()}
            self.assertEqual(reply['actions'], expected)

    def test_different_contract_rejected(self):
        payload, _, _ = self.payload()
        payload['identity_sha256'] = 'wrong'
        with self.assertRaisesRegex(ValueError, 'identity'):
            infer(self.policy, self.identity, payload)

    def test_safety_mask_tampering_rejected(self):
        payload, _, _ = self.payload('yellow')
        payload['junctions']['j3']['observation']['mask'] = [1, 1, 1]
        with self.assertRaisesRegex(ValueError, 'mask'):
            infer(self.policy, self.identity, payload)

    def test_feature_clock_mismatch_rejected(self):
        payload, _, _ = self.payload()
        payload['junctions']['j3']['observation']['features'][1][6] = 0
        with self.assertRaisesRegex(ValueError, 'timestamp'):
            infer(self.policy, self.identity, payload)

    def test_invalid_stage_and_bool_phase_rejected(self):
        payload, _, _ = self.payload()
        c = payload['junctions']['j3']['clock']
        c['current'] = True
        with self.assertRaises(ValueError):
            infer(self.policy, self.identity, payload)
        c['current'], c['stage'] = 1, 'blue'
        with self.assertRaises(ValueError):
            infer(self.policy, self.identity, payload)

    def test_nonfinite_and_oversized_features_rejected(self):
        payload, _, _ = self.payload()
        f = payload['junctions']['j3']['observation']['features']
        f[0][0] = float('nan')
        with self.assertRaises(ValueError):
            infer(self.policy, self.identity, payload)
        f[0][0] = 3
        with self.assertRaises(ValueError):
            infer(self.policy, self.identity, payload)
        with self.assertRaises(ValueError):
            strict_json('{"a":NaN}')

    def test_client_rejects_stale_illegal_and_wrong_junction_replies(self):
        obs, clocks = sample(elapsed=5)
        for defect in ('stale', 'illegal', 'junctions', 'boolean'):
            client = EdgeClient('http://127.0.0.1:9998', self.identity)
            client.checked = True
            def fake(path, payload, defect=defect):
                reply = dict(request_id=payload['request_id'], identity_sha256=self.identity['identity_sha256'],
                             actions={j: c.current for j, c in clocks.items()}, inference_ms=.1)
                if defect == 'stale':
                    reply['request_id'] = 'old'
                elif defect == 'illegal':
                    reply['actions']['j3'] = 0
                elif defect == 'boolean':
                    reply['actions']['j3'] = True
                else:
                    del reply['actions']['j3']
                return reply
            client._call = fake
            with self.assertRaises(ValueError):
                client.action(obs, clocks)

    def test_edge_http_roundtrip(self):
        server = edge_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = EdgeClient('http://127.0.0.1:' + str(server.server_port), self.identity)
            for stage in ('green', 'yellow', 'all_red'):
                obs, clocks = sample(stage=stage)
                actions = client.action(obs, clocks)
                self.assertEqual(actions, {j: self.policy.action(j, o, clocks[j]) for j, o in obs.items()})
            bad = request.Request(client.url + '/infer', data=b'[]', headers={'Content-Type': 'application/json'})
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(bad)
            self.assertEqual(caught.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_tampered_model_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'model.json').write_bytes(b'{}')
            (target / 'policy.json').write_bytes((DEFAULT_RELEASE / 'policy.json').read_bytes())
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                load_release(target)

    def test_remote_probe_matches_300_actions(self):
        server = edge_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'probe.json'
                probe('http://127.0.0.1:' + str(server.server_port), output)
                result = json.loads(output.read_text(encoding='utf-8'))
                self.assertEqual(result['matched_actions'], 300)
                self.assertEqual(set(result['stages']), {'green', 'yellow', 'all_red'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_bundle_runs_without_sumo_or_torch_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'pi'
            export(output)
            code = "import sys;sys.path.insert(0,'.');from signal_demo.release import load_release;from signal_demo.edge import make_server;p,i=load_release('release');assert not {'sumolib','traci','torch'} & set(sys.modules);print(i['variant'])"
            done = subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=output,
                                  capture_output=True, text=True, timeout=20)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn('load_gate_pressure_wait35', done.stdout)
            with self.assertRaises(FileExistsError):
                export(output)


class DecisionViewTests(unittest.TestCase):
    def setUp(self):
        self.policy, self.identity = load_release()

    def test_each_junction_keeps_its_own_diagnostic(self):
        obs, clocks = sample()
        payload = dict(request_id='diagnostics', identity_sha256=self.identity['identity_sha256'],
                       junctions=pack_frame(obs, clocks))
        reply = infer(self.policy, self.identity, payload)
        for jid, observation in obs.items():
            action = self.policy.action(jid, observation, clocks[jid])
            expected = capture(self.policy, observation, action)
            self.assertEqual(reply['diagnostics'][jid], expected)
            self.assertEqual(reply['actions'][jid], action)
            self.assertEqual(len(expected['scores']), len(clocks[jid].phases))

    def test_capture_is_immutable_and_does_not_change_actions(self):
        obs, clocks = sample()
        before = self.policy.action('j3', obs['j3'], clocks['j3'])
        saved = capture(self.policy, obs['j3'], before)
        original = copy.deepcopy(saved)
        self.policy.action('j5', obs['j5'], clocks['j5'])
        self.assertEqual(saved, original)
        self.assertEqual(self.policy.action('j3', obs['j3'], clocks['j3']), before)

    def test_cross_cpu_score_rounding_does_not_hide_action_changes(self):
        obs, clocks = sample()
        action = self.policy.action('j3', obs['j3'], clocks['j3'])
        original = {'j3': capture(self.policy, obs['j3'], action)}
        rounded = copy.deepcopy(original)
        rounded['j3']['scores'][0] += 1e-7
        self.assertTrue(diagnostics_match(rounded, original))
        rounded['j3']['decision']['action'] = (action + 1) % 3
        self.assertFalse(diagnostics_match(rounded, original))

    def test_wait_guard_explains_a_real_override(self):
        obs, clocks = sample()
        scores = np.array([0., 5., 1., 2., 3.])
        with patch.object(self.policy, 'scores', return_value=scores):
            action = self.policy.action('j5', obs['j5'], clocks['j5'])
            diagnostic = capture(self.policy, obs['j5'], action)
        self.assertEqual(diagnostic['decision']['raw_ai_action'], 1)
        self.assertEqual(action, 4)
        detail = decision_snapshot(obs['j5'], clocks['j5'], action, diagnostic, 125, 'local')
        self.assertEqual(detail['reason'], '긴 대기 보호 보정')
        self.assertEqual(detail['time_s'], 125)
        self.assertEqual(detail['phases'][4]['red_age_s'], 100)

    def test_safety_constraint_is_not_presented_as_free_ai_choice(self):
        for stage in ('green', 'yellow', 'all_red'):
            obs, clocks = sample(elapsed=5 if stage == 'green' else 35, stage=stage)
            action = self.policy.action('j3', obs['j3'], clocks['j3'])
            detail = decision_snapshot(obs['j3'], clocks['j3'], action,
                                      capture(self.policy, obs['j3'], action), 5, 'local')
            self.assertEqual(detail['reason'], '전환 제약에 따른 선택')
            self.assertEqual(sum(p['rank'] is not None for p in detail['phases']), 1)

    def test_old_and_malformed_diagnostics_do_not_change_valid_actions(self):
        obs, clocks = sample()
        actions = {j: self.policy.action(j, o, clocks[j]) for j, o in obs.items()}
        for extra in ({}, {'diagnostics': {'wrong-junction': {}}}):
            client = EdgeClient('http://127.0.0.1:9998', self.identity)
            client.checked = True
            def fake(path, payload):
                return dict(request_id=payload['request_id'], identity_sha256=self.identity['identity_sha256'],
                            actions=actions, inference_ms=.1, **extra)
            client._call = fake
            self.assertEqual(client.action(obs, clocks), actions)
            self.assertEqual(client.last_diagnostics, {})

    def test_queued_vehicle_count_and_raw_seconds_share_one_frame(self):
        obs, clocks = sample()
        clock = clocks['j3']
        clock.phases[0]['incoming'] = ['a', 'b']
        samples = {'a': {'queue': 3}, 'b': {'queue': 7}}
        action = self.policy.action('j3', obs['j3'], clock)
        detail = decision_snapshot(obs['j3'], clock, action, capture(self.policy, obs['j3'], action),
                                   35, 'edge', samples)
        self.assertEqual(detail['phases'][0]['queued_vehicles'], 10)
        self.assertEqual(detail['phases'][0]['features'], obs['j3']['features'][0].tolist())
        self.assertEqual(detail['phases'][0]['red_age_s'], clock.red_age[0])


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = Demo()

    def test_map_is_three_real_junctions_with_lanes(self):
        boot = self.demo.boot()
        self.assertEqual({j['id'] for j in boot['map']['junctions']}, {'gj_yonggang', 'gj_geunhwa', 'gj_seungsam'})
        self.assertEqual(len(boot['map']['edges']), 20)
        self.assertEqual(boot['comparison']['overall']['controllers']['v2']['arrived'], 8591)
        self.assertEqual(boot['comparison']['cases']['gyeongju']['controllers']['v2']['remaining'], 328)

    def test_invalid_settings_and_inactive_commands_rejected(self):
        for options in ({'scenario': 'missing'}, {'horizon_s': 31}, {'speed': float('nan')},
                        {'mode': 'edge', 'controller': 'fixed'}, {'mode': 'edge', 'edge_url': 'https://example.com'}):
            with self.assertRaises(ValueError):
                self.demo.start(options)
        with self.assertRaises(ValueError):
            self.demo.command('resume')

    def test_second_worker_rejected(self):
        class Active:
            def is_alive(self):
                return True
        previous = self.demo.worker
        self.demo.worker = Active()
        try:
            with self.assertRaises(RuntimeError):
                self.demo.start({})
        finally:
            self.demo.worker = previous

    def test_http_page_snapshot_and_origin_guard(self):
        server = web_server(self.demo, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        try:
            with request.urlopen(url + '/') as response:
                self.assertIn('경주 3교차로', response.read().decode())
            with request.urlopen(url + '/api/state') as response:
                self.assertEqual(json.load(response)['status'], 'ready')
            req = request.Request(url + '/api/start', data=b'{}',
                                  headers={'Origin': 'http://external.example', 'Content-Type': 'application/json'})
            with self.assertRaises(error.HTTPError) as caught:
                request.urlopen(req)
            self.assertEqual(caught.exception.code, 403)
            with request.urlopen(url + '/api/boot') as response:
                self.assertEqual(len(json.load(response)['map']['junctions']), 3)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_stop_finalization_failure_is_reported(self):
        class BrokenFinish:
            connection = object()
            def __init__(self, *args, **kwargs):
                pass
            def reset(self):
                raise StopRequested()
            def finish(self):
                raise AssertionError('trip count mismatch')
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as directory:
            demo = Demo(directory)
            with patch('signal_demo.runtime.LiveEnvironment', BrokenFinish):
                demo._run('gyeongju_busy', 'smart', 'local', '', 60, 60)
            self.assertEqual(demo.snapshot()['status'], 'failed')
            out = Path(demo.snapshot()['output'])
            self.assertEqual(json.loads((out / 'manifest.json').read_text())['status'], 'failed')

    def test_preflight_reports_ready_and_unavailable_without_starting_a_run(self):
        demo = Demo()
        edge = edge_server(port=0)
        web = web_server(demo, port=0)
        workers = [threading.Thread(target=s.serve_forever, daemon=True) for s in (edge, web)]
        for worker in workers:
            worker.start()
        url = 'http://127.0.0.1:' + str(web.server_port)
        edge_url = 'http://127.0.0.1:' + str(edge.server_port)
        def check(address):
            req = request.Request(url + '/api/check-edge',
                                  data=json_bytes(dict(edge_url=address)),
                                  headers={'Content-Type': 'application/json'})
            with request.urlopen(req, timeout=3) as response:
                return json.load(response)
        try:
            result = check(edge_url)
            self.assertEqual(result['status'], 'ready')
            self.assertEqual(result['health']['identity'], demo.identity)
            edge.shutdown()
            edge.server_close()
            result = check(edge_url)
            self.assertEqual(result['status'], 'unavailable')
            self.assertIn('error', result)
            self.assertEqual(demo.snapshot()['status'], 'ready')
            self.assertIsNone(demo.worker)
            with request.urlopen(url + '/?mode=edge&speed=30') as response:
                self.assertIn('id="edge-check"', response.read().decode())
            with self.assertRaises(error.HTTPError) as caught:
                check('file:///tmp/model')
            self.assertEqual(caught.exception.code, 400)
        finally:
            for server in (edge, web):
                server.shutdown()
                server.server_close()
            for worker in workers:
                worker.join()

    def test_playback_speed_change_does_not_advance_a_paused_simulation(self):
        demo = Demo()
        with self.assertRaises(ValueError):
            demo.set_speed(60)
        demo.update(status='paused', time_s=35, speed=30)
        changed = demo.set_speed(60)
        self.assertEqual((changed['time_s'], changed['status'], changed['speed']), (35, 'paused', 60))
        for invalid in (True, 0, 61, float('nan')):
            with self.assertRaises(ValueError):
                demo.set_speed(invalid)

    def test_close_failure_still_records_failed_manifest(self):
        class BrokenClose:
            connection = object()
            def __init__(self, *args, **kwargs):
                pass
            def reset(self):
                raise ValueError('failed simulation start')
            def close(self):
                raise ValueError('close failed')
        with tempfile.TemporaryDirectory() as directory:
            demo = Demo(directory)
            with patch('signal_demo.runtime.LiveEnvironment', BrokenClose):
                demo._run('gyeongju_busy', 'smart', 'local', '', 60, 60)
            self.assertEqual(demo.snapshot()['status'], 'failed')
            out = Path(demo.snapshot()['output'])
            manifest = json.loads((out / 'manifest.json').read_text())
            self.assertEqual(manifest['status'], 'failed')
            self.assertIn('close failed', manifest['error'])


if __name__ == '__main__':
    unittest.main()
