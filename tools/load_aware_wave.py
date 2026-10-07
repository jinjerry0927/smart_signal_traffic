"""Second bounded correction wave; fresh validation and untouched final demand."""
import argparse
from contextlib import ExitStack,contextmanager
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import common_rule_wave as original
from tools import structure_wave,run_adaptive_wave as common
from tools.load_aware_signal_policy import LoadAwareSignalPolicy,VARIANTS
from transferable_signal.deployment import validated_policy
from transferable_signal.spec import digest,read_json
from transferable_signal.suite import catalog,demand,load_suite

INPUT=ROOT/'network/load_aware_wave9_20261007'
OUTPUT=ROOT/'results/runs/load_aware_wave9_20261007'
BASE_SOURCES,BASE_PLAN=original.sources,original.plan

def sources():
    result=BASE_SOURCES()
    for name in ('load_aware_wave.py','load_aware_signal_policy.py'):
        p=ROOT/'tools'/name; result[str(p.relative_to(ROOT))]=digest(p)
    return result

def plan():
    result=BASE_PLAN()
    result.update(candidates={k:dict(v) for k,v in VARIANTS.items()},max_runs=216,seed_base=97000,
        validation_kind='known development geometries / fresh demand',
        final_kind='previously prepared, never evaluated final inputs',wave=9)
    return result

def policy(label):
    base,path,identity=validated_policy()
    if label=='v2': return base,path,identity
    obj=LoadAwareSignalPolicy(path,label)
    return obj,path,dict(identity,version=obj.config['version'],variant=label,config=dict(obj.config),
        common_source_sha256=digest(ROOT/'tools/load_aware_signal_policy.py'),experimental=True)

@contextmanager
def context():
    with ExitStack() as stack:
        for name,value in dict(INPUT=INPUT,OUTPUT=OUTPUT,VARIANTS=VARIANTS,sources=sources,plan=plan,policy=policy).items():
            stack.enter_context(patch.object(original,name,value))
        yield

def prepare():
    if INPUT.exists() or OUTPUT.exists(): raise FileExistsError('Preserve all previous output')
    old=original.verify_stage('validation')
    if read_json(original.OUTPUT/'selection.json')['chosen'] is not None or (original.OUTPUT/'final').exists():
        raise ValueError('Only a rejected wave with untouched final cases can be continued')
    oldproof=original.preflight(preserved=True)
    base,_,identity=validated_policy()
    exclusion=structure_wave.exclusions(base)
    final,_=load_suite(original.INPUT/'final/suite.json')
    unused_seeds={s['seed'] for s in final['scenarios']}; unused_routes={s['routes_sha256'] for s in final['scenarios']}
    exclusion['seeds']=[s for s in exclusion['seeds'] if s not in unused_seeds]
    exclusion['routes_sha256']=[h for h in exclusion['routes_sha256'] if h not in unused_routes]
    proof=dict(plan=plan(),source_sha256=sources(),policy_identity=identity,excluded=exclusion,
        prior_selection_sha256=digest(original.OUTPUT/'selection.json'),
        untouched_final_suite_sha256=digest(original.INPUT/'final/suite.json'),
        preserved_sha256={str(p.relative_to(ROOT)):digest(p) for p in (ROOT/'results').rglob('*') if p.is_file()})
    INPUT.mkdir(parents=True)
    common.atomic_json(INPUT/'proof.json',proof)
    validation,cases=load_suite(original.INPUT/'validation/suite.json')
    fresh=dict(validation,plan=plan(),scenarios=[])
    folder=INPUT/'validation'; folder.mkdir()
    for i,case in enumerate(validation['cases']):
        cid=case['id']; spec,net,_=cases[cid]; target=folder/cid; target.mkdir()
        for pi,profile in enumerate(original.PROFILES):
            for sample in range(2):
                seed=97000+i*100+pi*10+sample
                path=target/f'{profile}_s{sample:02d}.rou.xml'
                count=demand(catalog(net),profile,600,seed,path)
                fresh['scenarios'].append(dict(id=f'load_{cid}_{profile}_s{sample:02d}',case=cid,split='validation',
                    profile=profile,sample=sample,seed=seed,scheduled=count,synthetic=True,routes=str(path.resolve()),
                    routes_sha256=digest(path),duration_s=600,drain_s=300))
    common.atomic_json(folder/'suite.json',fresh)
    (INPUT/'final').mkdir()
    common.atomic_json(INPUT/'final/suite.json',dict(final,plan=plan()))
    proof['input_sha256']={**oldproof['input_sha256'],
        **{str(p.relative_to(ROOT)):digest(p) for p in INPUT.rglob('*') if p.is_file() and p.name!='proof.json'}}
    common.atomic_json(INPUT/'proof.json',proof)
    with context(): original.preflight(preserved=True)
    return proof

