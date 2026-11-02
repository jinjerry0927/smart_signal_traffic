"""Regression checks for presentation state, controls and original-model evidence."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import error, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from signal_demo.explanation import snapshot as decision_snapshot
from signal_demo.protocol import EdgeClient, json_bytes
from signal_demo.runtime import Demo, StopRequested
from signal_demo.web import make_server
from test_signal_demo import sample


class PresentationReadinessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.demo = Demo(self.directory.name)
        self.addCleanup(self.demo.close)

    def test_selection_resets_previous_run_and_uses_existing_untrained_networks(self):
        self.demo.update(output='old', link_error='old', decisions={'old': {}}, result={'old': True})
        revision = self.demo.snapshot()['revision']
        for network in ('valid_t2', 'valid_x3', 'gyeongju'):
            state = self.demo.select_network(network)
            self.assertGreater(state['revision'], revision)
            revision = state['revision']
            self.assertEqual(state['status'], 'ready')
            self.assertEqual(state['decisions'], {})
            for key in ('output', 'link_error', 'result', 'run_id'):
                self.assertIsNone(state[key])
            data = self.demo.boot()['networks'][network]
            self.assertEqual(len(data['map']['junctions']), 3 if network == 'gyeongju' else 1)
            self.assertEqual(len(data['scenarios']), 3)
            spec = self.demo.cases[network][0]
            self.assertNotIn(spec['network_sha256'], self.demo.policy.data['training']['training_network_sha256'])

    def test_start_clears_old_output_error_and_uses_new_run_id(self):
        self.demo.update(output='old', link_error='old', diagnostic_error='old', run_id='old')
        with patch.object(threading.Thread, 'start'):
            state = self.demo.start(dict(scenario='valid_t2_busy', horizon_s=60))
        self.assertEqual(state['network'], 'valid_t2')
        self.assertNotEqual(state['run_id'], 'old')
        self.assertEqual(state['junctions'], [])
        for key in ('output', 'link_error', 'diagnostic_error'):
            self.assertIsNone(state[key])

    def test_speed_and_network_guards_do_not_touch_policy(self):
        self.demo.update(status='running', speed=10)
        identity = self.demo.identity.copy()
        self.assertEqual(self.demo.set_speed(30)['speed'], 30)
        self.assertEqual(self.demo.identity, identity)
        for value in (True, float('nan'), float('inf'), 0, 61, '30'):
            with self.assertRaises(ValueError):
                self.demo.set_speed(value)
        self.demo.update(status='finalizing')
        with self.assertRaises(ValueError):
            self.demo.set_speed(10)
        class Active:
            def is_alive(self):
                return True
        with patch.object(self.demo, 'worker', Active()):
            with self.assertRaises(RuntimeError):
                self.demo.select_network('valid_x3')

    def test_terminal_state_is_published_after_manifest_and_close(self):
        entered, release = threading.Event(), threading.Event()
        class ClosingEnvironment:
            connection = object()
            def __init__(self, *args, **kwargs):
                pass
            def reset(self):
                raise StopRequested()
            def finish(self):
                return {'sample_seconds': 0}
            def close(self):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test close not released')
        with patch('signal_demo.runtime.LiveEnvironment', ClosingEnvironment):
            self.demo.start(dict(horizon_s=60))
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.demo.snapshot()['status'], 'finalizing')
                with self.assertRaises(RuntimeError):
                    self.demo.start({})
            finally:
                release.set()
                self.demo.worker.join(5)
        state = self.demo.snapshot()
        self.assertFalse(state['worker_active'])
        self.assertEqual(state['status'], 'stopped')
        saved = json.loads((Path(state['output']) / 'manifest.json').read_text())
        self.assertEqual(saved['status'], 'stopped')

    def test_manifest_failure_is_never_a_completed_or_stopped_run(self):
        class StoppedEnvironment:
            connection = None
            def __init__(self, *args, **kwargs):
                pass
            def reset(self):
                raise StopRequested()
            def close(self):
                pass
        from signal_demo.runtime import write_json
        def fail_final_manifest(path, value):
            if path.name == 'manifest.json' and value['status'] == 'stopped':
                raise OSError('disk unavailable')
            return write_json(path, value)
        with patch('signal_demo.runtime.LiveEnvironment', StoppedEnvironment), \
             patch('signal_demo.runtime.write_json', side_effect=fail_final_manifest):
            self.demo._run('gyeongju_busy', 'smart', 'local', '', 60, 60)
        self.assertEqual(self.demo.snapshot()['status'], 'failed')
        self.assertIn('disk unavailable', self.demo.snapshot()['error'])

    def test_baselines_do_not_claim_model_inference(self):
        observations, clocks = sample()
        for controller in ('fixed', 'webster', 'max_pressure'):
            detail = decision_snapshot(observations['j3'], clocks['j3'], 1, None, 35, 'local',
                                       controller=controller)
            self.assertIsNone(detail['raw_ai_action'])
            self.assertFalse(detail['diagnostic_available'])
            self.assertIn('추론하지 않습니다', detail['explanation'])
            self.assertTrue(all(p['score'] is None for p in detail['phases']))

    def test_edge_connection_check_and_malformed_http_inputs(self):
        server = make_server(self.demo, port=0)
        service = threading.Thread(target=server.serve_forever, daemon=True)
        service.start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        try:
            for payload, origin in (([], ''), ({}, 'http://localhost:invalid')):
                req = request.Request(url + '/api/command', data=json_bytes(payload),
                                      headers={'Content-Type': 'application/json', 'Origin': origin})
                with self.assertRaises(error.HTTPError) as caught:
                    request.urlopen(req, timeout=3)
                self.assertEqual(caught.exception.code, 400)
            req = request.Request(url + '/api/network', data=json_bytes({'network': 'valid_x3'}),
                                  headers={'Content-Type': 'application/json'})
            with request.urlopen(req, timeout=3) as response:
                self.assertEqual(json.load(response)['network'], 'valid_x3')
            with patch.object(EdgeClient, 'check', return_value={'hostname': 'test-pc', 'system': 'Windows', 'machine': 'AMD64'}):
                report = self.demo.check_edge('http://127.0.0.1:9998')
            self.assertEqual(report['hostname'], 'test-pc')
            self.assertGreaterEqual(report['roundtrip_ms'], 0)
            self.assertEqual(self.demo.snapshot()['link'], 'local')
        finally:
            server.shutdown()
            server.server_close()
            service.join()


if __name__ == '__main__':
    unittest.main()
