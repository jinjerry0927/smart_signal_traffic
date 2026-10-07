"""Bounded common-rule selection and untouched held-out evaluation, sequentially."""
import argparse
import csv
import math
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import VERSION, runner
from transferable_signal.deployment import validated_policy
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json
from transferable_signal.suite import catalog, demand, load_suite
from tools import diversity_wave, structure_wave, structure_diagnostic as diagnostic
from tools import diagnose_gyeongju_drain as drain, run_adaptive_wave as common
from tools.build_adaptive_holdout import build_skew
from tools.common_signal_policy import CommonSignalPolicy, VARIANTS

INPUT = ROOT / 'network/common_rule_wave8_20261007'
OUTPUT = ROOT / 'results/runs/common_rule_wave8_20261007'
PROFILES = ('quiet','busy','directional')
VALIDATION = (
    dict(id='ruleval_t1',arms='NES',lanes=1,length_m=240,coords=dict(N=[-20,260],E=[340,15],S=[30,-210])),
    dict(id='ruleval_t2',arms='NES',lanes=2,length_m=340,coords=dict(N=[-10,390],E=[310,25],S=[20,-460])),
    dict(id='ruleval_x2',arms='NESW',lanes=2,length_m=200,coords=dict(N=[-20,230],E=[320,15],S=[30,-240],W=[-240,-25])),
    dict(id='ruleval_x3',arms='NESW',lanes=3,length_m=350,coords=dict(N=[-15,330],E=[470,20],S=[20,-370],W=[-390,-15])),
)
FINAL = tuple(dict(id=f'rulefinal_{a.lower()}{l}',arms='NES' if a=='T' else 'NESW',lanes=l,
    length_m=200+55*l,coords={k:v for k,v in dict(N=[-18-l,190+70*l],E=[240+60*l,18+l],
        S=[22+l,-210-55*l],W=[-170-65*l,-20-l]).items() if a=='X' or k!='W'})
    for a in ('T','X') for l in (1,2,3))


def plan():
    return dict(candidates={k:dict(v) for k,v in VARIANTS.items()}, model_seed=43, additional_training=0,
        validation_geometries=list(VALIDATION), final_geometries=list(FINAL), profiles=list(PROFILES),
        validation_samples=2, final_samples=1, corridor_samples=2, seed_base=91000,
        duration_s=600,drain_s=300, max_runs=240,
        criteria=dict(queue_change_v2=-.02,queue_change_fixed=-.05,arrivals_nondecrease=True,worst_group_v2=.10))


def sources():
    paths = list((ROOT/'transferable_signal').glob('*.py')) + [ROOT/'evaluation_support.py']
    paths += [Path(m.__file__) for m in (common,diversity_wave,structure_wave,diagnostic,drain)]
    paths += [ROOT/'tools'/name for name in ('common_signal_policy.py','common_rule_wave.py',
        'build_adaptive_holdout.py','diagnose_gyeongju_drain_retry.py')]
    return {str(p.relative_to(ROOT)):digest(p) for p in paths}