def verify_stage(stage):
    original.preflight()
    suite_path=INPUT/stage/'suite.json'; suite,cases=load_suite(suite_path)
    scenarios={s['id']:s for s in suite['scenarios']}
    labels=['fixed','v2',*VARIANTS] if stage=='validation' else ['fixed','webster','max_pressure','v2',read_json(OUTPUT/'selection.json')['chosen']]
    meta=read_json(OUTPUT/stage/'manifest.json'); expected=len(scenarios)*len(labels)
    if meta['status']!='completed' or meta['actual_new_runs']!=expected or meta['expected_runs']!=expected or meta['labels']!=labels:
        raise ValueError('Incomplete paired capacity wave')
    keys=set()
    for r in meta['records']:
        row=r['row']; key=row['scenario'],row['policy_label']; s=scenarios[key[0]]
        if key in keys or key[1] not in labels or original.audit(suite_path,s,cases[s['case']][0],key[1],OUTPUT/r['directory'])!=r:
            raise ValueError('Changed/duplicate capacity record')
        keys.add(key)
    if keys!={(s,l) for s in scenarios for l in labels}: raise ValueError('Missing comparison')
    return meta

def run_stage(stage,resume=False,max_wall_seconds=2700):
    if not 0<max_wall_seconds<float('inf'): raise ValueError('Invalid wall time')
    start=time.monotonic(); original.preflight(preserved=True)
    suite_path=INPUT/stage/'suite.json'; suite,cases=load_suite(suite_path)
    labels=['fixed','v2',*VARIANTS]; selection_hash=None
    if stage=='final':
        prior=verify_stage('validation'); selection=read_json(OUTPUT/'selection.json')
        if selection!=original.select(prior['records']) or selection['chosen'] not in VARIANTS:
            raise ValueError('No eligible validation-only capacity candidate')
        labels=['fixed','webster','max_pressure','v2',selection['chosen']]
        selection_hash=digest(OUTPUT/'selection.json')
    expected=len(suite['scenarios'])*len(labels)
    out=OUTPUT/stage; fingerprint=dict(proof_sha256=digest(INPUT/'proof.json'),suite_sha256=digest(suite_path),
        source_sha256=sources(),selection_sha256=selection_hash)
    if out.exists():
        if not resume: raise FileExistsError('Preserve existing output')
        meta=read_json(out/'manifest.json')
        if meta['fingerprint']!=fingerprint or meta['labels']!=labels or meta['status'] in ('failed','safety_hold'):
            raise ValueError('Changed resume or failure requires inspection')
    else:
        if resume: raise FileNotFoundError('No existing wave')
        out.mkdir(parents=True)
        meta=dict(status='started',labels=labels,fingerprint=fingerprint,expected_runs=expected,
            actual_new_runs=0,additional_training=0,records=[])
    common.atomic_json(out/'manifest.json',meta)
    try:
        indexed={s['id']:s for s in suite['scenarios']}; completed=set()
        for r in meta['records']:
            key=r['row']['scenario'],r['row']['policy_label']; s=indexed[key[0]]
            if key in completed or original.audit(suite_path,s,cases[s['case']][0],key[1],OUTPUT/r['directory'])!=r:
                raise ValueError('Resume result differs')
            completed.add(key)
        if len(completed)!=meta['actual_new_runs']: raise ValueError('Counter differs')
        for s in suite['scenarios']:
            for label in labels:
                if (s['id'],label) in completed: continue
                if time.monotonic()-start>=max_wall_seconds:
                    meta['status']='checkpoint'; common.atomic_json(out/'manifest.json',meta); return meta
                original.preflight()
                directory=out/s['id']/label
                meta.update(status='running',active_run=str(directory.relative_to(OUTPUT)))
                common.atomic_json(out/'manifest.json',meta)
                if not directory.exists(): original.replay(suite_path,s,label,directory)
                record=original.audit(suite_path,s,cases[s['case']][0],label,directory)
                meta['records'].append(record); meta['actual_new_runs']=len(meta['records']); meta.pop('active_run',None)
                common.atomic_json(out/'manifest.json',meta)
                print(f"{stage} {meta['actual_new_runs']}/{expected} {s['id']}/{label}: queue={record['row']['mean_queue_vehicles']:.3f} arrived={record['row']['arrived_vehicles']}/{s['scheduled']} braking={len(record['warning_details'])}",flush=True)
        meta['status']='completed'; common.atomic_json(out/'manifest.json',meta)
        verify_stage(stage); original.preflight(preserved=True)
        if stage=='validation':
            selected=original.select(meta['records']); path=OUTPUT/'selection.json'
            if path.exists() and read_json(path)!=selected: raise ValueError('Selection changed')
            common.atomic_json(path,selected); print(f"Selected: {selected['chosen']}",flush=True)
        else:
            summary=original.comparison(meta['records'],labels[-1]); common.atomic_json(OUTPUT/'final_summary.json',summary)
            print(f"Final target_met={summary['target_met']} changes={summary['overall']['queue_change']}",flush=True)
        return meta
    except BaseException as error:
        meta.update(status='interrupted' if isinstance(error,KeyboardInterrupt) else 'safety_hold' if isinstance(error,common.SafetyHold) else 'failed',
                    error=f'{type(error).__name__}: {error}')
        common.atomic_json(out/'manifest.json',meta); raise

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','run','verify'))
    parser.add_argument('--stage',choices=('validation','final'),default='validation')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--max-wall-seconds',type=float,default=2700)
    args=parser.parse_args()
    if args.action=='prepare':
        proof=prepare(); print(f"Prepared fresh24 + untouched24; preserved={len(proof['preserved_sha256'])}; no traffic")
    else:
        with context():
            meta=run_stage(args.stage,args.resume,args.max_wall_seconds) if args.action=='run' else verify_stage(args.stage)
            print(f"{args.stage}: {meta['status']} {meta['actual_new_runs']}/{meta['expected_runs']}")
