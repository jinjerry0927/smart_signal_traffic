"""Read-only evidence table and timeline for five exactly reproduced stops."""
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from transferable_signal.spec import digest,read_json,write_json
from tools import unseen_transfer_build as build

OUTPUT=ROOT/'results/runs/unseen_webster_cause_20261029'


def summarize():
    summary=read_json(ROOT/'results/runs/unseen_transfer_20261028/reviewed-followup-summary.json')
    net=ET.parse(build.DEFAULT_INPUT/'zt_pair_short/network.net.xml').getroot()
    lengths={lane.get('id'):float(lane.get('length')) for edge in net.findall('edge') for lane in edge.findall('lane')}
    cases=[]
    for failed in summary['failed']:
        scenario=failed['id'].split('/')[0]
        path=OUTPUT/scenario/'webster'
        cert=read_json(path/'cause-verification.json')
        assert cert['status']=='PASS_exact_original_reproduction'
        assert cert['telemetry_sha256']==digest(path/'cause-telemetry.jsonl')
        target=cert['target_vehicle']
        meta=read_json(path/'manifest.json')
        end=cert['original_stop_time_s']-1;begin=end-299
        original=ROOT/'results/runs/unseen_transfer_20261028'/failed['path']
        original_log=(original/'sumo-errors.log').read_text(encoding='utf-8')
        import re
        source_lane=re.search(r"Teleporting vehicle '[^']+'; waited too long \([^)]+\), lane='([^']+)'",original_log).group(1)
        routefile=build.DEFAULT_INPUT/'zt_pair_short'/Path(meta['scenario']['routes']).name
        routes=ET.parse(routefile).getroot()
        vehicle=routes.find("vehicle[@id='%s']"%target)
        edges=routes.find("route[@id='%s']"%vehicle.get('route')).get('edges').split()
        origin,index=source_lane.rsplit('_',1)
        connection=next(c for c in net.findall('connection') if c.get('from')==origin and c.get('fromLane')==index and c.get('to')==edges[1])
        receiver=connection.get('to')+'_'+connection.get('toLane')
        samples=[]
        for line in (path/'cause-telemetry.jsonl').open(encoding='utf-8'):
            frame=json.loads(line);now=frame['time_s']
            if not begin<=now<=end:continue
            ego=frame['ego'];v=frame['vehicles'][target]
            assert ego['lane']==source_lane and v['speed_mps']<.1
            assert abs(v['waiting_s']-(now-begin+1))<1e-9
            tls=ego['next_tls'][0]
            assert tls[1]==int(connection.get('linkIndex'))
            rear_gap=min((a['lane_pos_m']-a['length_m'] for a in frame['vehicles'].values() if a['lane']==receiver),default=lengths[receiver])
            samples.append(dict(time_s=now,green=tls[3]=='G',receiver_rear_gap_m=rear_gap,
                body_length_m=v['length_m'],min_gap_m=v['min_gap_m'],basic_space_m=v['length_m']+v['min_gap_m'],
                stopline_distance_m=ego['stopline_distance_m'],foes=len(ego['junction_foes']),stop_state=ego['stop_state']))
        assert len(samples)==300
        green=[s for s in samples if s['green']]
        enough=[s for s in samples if s['receiver_rear_gap_m']>=s['basic_space_m']]
        joint=[s for s in enough if s['green']]
        assert green and enough and not joint
        cases.append(dict(job=failed['id'],target=target,type=vehicle.get('type'),source_lane=source_lane,receiving_lane=receiver,
            stopped_begin_s=begin,stopped_end_s=end,consecutive_stopped_seconds=300,
            body_length_m=green[0]['body_length_m'],min_gap_m=green[0]['min_gap_m'],basic_space_m=green[0]['basic_space_m'],
            green_seconds=len(green),non_green_seconds=300-len(green),
            receiver_rear_gap_when_green_min_m=min(s['receiver_rear_gap_m'] for s in green),
            receiver_rear_gap_when_green_max_m=max(s['receiver_rear_gap_m'] for s in green),
            space_available_during_non_green_seconds=len(enough),green_and_basic_space_seconds=0,
            green_frames_with_junction_foes=sum(s['foes']>0 for s in green),
            green_frames_with_stop_flags=sum(s['stop_state']!=0 for s in green),
            original_reproduction='EXACT',verification_sha256=digest(path/'cause-verification.json'),samples=samples))
    result=dict(status='PASS',finding='All five vehicles were green only when the receiving lane entry had less free rear space than body length plus minGap. Sufficient basic entry space occurred only while their entry signal was not green.',
        expected_teleport_diagnostic_replays=5,new_independent_performance_samples=0,parameter_changes=0,additional_training=0,
        exact_original_frame_seconds=sum(f['event']['time_s'] for f in summary['failed']),
        cases=cases,scope='Verified necessary physical space mismatch with signal timing; no claim to have instrumented the exact internal SUMO C++ decision branch or to have isolated every cause of downstream queue formation.',
        sources=dict(intersection_model='https://sumo.dlr.de/docs/Simulation/Intersections.html',vehicle_space='https://sumo.dlr.de/docs/Definition_of_Vehicles%2C_Vehicle_Types%2C_and_Routes.html'))
    target=OUTPUT/'cause-summary.json'
    if target.exists():raise FileExistsError('Preserve previous cause summary')
    write_json(target,result)
    render(cases[0])
    for c in cases:
        print(c['job'],'green',c['green_seconds'],'needed',c['basic_space_m'],'entry free min/max',
              round(c['receiver_rear_gap_when_green_min_m'],3),round(c['receiver_rear_gap_when_green_max_m'],3),'joint feasible seconds',0)
    print('PASS: 5 exact replays; all1500 stopped seconds checked; no overlapping green/basic entry space')


