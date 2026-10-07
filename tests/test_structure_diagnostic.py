"""Read-only logging, frozen scope, and bounded diagnostic sequencing."""
import copy
import csv
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import structure_diagnostic as diag
from transferable_signal.spec import read_json
from transferable_signal.spec import DEFAULT_LIMITS


class LoggingTests(unittest.TestCase):
    def test_recording_calls_real_policy_once_without_mutating_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            env = object.__new__(diag.DiagnosticEnvironment)
            env.output,env.drain_streams = Path(temp),{}
            env.connection = SimpleNamespace(simulation=SimpleNamespace(getTime=lambda:25))
            obs = dict(mask=np.array([True,True]),features=np.ones((2,7)),pressure=np.array([1.,2.]))
            before = copy.deepcopy(obs)
            calls = []
            policy = SimpleNamespace(data={'unchanged':True},last_decision=dict(mode='test'),
                action=lambda *a:(calls.append(a) or 1))
            clock = SimpleNamespace(current=0,stage='green',elapsed=25,state=lambda:'Grr')
            with patch.object(diag.DiagnosticEnvironment,'active',env):
                wrapped = diag.RecordingPolicy(policy)
                self.assertEqual(wrapped.action('center',obs,clock),1)
                self.assertIs(wrapped.data,policy.data)
            self.assertEqual(len(calls),1)
            for key in obs:
                np.testing.assert_array_equal(obs[key],before[key])
            for stream,_ in env.drain_streams.values():
                stream.close()
            with (Path(temp)/'decisions.csv').open() as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(json.loads(row['details']),{'mode':'test'})
            self.assertEqual(row['chosen'],'1')

    def test_service_logging_reads_existing_samples_once_per_second(self):
        with tempfile.TemporaryDirectory() as temp:
            env = object.__new__(diag.DiagnosticEnvironment)
            env.output,env.drain_streams = Path(temp),{}
            env.incoming = {'center':['N_in_0']}
            env.spec = dict(lanes={'N_in_0':dict(edge='N_in')})
            env.edge_previous,env.service_time = {},None
            timer = [1]
            env.connection = SimpleNamespace(simulation=SimpleNamespace(getTime=lambda:timer[0]))
            env.observer = SimpleNamespace(previous={'N_in':{'v1','v2'}})
            with patch.object(diag.drain.DrainEnvironment,'observe',return_value={}):
                env.observe()
                env.observe()
                timer[0]=2
                env.observer.previous['N_in']={'v2','v3'}
                env.observe()
            self.assertEqual(env.observer.previous['N_in'],{'v2','v3'})
            for stream,_ in env.drain_streams.values():
                stream.close()
            with (Path(temp)/'service.csv').open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows),2)
            self.assertEqual(json.loads(rows[1]['entered_ids']),['v3'])
            self.assertEqual(json.loads(rows[1]['left_ids']),['v1'])

    def test_preparation_changes_only_paths_and_drain(self):
        original = read_json(diag.SOURCE)
        before = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as temp:
            prepared = read_json(diag.prepare_suite(Path(temp),original))
        self.assertEqual(original,before)
        originals = {s['id']:s for s in original['scenarios']}
        self.assertEqual(len(prepared['scenarios']),7)
        for s in prepared['scenarios']:
            expected = dict(originals[s['id']],drain_s=1200,
                            routes=str((diag.SOURCE.parent/originals[s['id']]['routes']).resolve()))
            self.assertEqual(s,expected)

    def test_exact_scope_and_retained_coverage(self):
        self.assertEqual(len(diag.JOBS),10)
        self.assertEqual(len(set(diag.JOBS)),10)
        self.assertEqual(len({s for s,_ in diag.JOBS}),7)
        self.assertTrue(all((s,'fixed') in diag.JOBS and (s,'smart') in diag.JOBS for s in diag.WEAK))
        self.assertTrue(all((s,'smart') in diag.JOBS for s in diag.RETAINED))

    def test_changed_source_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'source.txt'
            path.write_text('changed')
            fp = dict(protected_sha256={str(path):'wrong'})
            with self.assertRaisesRegex(ValueError,'Frozen source'):
                diag.ensure_frozen(fp)

    def test_per_second_clearance_and_illegal_replay_rejected(self):
        spec = dict(limits=DEFAULT_LIMITS,junctions=[dict(id='center',phases=[dict(state='Gr'),dict(state='rG')])])
        decisions = [dict(time_s=t,junction='center',chosen=int(t==15),current=0,elapsed=t,
                          stage='green',state='Gr',mask=json.dumps([True,t==15])) for t in (0,5,10,15)]
        junctions = [dict(time_s=t,junction='center',phase=int(t==20),
                         stage='green' if t<=15 or t==20 else 'yellow' if t<=17 else 'all_red',
                         green_elapsed_s=t if t<=15 else 0 if t==20 else 15) for t in range(21)]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            def save(name,rows):
                with (directory/name).open('w',newline='') as stream:
                    writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
            save('decisions.csv',decisions)
            save('junctions.csv',junctions)
            with patch.object(diag,'HORIZON',20):
                self.assertEqual(len(diag.audit_decisions(directory,spec)),4)
                decisions[-1]['chosen']=0
                save('decisions.csv',decisions)
                with self.assertRaisesRegex(ValueError,'replay differs'):
                    diag.audit_decisions(directory,spec)


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)/'batch'
        source = read_json(diag.SOURCE)
        self.original = source
        self.prepared = dict(scenarios=[dict(s,drain_s=1200) for s in source['scenarios'] if s['id'] in {s for s,_ in diag.JOBS}])
        self.reference = {k:dict(directory=str(Path(*k))) for k in diag.JOBS}
        self.fp = {'test':'same'}
        self.calls = []

    def fake_replay(self,suite,scenario,controller,directory,fp):
        self.calls.append((scenario['id'],controller,scenario['duration_s']+scenario['drain_s']))
        directory.mkdir(parents=True)

    def fake_audit(self,directory,scenario,controller,*args):
        return dict(scenario=scenario['id'],controller=controller,remaining={'900':1,'1200':0,'1800':0},first_clear_s=950)

    def run_fake(self,auditor=None,**kwargs):
        with patch.object(diag,'freeze',return_value=(self.original,{},self.reference,self.fp)), \
             patch.object(diag,'ensure_frozen'), \
             patch.object(diag,'load_suite',return_value=(self.prepared,{})), \
             patch.object(diag,'replay',side_effect=self.fake_replay), \
             patch.object(diag,'audit',side_effect=auditor or self.fake_audit), \
             redirect_stdout(io.StringIO()):
            return diag.run_wave(self.output,**kwargs)

    def test_ten_runs_in_order_without_training(self):
        result = self.run_fake()
        self.assertEqual(result['status'],'completed')
        self.assertEqual([(s,c) for s,c,_ in self.calls],list(diag.JOBS))
        self.assertTrue(all(t==1800 for _,_,t in self.calls))
        self.assertEqual(result['additional_training'],0)

    def test_unsafe_event_stops_before_next_run(self):
        def fail(*a):
            raise diag.common.SafetyHold('Teleport')
        with self.assertRaises(diag.common.SafetyHold):
            self.run_fake(auditor=fail)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(read_json(self.output/'manifest.json')['status'],'safety_hold')
        with self.assertRaisesRegex(ValueError,'failure requires inspection'):
            self.run_fake(resume=True)
        self.assertEqual(len(self.calls),1)

    def test_prefix_mismatch_stops_before_next_run(self):
        def fail(*a):
            raise ValueError('prefix changed')
        with self.assertRaisesRegex(ValueError,'prefix changed'):
            self.run_fake(auditor=fail)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(read_json(self.output/'manifest.json')['status'],'failed')

    def test_resume_reaudits_without_rerunning_completed(self):
        self.run_fake()
        self.calls.clear()
        self.assertEqual(self.run_fake(resume=True)['actual_new_runs'],10)
        self.assertEqual(self.calls,[])

    def test_changed_resume_refused(self):
        self.run_fake()
        self.calls.clear()
        self.fp = {'test':'different'}
        with self.assertRaisesRegex(ValueError,'Changed resume'):
            self.run_fake(resume=True)
        self.assertEqual(self.calls,[])

    def test_existing_output_preserved(self):
        self.run_fake()
        self.calls.clear()
        with self.assertRaises(FileExistsError):
            self.run_fake()
        self.assertEqual(self.calls,[])

    def test_wall_boundary_starts_no_run(self):
        with patch.object(diag.time,'monotonic',side_effect=[0,100]):
            result = self.run_fake(max_wall_seconds=10)
        self.assertEqual(result['status'],'checkpoint')
        self.assertEqual(self.calls,[])


if __name__=='__main__':
    unittest.main(verbosity=2)
