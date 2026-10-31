"""Independent presentation numeric audit and optional ZIP packaging; no traffic."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
PACK=ROOT/'presentation/unseen_update_20261029'

def sha(data):return hashlib.sha256(data).hexdigest()
def load(path):return json.loads(path.read_text(encoding='utf-8'))
def rows(name):
    with (PACK/'data'/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def near(a,b):assert math.isclose(float(a),float(b),rel_tol=1e-12,abs_tol=1e-12),(a,b)
def dump(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def verify():
    original=ROOT/'results/runs/unseen_transfer_20261028/reviewed-followup-comparison.csv'
    source=PACK/'data/08_all162_attempts_source.csv'
    assert original.read_bytes()==source.read_bytes()
    certificate=load(ROOT/'docs/unseen_transfer_20261028/report-verification.json')
    assert sha(source.read_bytes())==certificate['comparison_sha256']
    raw=rows('08_all162_attempts_source.csv');assert len(raw)==162
    scenarios={s['id']:s for s in load(ROOT/'network/unseen_transfer_20261028/suite.json')['scenarios']}
    methods=['fixed','webster','max_pressure','smart_v2','raw_smart','rules_pressure']
    jobs={r['job']:r for r in raw};assert len(jobs)==162
    assert set(jobs)=={sid+'/'+method for sid in scenarios for method in methods}
    assert sum(r['outcome']=='completed' for r in raw)==157
    failed=[r for r in raw if r['outcome']!='completed'];assert len(failed)==5
    assert all(r['job'].startswith('zt_pair_short_') and r['job'].endswith('/webster') and r['outcome']=='failed_teleport' for r in failed)
    for r in failed:assert all(r[c]=='' for c in ['mean_queue_vehicles','arrived','scheduled','remaining'])
    def get(sid,method):return jobs[sid+'/'+method]
    def qmean(sids,method):return statistics.mean(float(get(s,method)['mean_queue_vehicles']) for s in sids)
    def total(sids,method,column):return sum(int(get(s,method)[column]) for s in sids)
    sids=list(scenarios)
    truth=load(PACK/'data/metrics_for_presentation.json')
    for r in rows('01_overall_full27.csv'):
        method=r['controller'];assert int(r['n'])==27 and method!='webster'
        assert all(get(s,method)['outcome']=='completed' for s in sids)
        near(r['mean_queue'],qmean(sids,method))
        for c in ['arrived','scheduled','remaining']:assert int(r[c])==total(sids,method,c)
    full=rows('04_all27_fixed_smart_pairs.csv');assert len(full)==27 and {r['scenario'] for r in full}==set(sids)
    for r in full:
        sid=r['scenario'];assert r['case']==scenarios[sid]['case'] and r['profile']==scenarios[sid]['profile']
        near(r['fixed_mean_queue'],get(sid,'fixed')['mean_queue_vehicles']);near(r['smart_mean_queue'],get(sid,'smart_v2')['mean_queue_vehicles'])
        delta=100*(float(get(sid,'smart_v2')['mean_queue_vehicles'])/float(get(sid,'fixed')['mean_queue_vehicles'])-1)
        near(r['queue_change_pct'],delta);assert delta<0
    for name,expected_n,group in [('02_by_road_full9.csv',9,['case']),('03_by_road_demand_full3.csv',3,['case','profile'])]:
        grouped=rows(name);assert len(grouped)==27//expected_n
        for r in grouped:
            ss=[s for s in sids if all(scenarios[s][key]==r[key] for key in group)];assert len(ss)==expected_n==int(r['n'])
            smart=qmean(ss,'smart_v2');fixed=qmean(ss,'fixed')
            near(r['smart_mean_queue'],smart);near(r['fixed_mean_queue'],fixed);near(r['queue_change_pct'],100*(smart/fixed-1))
            near(r['queue_reduction_pct'],100*(1-smart/fixed))
            for method in ['fixed','smart']:
                assert int(r[method+'_arrived'])==total(ss,'smart_v2' if method=='smart' else method,'arrived')
    paired=rows('05_paired_baselines.csv');assert len(paired)==5
    for r in paired:
        base=r['baseline'];ss=[s for s in sids if get(s,base)['outcome']=='completed'];assert len(ss)==int(r['n'])==(22 if base=='webster' else 27)
        smart=qmean(ss,'smart_v2');baseline=qmean(ss,base)
        near(r['smart_mean_queue'],smart);near(r['baseline_mean_queue'],baseline);near(r['queue_change_pct'],100*(smart/baseline-1))
        assert int(r['smart_arrived'])==total(ss,'smart_v2','arrived') and int(r['baseline_arrived'])==total(ss,base,'arrived')
    coverage=rows('06_completion_counts.csv');assert len(coverage)==6
    for r in coverage:
        n=22 if r['controller']=='webster' else 27
        assert (int(r['completed']),int(r['failed']),int(r['unattempted']))==(n,27-n,0)
    assert truth['smart']['arrived']==8709 and truth['smart']['scheduled']==9037 and truth['smart']['remaining']==328
    near(truth['smart']['mean_queue'],qmean(sids,'smart_v2'));near(truth['fixed_queue_change_pct'],100*(qmean(sids,'smart_v2')/qmean(sids,'fixed')-1))
    assert truth['learned_contribution_target_met'] is False and truth['original_full_protocol_pass_claim'] is False
    assert round(next(r for r in truth['by_road'] if r['case']=='zt_pair_short')['queue_reduction_pct'],2)==17.13
    assert truth['source_comparison_sha256']==sha(source.read_bytes())
    docs=list(PACK.glob('*.md'))+list(PACK.glob('*.txt'))
    for p in docs:
        content=p.read_text(encoding='utf-8');assert '\ufffd' not in content
        for ref in re.findall(r'(?<![\w/])(assets/[\w]+\.(?:png|svg))',content):assert (PACK/ref).is_file(),(p,ref)
    outline=(PACK/'01_슬라이드구성_발표대본.md').read_text(encoding='utf-8')
    prompt=(PACK/'00_ChatGPT_제작요청.txt').read_text(encoding='utf-8')
    assert prompt.endswith(outline) and '총 10장' in prompt
    durations=[int(s) for s in re.findall(r'^## [1-8]\. .* — (\d+)초$',outline,re.M)]
    assert len(durations)==8 and sum(durations)==300
    assert len(re.findall(r'^## 부록 [AB]\.',outline,re.M))==2
    pngs=sorted((PACK/'assets').glob('*.png'));svgs=sorted((PACK/'assets').glob('*.svg'))
    assert len(pngs)==12 and len(svgs)==10
    images=[]
    for p in pngs:
        with Image.open(p) as im:
            w,h=im.size;assert w>=1200 and h>=700;im.verify()
        images.append(dict(file=p.name,width=w,height=h))
    for p in svgs:assert ET.parse(p).getroot().tag.endswith('svg')
    for filename in ['results.md','report-verification.json','web-trial.md','webster-cause-investigation.md']:
        assert (PACK/'evidence'/filename).read_bytes()==(ROOT/'docs/unseen_transfer_20261028'/filename).read_bytes()
    for case in {s['case'] for s in scenarios.values()}:
        assert (PACK/'evidence/network_specs'/(case+'.json')).read_bytes()==(ROOT/'network/unseen_transfer_20261028'/case/'intersection.json').read_bytes()
    assert (PACK/'assets/12_network_geometry_reference.png').read_bytes()==(ROOT/'docs/unseen_transfer_20261028/road-layout-preview.png').read_bytes()
    return dict(status='PASS',source_rows=162,completed=157,failed=5,full_pairs=27,webster_pairs=22,road_groups=3,demand_cells=9,
        main_slides=8,appendix_slides=2,seconds=300,png_count=12,svg_count=10,images=images,original_comparison_sha256=sha(source.read_bytes()),traffic_runs=0)


def verify_archive():
    manifest=load(PACK/'manifest.json');target=PACK.with_suffix('.zip')
    assert {p.relative_to(PACK).as_posix() for p in PACK.rglob('*') if p.is_file()}==set(manifest['files'])|{'manifest.json'}
    with zipfile.ZipFile(target) as z:
        assert z.testzip() is None
        assert len(z.namelist())==len(manifest['files'])+1
        for name,info in manifest['files'].items():
            current=(PACK/name).read_bytes();assert sha(current)==info['sha256'] and len(current)==info['bytes']
            assert z.read(PACK.name+'/'+name)==current
        assert z.read(PACK.name+'/manifest.json')==(PACK/'manifest.json').read_bytes()
    return dict(path=str(target),bytes=target.stat().st_size,sha256=sha(target.read_bytes()),files=len(manifest['files'])+1,all_decompressed_hashes_match=True)


def package():
    manifest=dict(version=1,created='2026-10-29',kind='PPT planning, factual visual assets and editable source data',files={})
    for p in sorted(PACK.rglob('*')):
        if p.is_file() and p.name!='manifest.json':
            b=p.read_bytes();manifest['files'][p.relative_to(PACK).as_posix()]={'bytes':len(b),'sha256':sha(b)}
    dump(PACK/'manifest.json',manifest)
    target=PACK.with_suffix('.zip')
    if target.exists():raise FileExistsError('Refusing to replace existing final ZIP: '+str(target))
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(PACK.rglob('*')):
            if p.is_file():z.write(p,PACK.name+'/'+p.relative_to(PACK).as_posix())
    return verify_archive()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--package',action='store_true');args=parser.parse_args()
    result=verify()
    if args.package:result['zip']=package()
    elif PACK.with_suffix('.zip').exists():result['zip']=verify_archive()
    report=ROOT/'.codex-artifacts/presentation-verification-20261029.json';dump(report,result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
