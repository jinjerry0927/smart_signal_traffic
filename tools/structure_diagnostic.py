"""Read-only frozen replay: diagnose weak conditions and draining, never train."""
import argparse
import csv
import json
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import runner
from transferable_signal.control import SignalClock
from transferable_signal.deployment import validated_policy
from transferable_signal.spec import digest, read_json
from transferable_signal.suite import load_suite
from tools import structure_wave as structure, diversity_wave, run_adaptive_wave as common
from tools import diagnose_gyeongju_drain as drain
from tools.diagnose_gyeongju_drain_retry import corrected_auditor

SOURCE = ROOT / 'network/structure_wave6_20261007/suite.json'
REFERENCE = ROOT / 'results/runs/structure_wave6_20261007'
WEAK = ('structure_t1_busy_s00','structure_t2_busy_s01','structure_x3_busy_s00')
RETAINED = ('structure_t3_busy_s01','structure_x3_busy_s00','structure_x3_busy_s01',
            'structure_x3_directional_s00','structure_x3_directional_s01')
JOBS = tuple((s,c) for s in WEAK for c in ('fixed','smart')) + tuple((s,'smart') for s in RETAINED if s not in WEAK)
HORIZON = 1800


class DiagnosticEnvironment(drain.DrainEnvironment):
    active = None

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.edge_previous = {}
        self.service_time = None
        DiagnosticEnvironment.active = self

    def observe(self):
        result = super().observe()
        now = int(self.connection.simulation.getTime())
        if now != self.service_time:
            writer = self._writer('service.csv',['time_s','edge','entered_ids','left_ids'])
            for edge in sorted({self.spec['lanes'][l]['edge'] for ls in self.incoming.values() for l in ls}):
                current = set(self.observer.previous[edge])
                previous = self.edge_previous.get(edge,set())
                writer.writerow(dict(time_s=now,edge=edge,entered_ids=json.dumps(sorted(current-previous)),
                                     left_ids=json.dumps(sorted(previous-current))))
                self.edge_previous[edge] = current
            self.service_time = now
        return result


class RecordingPolicy:
    def __init__(self,policy):
        self.policy = policy

    def __getattr__(self,name):
        return getattr(self.policy,name)

    def action(self,jid,observation,clock):
        action = self.policy.action(jid,observation,clock)
        env = DiagnosticEnvironment.active
        details = dict(getattr(self.policy,'last_decision',None) or {})
        fields = ['time_s','junction','chosen','current','stage','elapsed','state','mask','features','pressure','details']
        env._writer('decisions.csv',fields).writerow(dict(time_s=int(env.connection.simulation.getTime()),
            junction=jid,chosen=int(action),current=clock.current,stage=clock.stage,elapsed=clock.elapsed,
            state=clock.state(),mask=json.dumps(observation['mask'].tolist()),
            features=json.dumps(observation['features'].tolist(),allow_nan=False),
            pressure=json.dumps(observation['pressure'].tolist(),allow_nan=False),details=json.dumps(details,allow_nan=False)))
        return action


def freeze():
    original = structure.verify(SOURCE,REFERENCE)
    suite,cases,base = structure.preflight(SOURCE)
    policy,_,identity = validated_policy()
    proof = read_json(ROOT / '.codex-artifacts/structure-preparation-proof-20261007.json')
    protected = dict(proof['preserved_sha256'],**proof['input_sha256'])
    for path in REFERENCE.rglob('*'):
        if path.is_file():
            protected[str(path.relative_to(ROOT))] = digest(path)
    for relative in ('.codex-artifacts/structure-wave6-independent-verification-20261007.json',):
        protected[relative] = digest(ROOT / relative)
    sources = [Path(__file__),Path(drain.__file__),ROOT / 'tools/diagnose_gyeongju_drain_retry.py',
               Path(common.__file__),Path(diversity_wave.__file__),ROOT / 'tools/structure_wave.py']
    for path in sources:
        protected[str(path.relative_to(ROOT))] = digest(path)
    for name,hashed in base['code_sha256'].items():
        relative = 'evaluation_support.py' if name=='evaluation_support.py' else 'transferable_signal/'+name
        protected[relative] = hashed
    protected[str(Path(identity['profile_path']).relative_to(ROOT))] = identity['profile_sha256']
    reference = {(r['row']['scenario'],r['row']['controller']):r for r in original['records']}
    runtime_meta = read_json(REFERENCE / reference[JOBS[0]]['directory'] / 'manifest.json')
    fp = dict(protected_sha256=protected,policy_identity=identity,code_sha256=base['code_sha256'],
              runtime={k:runtime_meta[k] for k in ('python','sumo')},jobs=[list(j) for j in JOBS],horizon_s=HORIZON)
    ensure_frozen(fp)
    return suite,cases,reference,fp