def prepare():
    if INPUT.exists() or OUTPUT.exists():
        raise FileExistsError('Preserve all previous wave output')
    base,model,identity = validated_policy()
    exclusion = structure_wave.exclusions(base)
    # Preserve every pre-existing result, not just summary metrics.
    preserved = {str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'results').rglob('*') if p.is_file()}
    INPUT.mkdir(parents=True)
    common.atomic_json(INPUT/'proof.json',dict(plan=plan(),source_sha256=sources(),policy_identity=identity,
        preserved_sha256=preserved,excluded=exclusion))
    used_nets,used_routes,used_seeds = (set(exclusion[k]) for k in ('network_sha256','routes_sha256','seeds'))
    for stage,geometries,samples,offset in (('validation',VALIDATION,2,0),('final',FINAL,1,1000)):
        folder=INPUT/stage
        folder.mkdir()
        suite=dict(version=VERSION,preparation_status='preparing',limits=dict(DEFAULT_LIMITS),plan=plan(),
            policy_identity=identity,synthetic=True,cases=[],scenarios=[])
        common.atomic_json(folder/'suite.json',suite)
        cases=[]
        for i,geometry in enumerate(geometries):
            target=folder/geometry['id']
            target.mkdir()
            net,spec=build_skew(geometry,target,DEFAULT_LIMITS)
            if spec['network_sha256'] in used_nets:
                raise ValueError('Geometry duplicates an existing/training network')
            used_nets.add(spec['network_sha256'])
            cases.append((geometry['id'],target/'intersection.json',net,spec,samples,i))
        if stage=='final':
            _,oldcases=load_suite(diversity_wave.SOURCE)
            spec,net,oldcase=oldcases['gyeongju']
            cases.append(('gyeongju',(diversity_wave.SOURCE.parent/oldcase['spec']).resolve(),net,spec,2,6))
        for cid,sp,net,spec,count,i in cases:
            split='validation' if stage=='validation' else 'test'
            suite['cases'].append(dict(id=cid,split=split,spec=str(sp.resolve()),spec_sha256=digest(sp)))
            target=folder/cid
            target.mkdir(exist_ok=True)
            for pi,profile in enumerate(PROFILES):
                for sample in range(count):
                    seed=91000+offset+i*100+pi*10+sample
                    path=target/f'{profile}_s{sample:02d}.rou.xml'
                    scheduled=demand(catalog(net),profile,600,seed,path)
                    hashed=digest(path)
                    if seed in used_seeds or hashed in used_routes:
                        raise ValueError('Demand duplicates existing/other split inputs')
                    used_seeds.add(seed); used_routes.add(hashed)
                    suite['scenarios'].append(dict(id=f'{cid}_{profile}_s{sample:02d}',case=cid,split=split,
                        profile=profile,sample=sample,seed=seed,scheduled=scheduled,synthetic=True,
                        routes=str(path.resolve()),routes_sha256=hashed,duration_s=600,drain_s=300))
        suite['preparation_status']='completed'
        common.atomic_json(folder/'suite.json',suite)
    proof=read_json(INPUT/'proof.json')
    proof['input_sha256']={str(p.relative_to(ROOT)):digest(p) for p in INPUT.rglob('*') if p.is_file() and p.name!='proof.json'}
    for stage in ('validation','final'):
        _,cases=load_suite(INPUT/stage/'suite.json')
        for spec,net,case in cases.values():
            sp=Path(case['spec']); proof['input_sha256'][str(sp.relative_to(ROOT))]=digest(sp)
            proof['input_sha256'][str(net.relative_to(ROOT))]=digest(net)
    common.atomic_json(INPUT/'proof.json',proof)
    preflight()
    return proof


def preflight(preserved=False):
    proof=read_json(INPUT/'proof.json')
    if proof['plan']!=plan() or proof['source_sha256']!=sources() or proof['policy_identity']!=validated_policy()[2]:
        raise ValueError('Frozen plan/model/source changed')
    for relative,hashed in {**proof['input_sha256'],**proof['excluded']['suites'],
                            **(proof['preserved_sha256'] if preserved else {})}.items():
        if digest(ROOT/relative)!=hashed:
            raise ValueError(f'Frozen input/original result changed: {relative}')
    netsets=[]; routes=set(proof['excluded']['routes_sha256']); seeds=set(proof['excluded']['seeds'])
    base,_,identity=validated_policy()
    for stage in ('validation','final'):
        suite,cases=load_suite(INPUT/stage/'suite.json')
        if suite['plan']!=plan() or suite['policy_identity']!=identity or len(suite['scenarios'])!=24:
            raise ValueError('Split plan/policy/condition count differs')
        hashes={spec['network_sha256'] for spec,_,_ in cases.values()}
        if hashes & set(base.data['training']['training_network_sha256']):
            raise ValueError('Model training contaminated new evaluation geometries')
        netsets.append(hashes)
        seen=set()
        for s in suite['scenarios']:
            spec,_,case=cases[s['case']]
            runner._check_scenario(base,s,spec,identity,suite)
            if (s['id'] in seen or s['seed'] in seeds or s['routes_sha256'] in routes
                    or s['duration_s']!=600 or s['drain_s']!=300 or s['synthetic'] is not True
                    or s['split']!=('validation' if stage=='validation' else 'test') or spec['limits']!=DEFAULT_LIMITS):
                raise ValueError('Split/demand/limits contamination')
            vehicles=ET.parse(s['routes']).getroot().findall('vehicle')
            if (len(vehicles)!=s['scheduled'] or len({v.get('id') for v in vehicles})!=len(vehicles)
                    or any(not 0<=float(v.get('depart'))<600 for v in vehicles)):
                raise ValueError('Explicit demand accounting invalid')
            seen.add(s['id']); seeds.add(s['seed']); routes.add(s['routes_sha256'])
    if netsets[0]&netsets[1]:
        raise ValueError('Validation/final geometries overlap')
    return proof


