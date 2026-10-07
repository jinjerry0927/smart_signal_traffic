"""Safety, observation invariants, and validation-only selection regressions."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from transferable_signal.control import SignalClock
from transferable_signal.spec import DEFAULT_LIMITS
from transferable_signal.deployment import validated_policy
from tools.common_signal_policy import CommonSignalPolicy,VARIANTS
from tools import common_rule_wave as wave


class PolicyTests(unittest.TestCase):
    def fixture(self,variant='all_density25',count=3,elapsed=25,density=.2):
        clock=SignalClock([dict(state='G'+'r'*(count-1)) for _ in range(count)],DEFAULT_LIMITS)
        clock.elapsed=elapsed
        obj=CommonSignalPolicy(validated_policy()[1],variant)
        obj.base.scores=lambda f:np.array([10]+[0]*(count-1),dtype=float)
        f=np.zeros((count,7)); f[:,1]=density; f[0,6]=1
        obs=dict(features=f,pressure=np.zeros(count),mask=clock.mask())
        return obj,clock,obs

    def test_high_density_guard_selects_queued_alternative(self):
        obj,clock,obs=self.fixture()
        obs['features'][:,0]=[.02,.1,.06]; obs['pressure'][:]=[0,8,4]
        self.assertEqual(obj.base.action('any',obs,clock),0)
        self.assertEqual(obj.action('any',obs,clock),1)
        self.assertTrue(obj.last_decision['common_guard'])

    def test_guard_considers_eligible_others_only(self):
        obj,clock,obs=self.fixture()
        obs['features'][:,0]=[.02,.05,.025]; obs['pressure'][:]=[20,2,15]
        self.assertEqual(obj.action('any',obs,clock),1)

    def test_minimum_green_and_pending_mask_all_variants(self):
        for variant in VARIANTS:
            obj,clock,obs=self.fixture(variant,elapsed=10)
            obs['features'][:,0]=[0,.8,.7]; obs['pressure'][:]=[0,20,15]
            self.assertEqual(obj.action('any',obs,clock),0)
            clock.elapsed=20; clock.request(2); obs['mask']=clock.mask()
            self.assertEqual(obj.action('any',obs,clock),2)
            for _ in range(3): clock.tick()
            self.assertEqual(clock.stage,'all_red')
            for _ in range(2): clock.tick()
            self.assertEqual((clock.stage,clock.current,clock.elapsed),('green',2,0))

    def test_maximum_green_and_oldest_red_override_hold(self):
        for variant in VARIANTS:
            obj,clock,obs=self.fixture(variant,elapsed=60,density=.02)
            obs['pressure'][:]=[20,0,1]
            self.assertNotEqual(obj.action('any',obs,clock),0)
            clock.elapsed=20; clock.red_age[:]=[0,180,100]; obs['mask']=clock.mask()
            self.assertEqual(obj.action('any',obs,clock),1)

    def test_small_pressure_hold_ends_after_25(self):
        obj,clock,obs=self.fixture('balanced25',elapsed=20,density=.05)
        obs['pressure'][:]=[0,2,0]
        self.assertEqual(obj.action('any',obs,clock),0)
        self.assertTrue(obj.last_decision['small_pressure_hold'])
        clock.elapsed=25
        self.assertEqual(obj.action('any',obs,clock),1)

    def test_guard20_responds_earlier(self):
        for variant,expected in [('all_density25',0),('balanced20',1)]:
            obj,clock,obs=self.fixture(variant,elapsed=20)
            obs['features'][:,0]=[.01,.08,.04]; obs['pressure'][:]=[0,8,3]
            self.assertEqual(obj.action('any',obs,clock),expected)

    def test_input_arrays_unmodified_and_junction_name_irrelevant(self):
        obj,clock,obs=self.fixture(); obs['features'][:,0]=[.01,.2,.1]; obs['pressure'][:]=[0,9,4]
        before=deepcopy(obs)
        self.assertEqual(obj.action('one',obs,clock),obj.action('unseen-other-name',obs,clock))
        for key in obs: np.testing.assert_array_equal(obs[key],before[key])

    def test_two_to_six_phases(self):
        for count in range(2,7):
            obj,clock,obs=self.fixture(count=count)
            obs['features'][count-1,0]=.5; obs['pressure'][count-1]=10
            self.assertEqual(obj.action('any',obs,clock),count-1)

    def test_phase_permutation_with_unique_priority(self):
        obj,clock,obs=self.fixture('balanced25')
        obs['features'][:,0]=[.01,.2,.08]; obs['pressure'][:]=[0,9,3]
        self.assertEqual(obj.action('any',obs,clock),1)
        order=np.array([2,0,1]); clock.current=1
        for key in ('features','pressure'): obs[key]=obs[key][order].copy()
        clock.red_age=clock.red_age[order].copy(); obs['mask']=clock.mask()
        obj.base.scores=lambda f:10*f[:,6]
        self.assertEqual(obj.action('any',obs,clock),2)

    def test_reject_nan_and_clock_mask_mismatch(self):
        obj,clock,obs=self.fixture(); obs['features'][1,0]=np.nan
        with self.assertRaises(ValueError): obj.action('any',obs,clock)
        obs['features'][1,0]=0; obs['mask'][2]=False
        with self.assertRaisesRegex(ValueError,'safety clock'): obj.action('any',obs,clock)


class SelectionTests(unittest.TestCase):
    def records(self,values=None):
        values=values or {'fixed':10,'v2':8,'all_density25':7.7,'balanced25':7.5,'balanced20':7.6}
        return [dict(row=dict(scenario=f'{p}_{i}',case=f'geometry{i}',profile=p,sample=i,
            policy_label=l,mean_queue_vehicles=q,arrived_vehicles=20,scheduled_vehicles=20))
            for p in wave.PROFILES for i in range(2) for l,q in values.items()]

    def test_select_only_eligible_lowest_queue(self):
        result=wave.select(self.records())
        self.assertEqual(result['chosen'],'balanced25'); self.assertTrue(result['validation_only'])

    def test_reject_tiny_improvement_and_lower_arrivals(self):
        rows=self.records({'fixed':10,'v2':8,**{l:7.9 for l in VARIANTS}})
        self.assertIsNone(wave.select(rows)['chosen'])
        rows=self.records()
        for r in rows:
            if r['row']['policy_label'] in VARIANTS: r['row']['arrived_vehicles']=19
        self.assertIsNone(wave.select(rows)['chosen'])

    def test_reject_group_regression_despite_better_mean(self):
        rows=self.records()
        for r in rows:
            if r['row']['policy_label'] in VARIANTS:
                r['row']['mean_queue_vehicles']=9 if r['row']['profile']=='quiet' else 4
        self.assertIsNone(wave.select(rows)['chosen'])

    def test_budget_and_disjoint_declared_shapes(self):
        self.assertEqual(wave.plan()['max_runs'],240)
        self.assertEqual(len(VARIANTS),3)
        self.assertFalse({g['id'] for g in wave.VALIDATION}&{g['id'] for g in wave.FINAL})


class SequenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.output=Path(self.temp.name)/'results'
        self.scenario=dict(id='one',case='case',scheduled=10)
        for name,value in [('OUTPUT',self.output),('INPUT',Path(self.temp.name)/'inputs')]:
            p=patch.object(wave,name,value); p.start(); self.addCleanup(p.stop)
        for name,value in [('preflight',{}),('sources',{}),('digest','frozen'),
            ('load_suite',(dict(scenarios=[self.scenario]),{'case':({},None,{})}))]:
            p=patch.object(wave,name,return_value=value); p.start(); self.addCleanup(p.stop)

    def test_safety_hold_stops_first_run_and_rejects_blind_resume(self):
        with patch.object(wave,'replay') as replay,patch.object(wave,'audit',side_effect=wave.common.SafetyHold('teleport')):
            with self.assertRaises(wave.common.SafetyHold): wave.run_stage('validation')
            self.assertEqual(replay.call_count,1)
            self.assertEqual(wave.read_json(self.output/'validation/manifest.json')['status'],'safety_hold')
            with self.assertRaisesRegex(ValueError,'diagnosis'): wave.run_stage('validation',resume=True)
            self.assertEqual(replay.call_count,1)

    def test_time_checkpoint_creates_no_actual_run(self):
        with patch.object(wave.time,'monotonic',side_effect=[0,2]),patch.object(wave,'replay') as replay:
            result=wave.run_stage('validation',max_wall_seconds=1)
            self.assertEqual(result['status'],'checkpoint'); self.assertEqual(result['actual_new_runs'],0)
            replay.assert_not_called()

    def test_existing_output_never_overwritten(self):
        target=self.output/'validation'; target.mkdir(parents=True)
        keep=target/'keep.txt'; keep.write_text('user result')
        with self.assertRaises(FileExistsError): wave.run_stage('validation')
        self.assertEqual(keep.read_text(),'user result')

    def test_no_eligible_candidate_refuses_final_before_output(self):
        records=SelectionTests().records({'fixed':10,'v2':8,**{l:7.9 for l in VARIANTS}})
        selected=wave.select(records)
        self.assertIsNone(selected['chosen'])
        self.output.mkdir(parents=True)
        wave.common.atomic_json(self.output/'selection.json',selected)
        with patch.object(wave,'verify_stage',return_value=dict(records=records)),patch.object(wave,'replay') as replay:
            with self.assertRaisesRegex(ValueError,'No eligible'):
                wave.run_stage('final')
            replay.assert_not_called()
            self.assertFalse((self.output/'final').exists())


if __name__=='__main__': unittest.main()