def ensure_frozen(fp):
    for relative,hashed in fp['protected_sha256'].items():
        if digest(ROOT / relative)!=hashed:
            raise ValueError(f'Frozen source/input/past result changed: {relative}')
    if validated_policy()[2]!=fp['policy_identity']:
        raise ValueError('Frozen validated policy changed')


def prepare_suite(output,suite):
    selected = {s for s,_ in JOBS}
    scenarios = [dict(s,drain_s=1200,routes=str((SOURCE.parent/s['routes']).resolve()))
                 for s in suite['scenarios'] if s['id'] in selected]
    case_ids = {s['case'] for s in scenarios}
    cases = [dict(c,spec=str((SOURCE.parent/c['spec']).resolve())) for c in suite['cases'] if c['id'] in case_ids]
    if len(scenarios)!=7:
        raise ValueError('Exactly seven existing diagnostic conditions required')
    data = dict(version=suite['version'],limits=suite['limits'],cases=cases,scenarios=scenarios,
                diagnostic_only=True,source_suite_sha256=digest(SOURCE),horizon_s=HORIZON)
    path = output / 'prepared_input/suite.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    common.atomic_json(path,data)
    return path


def replay(suite_path,scenario,controller,directory,fp):
    real_factory,real_baseline,real_provenance = runner.validated_policy,runner.Baseline,runner.provenance
    def factory(*args):
        policy,path,identity = real_factory(*args)
        return RecordingPolicy(policy),path,identity
    def provenance(*args):
        return dict(real_provenance(*args),diagnostic_tool_sha256=digest(Path(__file__)))
    with patch.object(runner,'SignalEnvironment',DiagnosticEnvironment), \
         patch.object(runner,'validated_policy',side_effect=factory), \
         patch.object(runner,'Baseline',side_effect=lambda *a:RecordingPolicy(real_baseline(*a))), \
         patch.object(runner,'provenance',side_effect=provenance):
        return runner.run(suite_path,scenario['id'],controller,directory,smart_policy='validated')


def audit_decisions(directory,spec):
    with (directory/'decisions.csv').open(encoding='utf-8',newline='') as stream:
        decisions = list(csv.DictReader(stream))
    ids = {j['id']:j for j in spec['junctions']}
    expected = {(t,j) for t in range(0,HORIZON,5) for j in ids}
    lookup = {(int(r['time_s']),r['junction']):r for r in decisions}
    if len(decisions)!=len(expected) or set(lookup)!=expected:
        raise ValueError('Missing/duplicate diagnostic decisions')
    clocks = {j:SignalClock(data['phases'],spec['limits']) for j,data in ids.items()}
    with (directory/'junctions.csv').open(encoding='utf-8',newline='') as stream:
        for row in csv.DictReader(stream):
            t,j = int(row['time_s']),row['junction']
            clock = clocks[j]
            if t:
                if (t-1)%5==0:
                    choice = lookup[(t-1,j)]
                    if (int(choice['current'])!=clock.current or int(choice['elapsed'])!=clock.elapsed
                            or choice['stage']!=clock.stage or choice['state']!=clock.state()
                            or json.loads(choice['mask'])!=clock.mask().tolist()):
                        raise ValueError('Logged decision safety clock changed')
                    clock.request(int(choice['chosen']))
                clock.tick()
            if (int(row['phase'])!=clock.current or row['stage']!=clock.stage
                    or int(row['green_elapsed_s'])!=clock.elapsed):
                raise ValueError('Per-second signal safety replay differs')
    return decisions