def policy(label):
    base,path,identity=validated_policy()
    if label=='v2':
        return base,path,identity
    if label not in VARIANTS:
        raise ValueError('Unknown policy label')
    obj=CommonSignalPolicy(path,label)
    identity=dict(identity,version=obj.config['version'],variant=label,config=dict(obj.config),
        common_source_sha256=digest(ROOT/'tools/common_signal_policy.py'),experimental=True)
    return obj,path,identity


def replay(suite_path,scenario,label,directory):
    name='smart' if label=='v2' or label in VARIANTS else label
    real_baseline,real_provenance=runner.Baseline,runner.provenance
    def factory(*args):
        obj,path,identity=policy(label)
        return diagnostic.RecordingPolicy(obj),path,identity
    def provenance(*args):
        return dict(real_provenance(*args),common_rule_label=label,common_sources_sha256=sources(),
                    latency_comparable=False)
    with patch.object(runner,'SignalEnvironment',diagnostic.DiagnosticEnvironment), \
         patch.object(runner,'validated_policy',side_effect=factory), \
         patch.object(runner,'Baseline',side_effect=lambda *a:diagnostic.RecordingPolicy(real_baseline(*a))), \
         patch.object(runner,'provenance',side_effect=provenance):
        return runner.run(suite_path,scenario['id'],name,directory,smart_policy='validated')


def audit(suite_path,scenario,spec,label,directory):
    controller='smart' if label=='v2' or label in VARIANTS else label
    identity=policy(label)[2] if controller=='smart' else validated_policy()[2]
    codes={p.name:digest(p) for p in (ROOT/'transferable_signal').glob('*.py')}
    codes['evaluation_support.py']=digest(ROOT/'evaluation_support.py')
    fp=dict(policy_identity=identity,suite_sha256=digest(suite_path),code_sha256=codes)
    row,warnings=diversity_wave.verify_run(directory,scenario,spec,fp,controller)
    meta=read_json(directory/'manifest.json')
    if meta['common_sources_sha256']!=sources() or meta['common_rule_label']!=label or meta['latency_comparable'] is not False:
        raise ValueError('Experiment source/label changed')
    with patch.object(drain,'HORIZON',900),patch.object(drain,'SNAPSHOTS',(900,)):
        seconds,_=diagnostic.corrected_auditor()(directory,spec,scenario['scheduled'])
    with patch.object(diagnostic,'HORIZON',900):
        decisions=diagnostic.audit_decisions(directory,spec)
    for field,column in (('mean_queue_vehicles','queued'),('mean_system_total_waiting_time_s','system_current_wait_s')):
        actual=statistics.mean(float(r[column]) for r in seconds[1:])
        if not math.isclose(actual,row[field],rel_tol=1e-9,abs_tol=1e-8):
            raise ValueError('Queue/wait metric differs from original per-second trace')
    if max(int(r['queued']) for r in seconds[1:])!=row['max_queue_vehicles']:
        raise ValueError('Max queue differs from trace')
    vehicleids={v.get('id') for v in ET.parse(scenario['routes']).getroot().findall('vehicle')}
    drain._snapshot_validate(read_json(directory/'snapshot_900.json'),vehicleids)
    row.update(case=scenario['case'],policy_label=label)
    return dict(directory=str(directory.relative_to(OUTPUT)),row=row,warning_details=warnings,
        decisions=len(decisions),raw_sha256={p.name:digest(p) for p in directory.iterdir() if p.is_file()})


