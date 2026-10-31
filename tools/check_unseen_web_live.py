"""Bounded HTTP/SUMO presentation checks, separate from the frozen benchmark."""
import argparse
import csv
import json
import math
from pathlib import Path
import sys
import time
from urllib import request
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from transferable_signal.spec import digest,read_json,write_json


def run(url,out):
    out.mkdir(parents=True,exist_ok=False)
    def api(path,payload=None):
        req=request.Request(url+path,data=None if payload is None else json.dumps(payload).encode(),
                            headers={} if payload is None else {'Content-Type':'application/json'})
        with request.urlopen(req,timeout=15) as response:return json.load(response)
    data=dict(status='running',purpose='web integration; not additional final comparisons',records=[])
    write_json(out/'checks.json',data)
    benchmark=ROOT/'results/runs/unseen_transfer_20261028'
    with (benchmark/'reviewed-followup-comparison.csv').open(encoding='utf-8-sig',newline='') as f:
        originals={r['job']:benchmark/r['path'] for r in csv.DictReader(f) if r['outcome']=='completed'}
    jobs=[('zt_t1_short','zt_t1_short_quiet_s00',900),('zt_x31_asymmetric','zt_x31_asymmetric_quiet_s00',900),
          ('zt_pair_short','zt_pair_short_quiet_s00',900),('gyeongju','gyeongju_quiet',60)]
    for case,scenario,horizon in jobs:
        try:
            boot=api('/api/select-network',dict(case=case))
            assert boot['selected_case']==case and scenario in boot['scenarios']
            state=api('/api/start',dict(scenario=scenario,controller='smart',mode='local',speed=60,horizon_s=horizon))
            started=time.monotonic()
            while state['status'] in ['starting','running','pausing','paused','stopping']:
                if time.monotonic()-started>180:raise TimeoutError('Web run did not finish in its bound')
                time.sleep(.2)
                state=api('/api/state')
                assert state['case']==case
            assert state['status']=='completed',state.get('error')
            path=Path(state['output'])
            while read_json(path/'manifest.json')['status']=='started':
                if time.monotonic()-started>180:raise TimeoutError('Web output was not finalized')
                time.sleep(.05)
            meta,result=read_json(path/'manifest.json'),read_json(path/'metrics.json')
            assert meta['status']=='completed' and meta['scenario']['id']==scenario and meta['controller']=='smart'
            assert result['collision_vehicle_events']==result['teleport_events']==0
            assert result['sample_seconds']==horizon and {j['id'] for j in state['junctions']}==set(boot['map']['signals'])
            record=dict(case=case,scenario=scenario,horizon_s=horizon,path=str(path.relative_to(ROOT)),
                        manifest_sha256=digest(path/'manifest.json'),metrics_sha256=digest(path/'metrics.json'),
                        arrived=result['arrived_vehicles'],mean_queue=result['mean_queue_vehicles'],model_sha256=meta['identity']['model_sha256'])
            if horizon==900:
                original=originals[scenario+'/smart_v2']
                expected=read_json(original/'metrics.json')
                for key,value in result.items():
                    assert key in expected,(key,'missing reference metric')
                    if isinstance(value,(int,float)):
                        assert math.isclose(value,expected[key],abs_tol=1e-6,rel_tol=1e-7),(key,value,expected[key])
                    else:assert value==expected[key],key
                def signals(folder):
                    with (folder/'signals.csv').open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
                assert signals(path)==signals(original),'Live web signals differ from the frozen run'
                with (path/'decisions.csv').open(encoding='utf-8',newline='') as f:actual=list(csv.DictReader(f))
                expected_choices=[(int(r['time_s']),jid,c['action']) for r in
                    [json.loads(l) for l in (original/'decisions.jsonl').open(encoding='utf-8')] for jid,c in r['junctions'].items()]
                assert [(int(float(r['time_s'])),r['junction'],int(r['action'])) for r in actual]==expected_choices
                assert all(r['inference']=='local' for r in actual)
                def trips(folder):
                    return {t.get('id'):t.attrib for t in ET.parse(folder/'tripinfo.xml').getroot().findall('tripinfo')}
                assert trips(path)==trips(original),'Trip-by-trip results differ'
                record.update(reference=str(original.relative_to(ROOT)),all_core_metrics='MATCH',all_signal_boundaries='MATCH',
                              all_controller_actions='MATCH',all_trip_records='MATCH',actions=len(actual))
            log=(path/'sumo-errors.log').read_text(encoding='utf-8')
            assert 'Error:' not in log and 'Teleporting' not in log
            record['warnings']=[line for line in log.splitlines() if 'Warning:' in line]
            data['records'].append(record)
            write_json(out/'checks.json',data)
            print('WEB PASS',case,horizon,'seconds',result['arrived_vehicles'],'arrived',flush=True)
        except BaseException as exc:
            data.update(status='failed',failed_scenario=scenario,error=f'{type(exc).__name__}: {exc}')
            write_json(out/'checks.json',data)
            raise
    data.update(status='PASS',traffic_runs=4,additional_training=0,formal_benchmark_reruns=0,
                source_sha256={str(p.relative_to(ROOT)):digest(p) for p in [Path(__file__),ROOT/'signal_demo/unseen.py',ROOT/'signal_demo/runtime.py',ROOT/'signal_demo/web.py',ROOT/'signal_demo/unseen_dashboard.html']})
    write_json(out/'checks.json',data)
    print('PASS: 3 new-network full web runs matched frozen metrics/signals/actions/trips; original Gyeongju60s regression complete',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8001')
    parser.add_argument('--out',type=Path,default=ROOT/'results/runs/unseen_web_checks_20261029')
    args=parser.parse_args()
    run(args.url,args.out)