def audit(directory,scenario,controller,suite_path,cases,reference,fp):
    spec = cases[scenario['case']][0]
    verify_fp = dict(policy_identity=fp['policy_identity'],suite_sha256=digest(suite_path),code_sha256=fp['code_sha256'])
    with patch.object(diversity_wave,'DRAIN',1200):
        metrics,warnings = diversity_wave.verify_run(directory,scenario,spec,verify_fp,controller)
    meta = read_json(directory/'manifest.json')
    if any(meta[k]!=v for k,v in fp['runtime'].items()) or meta.get('diagnostic_tool_sha256')!=digest(Path(__file__)):
        raise ValueError('Diagnostic runtime/source changed')
    seconds,_ = corrected_auditor()(directory,spec,scenario['scheduled'])
    decisions = audit_decisions(directory,spec)
    prefix = drain.audit_prefix(directory,reference,scenario,spec)
    snapshots = {str(t):read_json(directory/f'snapshot_{t}.json') for t in (900,1200,1800)}
    ids = {v.get('id') for v in ET.parse(scenario['routes']).getroot().findall('vehicle')}
    for snapshot in snapshots.values():
        drain._snapshot_validate(snapshot,ids)
        row = seconds[snapshot['time_s']]
        if any(snapshot[k]!=int(row[k]) for k in ('running','pending','arrived','departed','queued')):
            raise ValueError('Snapshot differs from the per-second record')
    old_trips = ET.parse(reference/'tripinfo.xml').getroot().findall('tripinfo')
    old_remaining = {t.get('id') for t in old_trips if float(t.get('arrival'))<0}
    if old_remaining!={v['id'] for v in snapshots['900']['vehicles']}:
        raise ValueError('Retained vehicle identities changed')
    state = read_json(directory/'drain_state.json')
    clear = state['first_fully_cleared_after_inflow_s']
    clear_seconds = [int(r['time_s']) for r in seconds if int(r['time_s'])>=600 and
                     int(r['running'])==int(r['pending'])==0 and int(r['arrived'])==scenario['scheduled']]
    if clear!=(min(clear_seconds) if clear_seconds else None):
        raise ValueError('Clearance time differs from raw vehicle curve')
    return dict(scenario=scenario['id'],controller=controller,prefix900=prefix,metrics1800=metrics,
                first_clear_s=clear,remaining={t:s['running']+s['pending'] for t,s in snapshots.items()},
                warning_details=warnings,per_second_rows=len(seconds),decision_rows=len(decisions))


