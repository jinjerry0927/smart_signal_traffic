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
from signal_demo.probe import probe


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
        # Covered separately from preflight: a failed run must remain a failure.
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
                self.assertIn('Pi 연결 확인', response.read().decode())
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