def comparison(records,label):
    indexed={(r['row']['scenario'],r['row']['policy_label']):r['row'] for r in records}
    ids=sorted(s for s,l in indexed if l==label)
    def group(selected):
        out={}
        for l in sorted({l for s,l in indexed}):
            rows=[indexed[(s,l)] for s in selected]
            out[l]=dict(mean_queue=statistics.mean(r['mean_queue_vehicles'] for r in rows),
                arrived=sum(r['arrived_vehicles'] for r in rows),scheduled=sum(r['scheduled_vehicles'] for r in rows),
                remaining=sum(r['scheduled_vehicles']-r['arrived_vehicles'] for r in rows))
        return dict(controllers=out,queue_change={l:out[label]['mean_queue']/out[l]['mean_queue']-1 for l in out if l!=label})
    overall=group(ids)
    bycase={c:group([s for s in ids if indexed[(s,label)]['case']==c])
            for c in sorted({indexed[(s,label)]['case'] for s in ids})}
    byprofile={p:group([s for s in ids if indexed[(s,label)]['profile']==p]) for p in PROFILES}
    worst=max(g['queue_change']['v2'] for g in [*bycase.values(),*byprofile.values()])
    criteria=plan()['criteria']; ctrl=overall['controllers']; changes=overall['queue_change']
    eligible=(changes['v2']<=criteria['queue_change_v2'] and changes['fixed']<=criteria['queue_change_fixed']
        and ctrl[label]['arrived']>=max(ctrl['v2']['arrived'],ctrl['fixed']['arrived'])
        and worst<=criteria['worst_group_v2'])
    return dict(overall=overall,cases=bycase,profiles=byprofile,worst_group_change_v2=worst,target_met=eligible,
        conditions={s:group([s]) for s in ids})


def select(records):
    summaries={l:comparison(records,l) for l in VARIANTS}
    eligible=[l for l in VARIANTS if summaries[l]['target_met']]
    chosen=min(eligible,key=lambda l:(summaries[l]['overall']['controllers'][l]['mean_queue'],
               -summaries[l]['overall']['controllers'][l]['arrived'],list(VARIANTS).index(l))) if eligible else None
    return dict(chosen=chosen,summaries=summaries,validation_only=True,additional_training=0)


def verify_stage(stage):
    preflight()
    path=OUTPUT/stage/'manifest.json'; meta=read_json(path)
    suite_path=INPUT/stage/'suite.json'; suite,cases=load_suite(suite_path)
    scenarios={s['id']:s for s in suite['scenarios']}
    labels=['fixed','v2',*VARIANTS] if stage=='validation' else ['fixed','webster','max_pressure','v2',read_json(OUTPUT/'selection.json')['chosen']]
    if meta['status']!='completed' or len(meta['records'])!=120 or meta['actual_new_runs']!=120 or meta['labels']!=labels:
        raise ValueError('Incomplete/changed paired stage')
    keys=set()
    for r in meta['records']:
        row=r['row']; key=row['scenario'],row['policy_label']
        if key in keys or key[0] not in scenarios or key[1] not in labels:
            raise ValueError('Duplicate/unexpected run')
        s=scenarios[key[0]]
        actual=audit(suite_path,s,cases[s['case']][0],key[1],OUTPUT/r['directory'])
        if actual!=r:
            raise ValueError('Saved experiment record changed')
        keys.add(key)
    if keys!={(s,l) for s in scenarios for l in labels}:
        raise ValueError('Missing paired run')
    return meta