def run_wave(output,resume=False,max_wall_seconds=2700):
    if not math.isfinite(max_wall_seconds) or max_wall_seconds<=0:
        raise ValueError('Finite positive wall limit required')
    started = time.monotonic()
    output = Path(output).resolve()
    if output.exists() and not resume:
        raise FileExistsError('Preserve diagnostic output; unchanged resume only')
    suite,cases,reference,fp = freeze()
    if output.exists():
        meta = read_json(output/'manifest.json')
        if meta['fingerprint']!=fp or meta['status'] in ('failed','safety_hold'):
            raise ValueError('Changed resume or failure requires inspection')
        suite_path = output/'prepared_input/suite.json'
    else:
        if resume:
            raise FileNotFoundError('No existing diagnostic batch')
        output.mkdir(parents=True)
        suite_path = prepare_suite(output,suite)
        meta = dict(status='started',fingerprint=fp,prepared_suite_sha256=digest(suite_path),
                    expected_runs=10,actual_new_runs=0,additional_training=0,records=[])
    prepared,prepared_cases = load_suite(suite_path)
    scenarios = {s['id']:s for s in prepared['scenarios']}
    proof = dict(fingerprint=fp,prepared_suite_sha256=meta['prepared_suite_sha256'])
    proof_path = output/'frozen_before.json'
    if proof_path.exists():
        if read_json(proof_path)!=proof:
            raise ValueError('Pre-run diagnostic proof changed')
    elif meta['actual_new_runs']:
        raise ValueError('Missing pre-run diagnostic proof')
    else:
        common.atomic_json(proof_path,proof)
    common.atomic_json(output/'manifest.json',meta)
    try:
        done = set()
        if digest(suite_path)!=meta['prepared_suite_sha256']:
            raise ValueError('Prepared diagnostic suite changed')
        for record in meta['records']:
            key = record['scenario'],record['controller']
            if key not in JOBS or key in done:
                raise ValueError('Duplicate/unknown diagnostic result')
            actual = audit(output/record['directory'],scenarios[key[0]],key[1],suite_path,prepared_cases,
                           REFERENCE/reference[key]['directory'],fp)
            if dict(actual,directory=record['directory'])!=record:
                raise ValueError('Saved diagnostic record changed')
            done.add(key)
        if meta['actual_new_runs']!=len(done):
            raise ValueError('Diagnostic run counter changed')
        for scenario_id,controller in JOBS:
            key = scenario_id,controller
            if key in done:
                continue
            if time.monotonic()-started>=max_wall_seconds:
                meta['status']='checkpoint'
                common.atomic_json(output/'manifest.json',meta)
                return meta
            ensure_frozen(fp)
            if digest(suite_path)!=meta['prepared_suite_sha256']:
                raise ValueError('Diagnostic input changed')
            relative = str(Path(scenario_id)/controller)
            directory = output/relative
            meta.update(status='running',active_run=relative)
            common.atomic_json(output/'manifest.json',meta)
            if not directory.exists():
                replay(suite_path,scenarios[scenario_id],controller,directory,fp)
            record = audit(directory,scenarios[scenario_id],controller,suite_path,prepared_cases,
                           REFERENCE/reference[key]['directory'],fp)
            meta['records'].append(dict(record,directory=relative))
            meta.update(actual_new_runs=len(meta['records']))
            meta.pop('active_run',None)
            common.atomic_json(output/'manifest.json',meta)
            print(f"Diagnostic {len(meta['records'])}/10: {scenario_id}/{controller}; remaining={record['remaining']}; clear={record['first_clear_s']}; prefix=PASS",flush=True)
        ensure_frozen(fp)
        meta['status']='completed'
        common.atomic_json(output/'manifest.json',meta)
        return meta
    except BaseException as error:
        meta.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else
                    'safety_hold' if isinstance(error,common.SafetyHold) else 'failed',error=f'{type(error).__name__}: {error}')
        common.atomic_json(output/'manifest.json',meta)
        raise


def verify(output):
    output = Path(output).resolve()
    meta = read_json(output/'manifest.json')
    if meta['status']!='completed' or meta['actual_new_runs']!=10 or len(meta['records'])!=10:
        raise ValueError('Diagnostic batch incomplete')
    fp = meta['fingerprint']
    if read_json(output/'frozen_before.json')!=dict(fingerprint=fp,prepared_suite_sha256=meta['prepared_suite_sha256']):
        raise ValueError('Pre-run diagnostic proof changed')
    ensure_frozen(fp)
    suite_path = output/'prepared_input/suite.json'
    if digest(suite_path)!=meta['prepared_suite_sha256']:
        raise ValueError('Diagnostic input changed')
    suite,cases = load_suite(suite_path)
    scenarios = {s['id']:s for s in suite['scenarios']}
    source = read_json(REFERENCE/'manifest.json')
    refs = {(r['row']['scenario'],r['row']['controller']):r for r in source['records']}
    keys = set()
    for record in meta['records']:
        key = record['scenario'],record['controller']
        if key in keys or key not in JOBS or record['directory']!=str(Path(*key)):
            raise ValueError('Diagnostic identity/path changed')
        actual = audit(output/record['directory'],scenarios[key[0]],key[1],suite_path,cases,
                       REFERENCE/refs[key]['directory'],fp)
        if dict(actual,directory=record['directory'])!=record:
            raise ValueError('Diagnostic raw result changed')
        keys.add(key)
    if keys!=set(JOBS):
        raise ValueError('Missing diagnostic pair')
    return meta


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('run','verify'))
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--resume',action='store_true')
    p.add_argument('--max-wall-seconds',type=float,default=2700)
    a = p.parse_args()
    result = run_wave(a.out,a.resume,a.max_wall_seconds) if a.action=='run' else verify(a.out)
    print(f"Diagnostic {result['status']}: {result['actual_new_runs']}/10; training=0",flush=True)
