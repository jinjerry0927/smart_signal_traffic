"""New-network selection, live-run isolation and audited evidence integration."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib import request,error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from signal_demo.unseen import TrialDemo,NETWORKS
from signal_demo.runtime import Demo,SCENARIOS,NAMES
from signal_demo.web import make_server
from signal_demo.protocol import json_bytes


class TrialWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo=TrialDemo()

    def setUp(self):
        self.demo.select_network('zt_t1_short')

    def test_all_networks_show_matching_junctions_demands_and_model(self):
        expected=dict(gyeongju=3,zt_t1_short=1,zt_x31_asymmetric=1,zt_pair_short=2)
        original_model=self.demo.identity['model_sha256']
        for case in NETWORKS:
            boot=self.demo.select_network(case)
            self.assertEqual(len(boot['map']['junctions']),expected[case])
            self.assertEqual(set(boot['map']['signals']),{j['id'] for j in boot['map']['junctions']})
            self.assertEqual(boot['selected_case'],case)
            self.assertEqual(self.demo.snapshot()['case'],case)
            self.assertEqual(boot['identity']['model_sha256'],original_model)
            self.assertEqual(len(boot['scenarios']),3 if case=='gyeongju' else 9)
            for sid in boot['scenarios']:
                s=next(s for s in self.demo.suite['scenarios'] if s['id']==sid)
                self.assertEqual(s['case'],case)
        self.assertEqual(len(SCENARIOS),3)
        self.assertEqual(len(NAMES),3)

    def test_cross_network_scenario_and_remote_mode_are_rejected_before_worker(self):
        sid=next(iter(self.demo.scenarios))
        self.demo.select_network('zt_pair_short')
        with patch.object(Demo,'start') as start:
            for opts in [dict(scenario=sid),dict(mode='edge'),dict(scenario='not-a-scenario')]:
                with self.assertRaises(ValueError):
                    self.demo.start(opts)
            start.assert_not_called()

    def test_switch_is_rejected_for_running_and_paused_workers(self):
        class Active:
            def is_alive(self):return True
        self.demo.worker=Active()
        try:
            for status in ['running','paused','starting','stopping']:
                self.demo.state['status']=status
                with self.assertRaises(RuntimeError):
                    self.demo.select_network('zt_pair_short')
                self.assertEqual(self.demo.selected_case,'zt_t1_short')
        finally:
            self.demo.worker=None

    def test_comparison_scope_is_new_evidence_with_explicit_missing_cases(self):
        boot=self.demo.select_network('zt_pair_short')
        self.assertEqual(boot['evidence']['case_conditions'],4)
        self.assertEqual(boot['evidence']['common_conditions'],22)
        self.assertEqual(boot['evidence']['controller_coverage']['webster'],dict(completed=22,failed=5))
        self.assertFalse(boot['evidence']['primary_target_evaluable'])
        self.assertFalse(boot['evidence']['learning_target_met'])
        self.assertEqual(boot['evidence']['pairs']['fixed']['paired_conditions'],27)
        self.assertEqual(boot['evidence']['pairs']['webster']['paired_conditions'],22)
        self.assertAlmostEqual(boot['comparison']['cases']['zt_pair_short']['controllers']['v2']['mean_queue'],
                               self.demo.trial_result['cases_common_completed']['zt_pair_short']['controllers']['smart_v2']['mean_queue'])
        self.assertNotIn('gyeongju',boot['comparison']['cases'])

    def test_http_page_selection_snapshot_invalid_selection_and_origin(self):
        server=make_server(self.demo,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        url='http://127.0.0.1:'+str(server.server_port)
        def choose(case,origin=None):
            headers={'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            req=request.Request(url+'/api/select-network',data=json_bytes(dict(case=case)),headers=headers)
            return request.urlopen(req,timeout=5)
        try:
            with request.urlopen(url) as response:
                html=response.read().decode('utf-8')
                self.assertIn('같은 AI를 새로운 도로망에 적용합니다',html)
                self.assertIn('id="network"',html)
            with choose('zt_pair_short') as response:
                self.assertEqual(len(json.load(response)['map']['junctions']),2)
            with request.urlopen(url+'/api/state') as response:
                self.assertEqual(json.load(response)['case'],'zt_pair_short')
            for case,origin,code in [('missing',None,400),('zt_t1_short','http://external.example',403)]:
                with self.assertRaises(error.HTTPError) as caught:
                    choose(case,origin)
                self.assertEqual(caught.exception.code,code)
            self.assertEqual(self.demo.selected_case,'zt_pair_short')
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    unittest.main()