def run_stage(stage,resume=False,max_wall_seconds=2700):
    if not math.isfinite(max_wall_seconds) or max_wall_seconds<=0:
        raise ValueError('Invalid bounded wall time')
    start=time.monotonic(); proof=preflight(preserved=True)
    suite_path=INPUT/stage/'suite.json'; suite,cases=load_suite(suite_path)
    labels=['fixed','v2',*VARIANTS]
    selection_hash=None
    if stage=='final':
        validation=verify_stage('validation'); selected=read_json(OUTPUT/'selection.json')
        if selected!=select(validation['records']) or selected['chosen'] not in VARIANTS:
            raise ValueError('No eligible frozen validation-only selection')
        labels=['fixed','webster','max_pressure','v2',selected['chosen']]
        selection_hash=digest(OUTPUT/'selection.json')
    out=OUTPUT/stage; fp=dict(proof_sha256=digest(INPUT/'proof.json'),suite_sha256=digest(suite_path),
                             selection_sha256=selection_hash,source_sha256=sources())
    if out.exists():
        if not resume:
            raise FileExistsError('Preserve prior stage')
        meta=read_json(out/'manifest.json')
        if meta['fingerprint']!=fp or meta['labels']!=labels or meta['status'] in ('failed','safety_hold'):
            raise ValueError('Changed resume or failure needs diagnosis')
    else:
        if resume:
            raise FileNotFoundError('No stage to resume')
        out.mkdir(parents=True)
        meta=dict(status='started',labels=labels,expected_runs=120,actual_new_runs=0,additional_training=0,
                  fingerprint=fp,records=[])
    common.atomic_json(out/'manifest.json',meta)
    try:
        completed=set()
        scenarios={s['id']:s for s in suite['scenarios']}
        for r in meta['records']:
            s=scenarios[r['row']['scenario']]; l=r['row']['policy_label']
            if audit(suite_path,s,cases[s['case']][0],l,OUTPUT/r['directory'])!=r or (s['id'],l) in completed:
                raise ValueError('Resume record differs/duplicated')
            completed.add((s['id'],l))
        if len(completed)!=meta['actual_new_runs']:
            raise ValueError('Resume run count differs')
        for s in suite['scenarios']:
            for l in labels:
                if (s['id'],l) in completed:
                    continue
                if time.monotonic()-start>=max_wall_seconds:
                    meta['status']='checkpoint'; common.atomic_json(out/'manifest.json',meta); return meta
                preflight()
                directory=out/s['id']/l
                meta.update(status='running',active_run=str(directory.relative_to(OUTPUT)))
                common.atomic_json(out/'manifest.json',meta)
                if not directory.exists():
                    replay(suite_path,s,l,directory)
                record=audit(suite_path,s,cases[s['case']][0],l,directory)
                meta['records'].append(record); meta['actual_new_runs']=len(meta['records'])
                meta.pop('active_run',None); common.atomic_json(out/'manifest.json',meta)
                print(f"{stage} {meta['actual_new_runs']}/120 {s['id']}/{l}: queue={record['row']['mean_queue_vehicles']:.3f} arrived={record['row']['arrived_vehicles']}/{s['scheduled']} braking={len(record['warning_details'])}",flush=True)
        meta['status']='completed'; common.atomic_json(out/'manifest.json',meta)
        verify_stage(stage); preflight(preserved=True)
        if stage=='validation':
            selected=select(meta['records'])
            path=OUTPUT/'selection.json'
            if path.exists() and read_json(path)!=selected:
                raise ValueError('Frozen selection differs')
            common.atomic_json(path,selected)
            print(f"Validation selected: {selected['chosen']}",flush=True)
        else:
            summary=comparison(meta['records'],labels[-1])
            common.atomic_json(OUTPUT/'final_summary.json',summary)
            print(f"Final target_met={summary['target_met']}; changes={summary['overall']['queue_change']}",flush=True)
        return meta
    except BaseException as error:
        meta.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else
                    'safety_hold' if isinstance(error,common.SafetyHold) else 'failed',error=f'{type(error).__name__}: {error}')
        common.atomic_json(out/'manifest.json',meta)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','run','verify'))
    parser.add_argument('--stage',choices=('validation','final'),default='validation')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--max-wall-seconds',type=float,default=2700)
    args=parser.parse_args()
    if args.action=='prepare':
        proof=prepare(); print(f"Prepared 48 conditions; preserved={len(proof['preserved_sha256'])}; no traffic/training")
    elif args.action=='run':
        meta=run_stage(args.stage,args.resume,args.max_wall_seconds)
        print(f"Stage {meta['status']} {meta['actual_new_runs']}/120")
    else:
        meta=verify_stage(args.stage); preflight(preserved=True)
        print(f"Raw verification PASS {args.stage}: {meta['actual_new_runs']} runs")
