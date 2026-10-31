"""Replay a frozen failed run with read-only position/leader/junction telemetry."""
import argparse
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import unseen_transfer_build as build
from tools import unseen_transfer_experiment as experiment
from tools import unseen_transfer_runtime as runtime
from transferable_signal.spec import digest,read_json,write_json
from traci import constants as tc

RESULTS=experiment.DEFAULT_OUTPUT
OUTPUT=ROOT/'results/runs/unseen_webster_cause_20261029'


def diagnostic_environment(target,expected_end):
    class DiagnosticEnvironment(runtime.TracedEnvironment):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.telemetry=(self.output/'cause-telemetry.jsonl').open('x',encoding='utf-8')
            self.extra_subscribed=set()
            self.extra_last=None

        def observe(self):
            try:
                return super().observe()
            finally:
                if self.connection is not None:
                    self.capture()

        def capture(self):
            c=self.connection
            now=int(c.simulation.getTime())
            if now==self.extra_last:return
            ids=set(c.vehicle.getIDList())
            variables=[tc.VAR_SPEED,tc.VAR_WAITING_TIME,tc.VAR_POSITION,tc.VAR_LANE_ID,
                       tc.VAR_LANEPOSITION,tc.VAR_LENGTH,tc.VAR_MINGAP]
            for vid in sorted(ids-self.extra_subscribed):
                c.vehicle.subscribe(vid,variables)
            self.extra_subscribed=ids
            results=c.vehicle.getAllSubscriptionResults()
            vehicles={vid:dict(lane=r[tc.VAR_LANE_ID],lane_pos_m=r[tc.VAR_LANEPOSITION],xy=r[tc.VAR_POSITION],
                length_m=r[tc.VAR_LENGTH],min_gap_m=r[tc.VAR_MINGAP],speed_mps=r[tc.VAR_SPEED],waiting_s=r[tc.VAR_WAITING_TIME])
                for vid,r in sorted(results.items()) if vid in ids}
            ego=None
            if target in ids:
                lane=vehicles[target]['lane']
                chain=[];vid=target;seen={vid}
                for _ in range(8):
                    leader=c.vehicle.getLeader(vid,200)
                    chain.append(dict(vehicle=vid,leader=leader))
                    if leader is None or not leader[0] or leader[0] in seen:break
                    vid=leader[0];seen.add(vid)
                ego=dict(id=target,lane=lane,stopline_distance_m=c.lane.getLength(lane)-vehicles[target]['lane_pos_m'],
                         next_tls=c.vehicle.getNextTLS(target),leader_chain=chain,
                         junction_foes=c.vehicle.getJunctionFoes(target,150),best_lanes=c.vehicle.getBestLanes(target),
                         left_change_state=c.vehicle.getLaneChangeStatePretty(target,1),
                         right_change_state=c.vehicle.getLaneChangeStatePretty(target,-1),
                         stop_state=c.vehicle.getStopState(target),links=c.lane.getLinks(lane,extended=True))
            runtime.emit(self.telemetry,dict(time_s=now,vehicles=vehicles,ego=ego))
            self.extra_last=now
            if now>expected_end:
                raise AssertionError('Replay passed the original first-teleport boundary')

        def close(self):
            try:super().close()
            finally:
                if hasattr(self,'telemetry') and not self.telemetry.closed:self.telemetry.close()
    return DiagnosticEnvironment


def investigate(index):
    fp=experiment.checked_evaluation(build.DEFAULT_INPUT,require_review=True)
    summary=read_json(RESULTS/'reviewed-followup-summary.json')
    failed=summary['failed'][index]
    original=RESULTS/failed['path']
    for name,sha in failed['files_sha256'].items():
        assert digest(original/name)==sha,'Original failure output changed'
    metadata=read_json(original/'manifest.json')
    assert metadata['status']=='failed' and metadata['fingerprint']==fp
    log=(original/'sumo-errors.log').read_text(encoding='utf-8')
    target=re.search(r"Teleporting vehicle '([^']+)'",log).group(1)
    jobs=experiment.jobs_for('final',build.DEFAULT_INPUT)
    scenario,name,(spec,network,_),routes=next(j for j in jobs if j[0]['id']+'/'+j[1]==failed['id'])
    output=OUTPUT/scenario['id']/name
    if output.exists():raise FileExistsError('Preserve existing diagnostic attempt; never auto-retry')
    parent=dict(original_failure_sha256=digest(original/'manifest.json'),diagnostic_source_sha256=digest(Path(__file__)),
                original_fingerprint=fp,purpose='read-only cause diagnosis; not a new performance comparison',target_vehicle=target)
    expected_end=failed['event']['time_s']
    diag_fp=dict(fp,diagnosis=parent)
    error=None
    with patch.object(runtime,'TracedEnvironment',diagnostic_environment(target,expected_end)):
        try:runtime.run_one(spec,network,routes,scenario,name,output,diag_fp,kind='cause_diagnosis')
        except RuntimeError as exc:
            if str(exc)!='Collision or teleport; stop subsequent experiments':raise
            error=str(exc)
    assert error is not None,'Expected original teleport was not reproduced'
    verified={}
    for name in ['frames.jsonl','decisions.jsonl','signals.csv','sumo-errors.log']:
        actual,expected=digest(output/name),digest(original/name)
        if actual!=expected:
            write_json(output/'reproduction-mismatch.json',dict(file=name,actual_sha256=actual,expected_sha256=expected))
            raise AssertionError('Additional reads did not reproduce the original exactly: '+name)
        verified[name]=actual
    def trips(folder):
        return {t.get('id'):t.attrib for t in ET.parse(folder/'tripinfo.xml').getroot().findall('tripinfo')}
    assert trips(output)==trips(original)
    cert=dict(status='PASS_exact_original_reproduction',job=failed['id'],target_vehicle=target,
              original_stop_time_s=expected_end,identical_original_files=verified,trip_records_identical=True,
              telemetry_sha256=digest(output/'cause-telemetry.jsonl'),diagnostic_source_sha256=digest(Path(__file__)),
              original_failure_sha256=digest(original/'manifest.json'),additional_training=0,parameter_changes=0,
              formal_results_replaced=False,expected_teleport_preserved=True)
    write_json(output/'cause-verification.json',cert)
    print('CAUSE REPLAY PASS',failed['id'],target,'exact original frames/actions/signals/log/trips; first teleport',expected_end,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=int,choices=range(5),required=True)
    investigate(parser.parse_args().index)
