"""Render the actual frozen SUMO lane polylines for human inspection."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / 'network/unseen_transfer_20261028'
OUTPUT = ROOT / 'docs/unseen_transfer_20261028/road-layout-preview.png'


def render():
    if OUTPUT.exists():
        raise FileExistsError('Preserve previous diagram')
    im = Image.new('RGB', (1920, 1120), '#f3f6fa')
    d = ImageDraw.Draw(im)
    regular = 'C:/Windows/Fonts/malgun.ttf'
    bold = 'C:/Windows/Fonts/malgunbd.ttf'
    font = lambda size, strong=False: ImageFont.truetype(bold if strong else regular, size)
    d.text((60, 30), '처음 보는 교차로 시험 · 새 도로망 3종', font=font(44, True), fill='#15273e')
    d.text((62, 96), '동결한 공통 모델과 규칙을 지원 범위 안의 새 기하에 적용 · 추가 학습 0회', font=font(25), fill='#40546d')
    cases = [('zt_t1_short', 'A  짧은 접근로 삼거리', '신호 1개 · 녹색 후보 3개 · 편도 1차로'),
             ('zt_x31_asymmetric', 'B  3:1 비대칭 사거리', '신호 1개 · 남북 3차로 / 동서 1차로'),
             ('zt_pair_short', 'C  짧은 연결의 2교차로', '신호 2개 · 연결 편도 2차로')]
    colors = {1:'#586b83', 2:'#147d91', 3:'#ba7130'}
    for index, (case, title, subtitle) in enumerate(cases):
        left, top, right, bottom = 45+index*625, 165, 45+index*625+600, 990
        d.rounded_rectangle((left,top,right,bottom), 20, fill='white', outline='#d9e1eb', width=2)
        d.text((left+22, top+22), title, font=font(30, True), fill='#15273e')
        d.text((left+22, top+72), subtitle, font=font(21), fill='#40546d')
        net = ET.parse(INPUT / case / 'network.net.xml').getroot()
        edges = [e for e in net.findall('edge') if e.get('function') != 'internal']
        all_points = [tuple(map(float, p.split(','))) for e in edges for lane in e.findall('lane') for p in lane.get('shape').split()]
        xs, ys = zip(*all_points)
        box = (left+47,top+145,right-47,bottom-130)
        scale = min((box[2]-box[0])/(max(xs)-min(xs)), (box[3]-box[1])/(max(ys)-min(ys)))
        cx,cy = (max(xs)+min(xs))/2, (max(ys)+min(ys))/2
        def project(x,y):
            return ((box[0]+box[2])/2+(x-cx)*scale, (box[1]+box[3])/2-(y-cy)*scale)
        for edge in edges:
            lanes = edge.findall('lane')
            for lane in lanes:
                points = [project(*map(float,p.split(','))) for p in lane.get('shape').split()]
                d.line(points, fill=colors[len(lanes)], width=max(2,round(2.7*scale)))
        for junction in net.findall('junction'):
            if junction.get('type') != 'traffic_light':
                continue
            x,y = project(float(junction.get('x')),float(junction.get('y')))
            d.ellipse((x-7,y-7,x+7,y+7), fill='#b52e61', outline='white', width=2)
            label = junction.get('id')
            d.text((x+15,y-15), label, font=font(19, True), fill='#8a1f46', stroke_width=2, stroke_fill='white')
        d.text((right-65,top+120), 'N ↑', font=font(20, True), fill='#40546d')
        spec = json.loads((INPUT/case/'intersection.json').read_text(encoding='utf-8'))
        incoming = {m['incoming'] for j in spec['junctions'] for m in j['movements']}
        shortest = min(spec['lanes'][lid]['length_m'] for lid in incoming)
        d.text((left+24,bottom-90), f'실제 최단 접근 차로: {shortest:.1f}m', font=font(21), fill='#40546d')
        if index == 2:
            d.text((left+24,bottom-53), '신호 중심 간격 160m · 연결 차로 129.6m', font=font(20), fill='#40546d')
        else:
            d.text((left+24,bottom-53), '차로 길이·구성이 쇼케이스와 다름', font=font(20), fill='#40546d')
    x = 62
    for lanes, color in colors.items():
        d.line([(x,1030),(x+38,1030)], fill=color, width=7)
        d.text((x+50,1012), f'편도 {lanes}차로', font=font(22), fill='#40546d')
        x += 220
    d.text((x+10,1012), '● 신호 위치', font=font(22), fill='#b52e61')
    d.text((62,1063), '실제 SUMO 차로 좌표로 작성 · 도면별 축척 상이 · 내부 회전 차로 생략 · 가상 수요 시험이며 현장 성능 검증과 구분', font=font(22), fill='#566a82')
    im.save(OUTPUT)
    print('WROTE', OUTPUT)


if __name__ == '__main__':
    render()