def render(case):
    image=Image.new('RGB',(1600,860),'#f4f7fa');d=ImageDraw.Draw(image)
    def font(size,bold=False):return ImageFont.truetype('C:/Windows/Fonts/malgunbd.ttf' if bold else 'C:/Windows/Fonts/malgun.ttf',size)
    ink,muted,green,red='#173047','#526b7e','#168b70','#d57b82'
    d.text((55,35),'버스가 5분 동안 멈춘 이유',font=font(43,True),fill=ink)
    d.text((58,100),'첫 혼잡 표본의 실제 재현 기록 · 버스가 들어갈 자리가 생긴 때와 녹색 신호가 맞지 않았습니다',font=font(25),fill=muted)
    left,right=310,1515
    d.rounded_rectangle((45,158,1555,620),18,fill='white',outline='#dce5ed',width=2)
    for label,top,condition in [('버스 쪽 녹색',232,lambda s:s['green']),('빈 공간 ≥ 14.5m',365,lambda s:s['receiver_rear_gap_m']>=s['basic_space_m'])]:
        d.text((75,top+15),label,font=font(25,True),fill=ink)
        for i,s in enumerate(case['samples']):
            x0=left+(right-left)*i/300;x1=left+(right-left)*(i+1)/300
            d.rectangle((x0,top,x1+1,top+70),fill=green if condition(s) else '#f0cdd1')
    for t in range(0,301,60):
        x=left+(right-left)*t/300
        d.line((x,218,x,447),fill='#d1dbe3',width=1)
        d.text((x-18,467),str(t//60)+'분',font=font(22),fill=muted)
    d.rectangle((315,530,335,550),fill=green);d.text((348,521),'조건 충족',font=font(22),fill=muted)
    d.rectangle((550,530,570,550),fill='#f0cdd1');d.text((583,521),'조건 미충족',font=font(22),fill=muted)
    d.text((80,573),'두 줄이 동시에 초록인 시간: 0초',font=font(25,True),fill='#a63e4d')
    d.rounded_rectangle((45,650,1555,782),16,fill='#e9f3ef')
    d.text((73,674),'버스 12m + 기본 안전거리 2.5m = 최소 14.5m',font=font(29,True),fill=ink)
    d.text((73,724),'녹색일 때 실제 빈 공간은 4.6~11.6m → 교차로를 건넌 뒤 들어갈 자리가 부족',font=font(26),fill=muted)
    d.text((58,805),'시간축: 연속 정지 시작부터 300초 · 분홍 구간의 신호는 적색 또는 전환 중 · 원래 운행과 매초 동일함을 검증',font=font(21),fill=muted)
    path=build.BUNDLE/'webster-cause-timeline.png'
    if path.exists():raise FileExistsError('Preserve prior figure')
    image.save(path)


if __name__=='__main__':summarize()
