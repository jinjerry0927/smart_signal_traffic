"""Read-only microscopic context of a closed, reviewed-suffix teleport stop."""
import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import unseen_transfer_followup as followup
from tools.diagnose_unseen_transfer_stop import periods
from transferable_signal.spec import write_json


def diagnose(number):
    fp,jobs,attempts,parents=followup.ledger(number+1)
    failure=attempts[-1]
    assert failure['outcome']=='failed_teleport'
    path=followup.OUTPUT/failure['path']
    job=jobs[len(attempts)-1]
    scenario,name,(spec,network,_),routes=job
    warning=(path/'sumo-errors.log').read_text(encoding='utf-8')
    matches=re.findall(r"Teleporting vehicle '([^']+)'; waited too long \(([^)]+)\), lane='([^']+)', time=([0-9]+(?:\.[0-9]+)?)",warning)
    assert len(matches)==1
    vid,reason,source_lane,logtime=matches[0]
    frames=[json.loads(line) for line in (path/'frames.jsonl').open(encoding='utf-8')]
    before=frames[-2]
    stopped=[]
    for frame in reversed(frames[:-1]):
        if vid not in frame['vehicles'] or frame['vehicles'][vid][0]>=.1:
            break
        stopped.append(frame)
    stopped.reverse()
    assert stopped
    route_xml=ET.parse(routes).getroot()
    vehicle=route_xml.find("vehicle[@id='%s']"%vid)
    route=route_xml.find("route[@id='%s']"%vehicle.get('route')).get('edges').split()
    source_junction=next(j for j in spec['junctions'] if any(m['incoming']==source_lane for m in j['movements']))
    outgoing=next(m['outgoing'] for m in source_junction['movements'] if m['incoming']==source_lane and spec['lanes'][m['outgoing']]['edge']==route[1])
    source_phase=next(i for i,p in enumerate(source_junction['phases']) if source_lane in p['incoming'])
    source_green=[f for f in stopped if f['clocks'][source_junction['id']]['stage']=='green' and f['clocks'][source_junction['id']]['current']==source_phase]
    next_movements=[(j,m) for j in spec['junctions'] for m in j['movements']
                    if len(route)>2 and spec['lanes'][m['incoming']]['edge']==route[1] and spec['lanes'][m['outgoing']]['edge']==route[2]]
    downstream=None
    if next_movements:
        j,m=next_movements[0]
        phase=next(i for i,p in enumerate(j['phases']) if m['incoming'] in p['incoming'])
        downstream=dict(junction=j['id'],green=periods([f['time_s'] for f in stopped if f['clocks'][j['id']]['stage']=='green' and f['clocks'][j['id']]['current']==phase]),
                        continuous_from_actual_receiving_lane=any(m['incoming']==outgoing for j,m in next_movements),
                        next_incoming_options=[m['incoming'] for j,m in next_movements])
    occupancies=[f['lanes'][outgoing][1] for f in source_green]
    value=dict(completed=sum(a['outcome']=='completed' for a in attempts),failed=sum(a['outcome']!='completed' for a in attempts),
        unattempted=len(jobs)-len(attempts),failed_prefix=failure,reason=reason,source_lane=source_lane,sumo_warning_time=float(logtime),
        vehicle=dict(vehicle.attrib,route_edges=route),stopped_begin=stopped[0]['time_s'],stopped_end=stopped[-1]['time_s'],continuous_stopped_seconds=len(stopped),
        last_pre_event_speed_wait=before['vehicles'].get(vid),upstream_junction=source_junction['id'],upstream_green=periods([f['time_s'] for f in source_green]),
        receiving_lane=outgoing,receiving_lane_count_when_green=[min(occupancies),max(occupancies)] if occupancies else None,
        downstream=downstream,
        uncertainty='Exact microscopic gap/leader cause is not established without position/leader data. Do not tune the final inputs based on this result.')
    target=followup.build.BUNDLE/f'teleport-diagnosis-{number+1:02d}.json'
    if target.exists():
        raise FileExistsError('Preserve prior incident diagnosis')
    write_json(target,value)
    print(json.dumps({k:v for k,v in value.items() if k!='failed_prefix'},ensure_ascii=False,indent=2))
    print('PREFIX',failure['frames'],'frames',failure['actions'],'actions',failure['prefix_replay'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--round',type=int,required=True)
    diagnose(parser.parse_args().round)
