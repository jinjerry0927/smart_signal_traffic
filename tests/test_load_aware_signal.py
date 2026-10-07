"""Capacity protection, pressure agreement, and shared safety constraints."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from transferable_signal.control import SignalClock
from transferable_signal.spec import DEFAULT_LIMITS
from transferable_signal.deployment import validated_policy
from tools.load_aware_signal_policy import LoadAwareSignalPolicy,VARIANTS
from tools import load_aware_wave as wave,common_rule_wave as original

class Tests(unittest.TestCase):
    def fixture(self,count=4,elapsed=25,variant='load_aware25'):
        obj=LoadAwareSignalPolicy(validated_policy()[1],variant)
        clock=SignalClock([dict(state='G'+'r'*(count-1)) for _ in range(count)],DEFAULT_LIMITS)
        clock.elapsed=elapsed
        f=np.zeros((count,7)); f[:,1]=.2; f[:,0]=.02; f[1,0]=.2; f[0,6]=1
        obs=dict(features=f,pressure=np.array([0,10]+[0]*(count-2)),mask=clock.mask(),critical_ratio=np.full(count,.2))
        obj.base.scores=lambda f:10*f[:,6]
        return obj,clock,obs

    def test_saturated_four_phase_capacity_preserved(self):
        obj,clock,obs=self.fixture()
        self.assertEqual(obj.action('unseen',obs,clock),0)
        self.assertEqual(obj.last_decision['guard_threshold_s'],40)
        clock.elapsed=40
        self.assertEqual(obj.action('unseen',obs,clock),1)

    def test_three_phase_lower_load_releases_at_floor(self):
        obj,clock,obs=self.fixture(count=3)
        self.assertEqual(obj.action('any',obs,clock),1)
        self.assertEqual(obj.last_decision['estimated_greens_s'],[20]*3)
        self.assertTrue(obj.last_decision['capacity_guard'])

    def test_floor30_is_distinct(self):
        obj,clock,obs=self.fixture(count=3,variant='load_aware30')
        self.assertEqual(obj.action('any',obs,clock),0)
        clock.elapsed=30
        self.assertEqual(obj.action('any',obs,clock),1)

    def test_never_force_lower_or_negative_pressure(self):
        obj,clock,obs=self.fixture(count=3)
        for pressure in ([20,10,0],[-10,-5,-8],[0,2,0]):
            obs['pressure']=np.array(pressure)
            self.assertEqual(obj.action('any',obs,clock),0)

    def test_approaching_hold_requires_current_traffic_and_no_old_queue(self):
        obj,clock,obs=self.fixture(count=3,elapsed=20)
        obs['features'][:,1]=.05; obs['pressure'][:]=[0,2,0]
        self.assertEqual(obj.action('any',obs,clock),0)
        obs['features'][0,1]=0
        self.assertEqual(obj.action('any',obs,clock),1)
        obs['features'][0,1]=.05; obs['features'][1,4]=.35
        self.assertEqual(obj.action('any',obs,clock),1)

    def test_minimum_maximum_red_and_clearance_masks(self):
        for variant in VARIANTS:
            obj,clock,obs=self.fixture(count=3,elapsed=10,variant=variant)
            self.assertEqual(obj.action('any',obs,clock),0)
            clock.elapsed=60; obs['mask']=clock.mask()
            self.assertNotEqual(obj.action('any',obs,clock),0)
            clock.elapsed=20; clock.red_age[2]=180; obs['mask']=clock.mask()
            self.assertEqual(obj.action('any',obs,clock),2)
            clock.request(2); obs['mask']=clock.mask()
            self.assertEqual(obj.action('any',obs,clock),2)
            for _ in range(3): clock.tick()
            self.assertEqual(clock.stage,'all_red')
            for _ in range(2): clock.tick()
            self.assertEqual((clock.stage,clock.current),('green',2))

    def test_reject_malformed_flow(self):
        obj,clock,obs=self.fixture()
        for v in ([.1],np.full(4,np.nan),[-1,0,0,0]):
            obs['critical_ratio']=np.array(v)
            with self.assertRaises(ValueError): obj.action('any',obs,clock)

    def test_no_input_mutation_or_junction_id_dependence(self):
        obj,clock,obs=self.fixture(count=3); before=deepcopy(obs)
        self.assertEqual(obj.action('one',obs,clock),obj.action('new-other',obs,clock))
        for k in obs: np.testing.assert_array_equal(obs[k],before[k])

    def test_two_to_six_supported_phase_counts(self):
        for count in range(2,7):
            obj,clock,obs=self.fixture(count=count,elapsed=40)
            value=obj.action('any',obs,clock)
            self.assertTrue(obs['mask'][value])

    def test_isolated_wave_context_restores_original_plan(self):
        before=(original.INPUT,original.OUTPUT,original.plan(),original.policy)
        with wave.context():
            self.assertEqual(original.plan()['max_runs'],216)
            self.assertEqual(len(original.VARIANTS),2)
            self.assertEqual(original.INPUT,wave.INPUT)
        self.assertEqual((original.INPUT,original.OUTPUT,original.plan(),original.policy),before)

if __name__=='__main__': unittest.main()
