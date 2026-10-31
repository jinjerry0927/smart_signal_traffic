"""Verified slide-ready evidence and editable chart data; no simulation or training."""
import csv
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.codex-artifacts/presentation-python-20261029'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager,ticker
import numpy as np

OUT=ROOT/'presentation/unseen_update_20261029'
ASSETS=OUT/'assets'
DATA=OUT/'data'
CASE_NAMES={'zt_t1_short':'삼거리','zt_x31_asymmetric':'비대칭 사거리','zt_pair_short':'연결 2교차로'}
PROFILES={'quiet':'한산','busy':'혼잡','directional':'방향 편중'}
NAMES={'fixed':'Fixed','webster':'Webster','max_pressure':'Max-Pressure','smart_v2':'SmartSignal V2','raw_smart':'학습 모델 단독','rules_pressure':'규칙 중심 진단'}
TEAL,GRAY,INK,RED='#087f76','#abb5be','#193247','#bd4c58'


def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def write(p,value):Path(p).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def csv_file(name,rows):
    with (DATA/name).open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


def prepare_data():
    source=ROOT/'results/runs/unseen_transfer_20261028/reviewed-followup-comparison.csv'
    summary_path=source.with_name('reviewed-followup-summary.json')
    cert=read(ROOT/'docs/unseen_transfer_20261028/report-verification.json')
    assert digest(source)==cert['comparison_sha256'] and digest(summary_path)==cert['summary_sha256']
    summary=read(summary_path)
    suite=read(ROOT/'network/unseen_transfer_20261028/suite.json')
    scenarios={s['id']:s for s in suite['scenarios']}
    with source.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    assert len(rows)==162 and len({r['job'] for r in rows})==162
    assert sum(r['outcome']=='completed' for r in rows)==157
    assert sum(r['outcome']=='failed_teleport' for r in rows)==5
    completed={}
    for r in rows:
        sid,controller=r['job'].split('/')
        if r['outcome']=='completed':
            completed[(sid,controller)]=dict(mean_queue=float(r['mean_queue_vehicles']),arrived=int(r['arrived']),
                scheduled=int(r['scheduled']),remaining=int(r['remaining']))
        else:assert all(not r[k] for k in ['mean_queue_vehicles','arrived','scheduled','remaining'])
    pairs=[]
    for sid,s in scenarios.items():
        a,b=completed[(sid,'smart_v2')],completed[(sid,'fixed')]
        assert a['scheduled']==b['scheduled'] and b['mean_queue']>0
        pairs.append(dict(scenario=sid,case=s['case'],road=CASE_NAMES[s['case']],profile=s['profile'],demand=PROFILES[s['profile']],
            sample=int(sid.rsplit('s',1)[1])+1,n=1,fixed_mean_queue=b['mean_queue'],smart_mean_queue=a['mean_queue'],
            queue_change_pct=100*(a['mean_queue']/b['mean_queue']-1),fixed_arrived=b['arrived'],smart_arrived=a['arrived'],
            scheduled=a['scheduled'],smart_remaining=a['remaining']))
    assert len(pairs)==27 and all(r['queue_change_pct']<0 for r in pairs)
    by_road=[];by_cell=[]
    for case,label in CASE_NAMES.items():
        selected=[r for r in pairs if r['case']==case];assert len(selected)==9
        def aggregate(rs):
            fixed=statistics.mean(r['fixed_mean_queue'] for r in rs);smart=statistics.mean(r['smart_mean_queue'] for r in rs)
            return dict(n=len(rs),fixed_mean_queue=fixed,smart_mean_queue=smart,queue_change_pct=100*(smart/fixed-1),
                queue_reduction_pct=100*(1-smart/fixed),fixed_arrived=sum(r['fixed_arrived'] for r in rs),
                smart_arrived=sum(r['smart_arrived'] for r in rs),scheduled=sum(r['scheduled'] for r in rs))
        by_road.append(dict(case=case,road=label,**aggregate(selected)))
        for profile,demand in PROFILES.items():
            rs=[r for r in selected if r['profile']==profile];assert len(rs)==3
            by_cell.append(dict(case=case,road=label,profile=profile,demand=demand,**aggregate(rs)))
    overall=[]
    for name in ['fixed','max_pressure','smart_v2','raw_smart','rules_pressure']:
        selected=[v for (sid,n),v in completed.items() if n==name];assert len(selected)==27
        overall.append(dict(controller=name,label=NAMES[name],n=27,mean_queue=statistics.mean(r['mean_queue'] for r in selected),
            arrived=sum(r['arrived'] for r in selected),scheduled=sum(r['scheduled'] for r in selected),remaining=sum(r['remaining'] for r in selected)))
    paired=[]
    for name,p in summary['paired_smart_comparisons'].items():
        keys=[sid for sid in scenarios if (sid,name) in completed and (sid,'smart_v2') in completed]
        qa=statistics.mean(completed[(sid,'smart_v2')]['mean_queue'] for sid in keys)
        qb=statistics.mean(completed[(sid,name)]['mean_queue'] for sid in keys)
        assert len(keys)==p['paired_conditions'] and abs((qa/qb-1)-p['smart_queue_change'])<1e-12
        paired.append(dict(baseline=name,label=NAMES[name],n=len(keys),smart_mean_queue=qa,baseline_mean_queue=qb,
            queue_change_pct=100*(qa/qb-1),smart_arrived=sum(completed[(s,'smart_v2')]['arrived'] for s in keys),
            baseline_arrived=sum(completed[(s,name)]['arrived'] for s in keys)))
    coverage=[dict(controller=n,label=NAMES[n],completed=c['completed'],failed=c['failed'],unattempted=27-c['completed']-c['failed']) for n,c in summary['controller_coverage'].items()]
    csv_file('01_overall_full27.csv',overall);csv_file('02_by_road_full9.csv',by_road);csv_file('03_by_road_demand_full3.csv',by_cell)
    csv_file('04_all27_fixed_smart_pairs.csv',pairs);csv_file('05_paired_baselines.csv',paired);csv_file('06_completion_counts.csv',coverage)
    csv_file('07_experiment_matrix.csv',[dict(road=label,quiet_samples=3,busy_samples=3,directional_samples=3,total_conditions=9) for label in CASE_NAMES.values()])
    shutil.copyfile(source,DATA/'08_all162_attempts_source.csv')
    smart=next(r for r in overall if r['controller']=='smart_v2');fixed=next(r for r in overall if r['controller']=='fixed')
    pressure=next(r for r in overall if r['controller']=='max_pressure')
    truth=dict(title='동결한 SmartSignal V2의 새 도로망 적용',data_date='2026-10-29',
        scope='지원된 T/X·1~3차로 범위의 새 가상 기하3종; 새 교차로 유형 전체나 현장 검증이 아님',
        roads=3,conditions=27,profiles=3,departure_samples_per_profile=3,controllers=6,attempts=162,completed_runs=157,failed_runs=5,
        smart_conditions_completed=27,smart_collision_events=0,smart_teleport_events=0,additional_training=0,policy_changes=0,
        smart=smart,fixed=fixed,max_pressure=pressure,by_road=by_road,by_road_demand=by_cell,paired_baselines=paired,coverage=coverage,
        fixed_queue_change_pct=100*(smart['mean_queue']/fixed['mean_queue']-1),
        fixed_arrival_increase=smart['arrived']-fixed['arrived'],fixed_arrival_change_pct=100*(smart['arrived']/fixed['arrived']-1),
        max_pressure_queue_change_pct=100*(smart['mean_queue']/pressure['mean_queue']-1),
        max_pressure_arrival_increase=smart['arrived']-pressure['arrived'],all27_queue_improved_vs_fixed=True,
        learned_contribution_target_met=False,original_full_protocol_pass_claim=False,
        source_comparison_sha256=digest(source),source_summary_sha256=digest(summary_path),
        model_sha256=summary['fingerprint']['model_sha256'],policy_sha256=summary['fingerprint']['policy_sha256'])
    assert (smart['arrived'],smart['scheduled'],smart['remaining'])==(8709,9037,328)
    assert round(truth['fixed_queue_change_pct'],2)==-19.03
    write(DATA/'metrics_for_presentation.json',truth)
    return truth,pairs


def style():
    font_manager.fontManager.addfont('C:/Windows/Fonts/malgun.ttf')
    font_manager.fontManager.addfont('C:/Windows/Fonts/malgunbd.ttf')
    plt.rcParams.update({'font.family':'Malgun Gothic','font.size':16,'axes.titlesize':22,'axes.labelsize':17,
        'xtick.labelsize':15,'ytick.labelsize':16,'legend.fontsize':15,'axes.unicode_minus':False,
        'svg.fonttype':'none','axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#c5ced5',
        'axes.labelcolor':INK,'text.color':INK,'xtick.color':INK,'ytick.color':INK,'figure.facecolor':'white','axes.facecolor':'white'})


def finish(fig,name,foot):
    fig.text(.06,.025,foot,ha='left',va='bottom',fontsize=12,color='#586e7c')
    fig.savefig(ASSETS/(name+'.png'),dpi=220,facecolor='white')
    fig.savefig(ASSETS/(name+'.svg'),facecolor='white')
    plt.close(fig)


def charts(t,pairs):
    style();smart,fixed=t['smart'],t['fixed']
    fig,ax=plt.subplots(figsize=(11.6,6.2));fig.subplots_adjust(left=.12,right=.96,top=.83,bottom=.21)
    bars=ax.bar(['Fixed','SmartSignal V2'],[fixed['mean_queue'],smart['mean_queue']],color=[GRAY,TEAL],width=.5)
    ax.set_ylim(0,48);ax.set_ylabel('평균 대기행렬 (대)');ax.set_axisbelow(True);ax.yaxis.grid(True,color='#e7edf1')
    ax.bar_label(bars,fmt='%.2f',padding=10,fontsize=23,fontweight='bold')
    fig.text(.12,.91,'고정 신호 대비 19.03% 감소',fontsize=27,fontweight='bold',color=TEAL)
    finish(fig,'04_overall_queue','동일한 새 27조건의 900초 평균. 추가 학습 0회. 평균 대기행렬은 대기시간과 다릅니다.')

    fig,ax=plt.subplots(figsize=(11.6,6.2));fig.subplots_adjust(left=.14,right=.96,top=.83,bottom=.21)
    bars=ax.bar(['Fixed','SmartSignal V2'],[fixed['arrived'],smart['arrived']],color=[GRAY,TEAL],width=.5)
    ax.set_ylim(0,10300);ax.set_ylabel('900초 안에 도착한 차량 합계 (대)');ax.yaxis.set_major_formatter(ticker.StrMethodFormatter('{x:,.0f}'))
    ax.set_axisbelow(True);ax.yaxis.grid(True,color='#e7edf1')
    ax.bar_label(bars,labels=[f"{fixed['arrived']:,}",f"{smart['arrived']:,}"],padding=10,fontsize=23,fontweight='bold')
    fig.text(.14,.91,'같은 차량 일정에서 353대 더 도착',fontsize=26,fontweight='bold',color=TEAL)
    finish(fig,'05_arrived_vehicles','총 예정 차량 9,037대. SmartSignal 도착 8,709대, 900초 종료 시 잔류·미진입 합계 328대.')

    data=t['by_road'];x=np.arange(3);w=.32
    fig,ax=plt.subplots(figsize=(12.8,6.7));fig.subplots_adjust(left=.1,right=.98,top=.87,bottom=.24)
    a=ax.bar(x-w/2,[r['fixed_mean_queue'] for r in data],w,color=GRAY,label='Fixed')
    b=ax.bar(x+w/2,[r['smart_mean_queue'] for r in data],w,color=TEAL,label='SmartSignal V2')
    ax.set_xticks(x,[r['road']+'\n9조건' for r in data]);ax.set_ylabel('평균 대기행렬 (대)');ax.set_ylim(0,102)
    ax.bar_label(a,fmt='%.2f',padding=3,fontsize=14);ax.bar_label(b,fmt='%.2f',padding=3,fontsize=14)
    for i,r in enumerate(data):ax.text(i,max(r['fixed_mean_queue'],r['smart_mean_queue'])+12,f"{r['queue_reduction_pct']:.2f}% 감소",ha='center',fontsize=20,fontweight='bold',color=TEAL)
    ax.legend(loc='upper left',frameon=False);ax.set_axisbelow(True);ax.yaxis.grid(True,color='#e7edf1')
    finish(fig,'06_by_geometry','도로망마다 한산·혼잡·방향 편중 × 출발 일정 3개 = 9조건 전체. 각 도로망 안에서 같은 조건끼리 비교했습니다.')

    matrix=np.asarray([[next(r['queue_reduction_pct'] for r in t['by_road_demand'] if r['case']==case and r['profile']==profile) for profile in PROFILES] for case in CASE_NAMES])
    fig,ax=plt.subplots(figsize=(11.8,6.7));fig.subplots_adjust(left=.25,right=.87,top=.88,bottom=.23)
    im=ax.imshow(matrix,cmap='Greens',vmin=0,vmax=65,aspect='auto')
    ax.set_xticks(range(3),list(PROFILES.values()));ax.set_yticks(range(3),list(CASE_NAMES.values()))
    ax.tick_params(length=0,pad=14)
    for i in range(3):
        for j in range(3):ax.text(j,i,f'{matrix[i,j]:.2f}%',ha='center',va='center',fontsize=25,fontweight='bold',color='white' if matrix[i,j]>35 else INK)
    for spine in ax.spines.values():spine.set_visible(False)
    cbar=fig.colorbar(im,ax=ax,fraction=.045,pad=.06);cbar.set_label('Fixed 대비 대기행렬 감소율 (%)',fontsize=14)
    fig.text(.25,.95,'9개 도로망·수요 조합에서 모두 개선',fontsize=23,fontweight='bold')
    finish(fig,'07_demand_heatmap','각 칸은 출발 일정 3개의 평균 대기행렬로 계산한 감소율입니다. 진한 초록일수록 감소 폭이 큽니다.')

    rows=[r for r in t['paired_baselines'] if r['baseline'] in ['fixed','webster','max_pressure']]
    fig,ax=plt.subplots(figsize=(12.8,6.8));fig.subplots_adjust(left=.29,right=.94,top=.88,bottom=.26)
    v=[r['queue_change_pct'] for r in rows];y=np.arange(3)
    ax.barh(y,v,color=[TEAL if value<0 else RED for value in v],height=.5)
    ax.set_yticks(y,[r['label']+f" ({r['n']}짝)" for r in rows]);ax.invert_yaxis();ax.axvline(0,color='#718493',lw=1.2)
    ax.set_xlim(-27,9);ax.set_xlabel('SmartSignal 평균 대기행렬 변화율 (%)')
    ax.set_xticks([-25,-20,-15,-10,-5,0,5]);ax.xaxis.grid(True,color='#e7edf1');ax.set_axisbelow(True)
    for i,value in enumerate(v):ax.text(value-.7 if value<0 else value+.7,i,f'{value:+.2f}%',ha='right' if value<0 else 'left',va='center',fontsize=22,fontweight='bold',color=TEAL if value<0 else RED)
    fig.text(.29,.95,'감소는 개선, 증가는 악화',fontsize=23,fontweight='bold')
    finish(fig,'08_paired_baselines','각 행은 동일 조건의 완료 짝만 비교합니다. Webster는 실패 5조건을 제외한 22짝이며, 다른 두 행은 27짝입니다.')

    rows=[r for r in t['coverage'] if r['controller'] in ['fixed','webster','max_pressure','smart_v2']]
    fig,ax=plt.subplots(figsize=(12.8,6.6));fig.subplots_adjust(left=.24,right=.94,top=.83,bottom=.24)
    y=np.arange(4);ok=[r['completed'] for r in rows];bad=[r['failed'] for r in rows]
    ax.barh(y,ok,height=.5,color=[TEAL if r['controller']=='smart_v2' else GRAY for r in rows],label='900초 정상 종료')
    ax.barh(y,bad,left=ok,height=.5,color=RED,label='정체로 중단')
    ax.set_yticks(y,[r['label'] for r in rows]);ax.invert_yaxis();ax.set_xlim(0,31);ax.set_xticks([0,9,18,27]);ax.set_xlabel('조건 수')
    for i,r in enumerate(rows):ax.text(27.6,i,f"{r['completed']}/27",va='center',fontsize=18,fontweight='bold')
    ax.legend(loc='lower left',bbox_to_anchor=(-.01,1.02),ncol=2,frameon=False)
    finish(fig,'09_completion','정상 종료는 모든 차량의 도착을 뜻하지 않습니다. 전체 비교는 기본 4제어와 진단 2제어, 총 162시도입니다.')

    fig,ax=plt.subplots(figsize=(12.8,6.5));fig.subplots_adjust(left=.24,right=.96,top=.88,bottom=.27)
    marks={'quiet':'o','busy':'s','directional':'^'};offsets={'quiet':-.16,'busy':0,'directional':.16}
    for i,(case,label) in enumerate(CASE_NAMES.items()):
        for profile in PROFILES:
            rs=[r for r in pairs if r['case']==case and r['profile']==profile]
            ax.scatter([r['queue_change_pct'] for r in rs],[i+offsets[profile]+(r['sample']-2)*.025 for r in rs],
                s=100,marker=marks[profile],color=TEAL,alpha=.8,label=PROFILES[profile] if i==0 else None)
    ax.axvline(0,color=RED,lw=1.5);ax.set_yticks(range(3),list(CASE_NAMES.values()));ax.invert_yaxis();ax.set_xlim(-72,5)
    ax.set_xlabel('각 조건의 Fixed 대비 평균 대기행렬 변화율 (%)');ax.xaxis.grid(True,color='#e7edf1');ax.set_axisbelow(True)
    ax.legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=3,frameon=False)
    finish(fig,'11_all27_conditions','27개 점 모두 0% 왼쪽입니다. 각 점은 하나의 도로망·수요·출발 일정이며, 서로 다른 학습 초기값 반복은 아닙니다.')


def handoff():
    intro='''첨부한 unseen_update_20261029.zip 자료로 교수님께 약 5분간 발표할 한국어 PPT를 제작해 주세요.
주제는 졸업작품 SmartSignal V2의 ‘기존 모델과 규칙을 고정한 새 도로망 적용 시험’입니다.
기획안만 답하지 말고, 자료를 읽고 바로 사용할 수 있는 PPTX와 PDF, 발표자 노트를 만들어 주세요.
본문 8장(표지 포함) + 질문 대응용 부록 2장, 총 10장으로 구성해 주세요. 아래 구성과 문구를 기준으로 표현을 다듬되 실험의 범위·숫자·단위·한계는 유지해 주세요.

[먼저 읽을 첨부 자료]
- 01_슬라이드구성_발표대본.md: 슬라이드별 문구·대본·시각자료·필수 주석
- data/metrics_for_presentation.json: 검증된 정밀 수치와 정의
- 02_교수님_질문대응.md / 03_수치정의_주의사항.md / 04_시각자료_목록.md
- assets/: 실제 가상 도로망 웹 지도 PNG/SVG, 실제 웹 대기 화면, 수치 그래프 PNG/SVG
- data/*.csv: PowerPoint 기본 차트와 표를 다시 만들 수 있는 편집용 원수치

[디자인과 제작 조건]
- 16:9, 흰 배경, 진한 남색(#193247) 글자, 청록(#087f76) V2, 회색(#abb5be) Fixed. 악화 지표는 붉은색(#bd4c58).
- 교수님 앞에서 읽히는 제목 30~34pt, 본문 22~24pt, 표·축 18pt 안팎, 각주 14pt 이상. 공간이 부족하면 글자를 과하게 줄이지 말고 내용을 덜어 주세요.
- 차트·표·문구는 가능한 한 편집 가능한 PowerPoint 요소로 만들어 주세요. CSV에서 값을 가져오고 원래 PNG는 모양과 값 확인용으로 쓰세요. 편집 차트를 만들 수 없는 경우 제공된 고해상도 PNG/SVG를 사용해 주세요.
- 장마다 하나의 결론을 보여 주세요. 로봇·자동차 스톡 사진, 장식 아이콘, 과도한 카드·그라데이션·애니메이션은 넣지 마세요.
- 새 교차로 이미지는 제공한 실제 웹 지도만 사용하고 ‘SUMO 가상 시험망’이라고 캡션을 붙여 주세요. 현장 도로 사진이나 생성한 교차로 이미지를 추가하지 마세요. 지도 3종은 축척이 다르므로 도로 길이는 캡션 수치로 설명하세요.
- 성능 막대의 축은 0에서 시작하세요. 퍼센트 변화율은 0 기준선을 표시하세요. n=27 / 도로망별 n=9 / 수요별 n=3 / Webster n=22의 범위를 보존하세요.
- 그래프 PNG에 제목이 이미 있으면 슬라이드 본문과 같은 제목을 두 번 크게 반복하지 마세요. 원본을 쓸 때 필수 축·단위·조건 수와 22짝 주석은 자르지 마세요.
- 제공하지 않은 학교명·팀명·수상·실측 결과·통계적 유의성·비용절감 효과를 만들지 마세요. 발표자 이름은 임의로 넣지 않아도 됩니다.
- 각 슬라이드의 발표자 노트에 아래 대본을 넣고 전체 약 5분으로 조절해 주세요. 부록은 질문 때만 보여 주는 자료입니다.
- 최종 슬라이드를 렌더링해 한글 깨짐·글자 겹침·표/차트 잘림을 확인한 뒤 파일을 제공해 주세요. 파일을 만들 수 없는 환경이면 만들었다고 하지 말고 완성된 슬라이드별 원고와 배치 명세를 제공해 주세요.

[주장 범위를 반드시 지킬 것]
1. SmartSignal V2는 학습 모델 + V2 규칙을 함께 고정한 시스템입니다. 이번 추가 학습과 규칙 보정은 0회입니다. ‘순수 AI 학습의 효과 입증’으로 표현하면 안 됩니다.
2. 새 도로망 3종, 제어 교차로는 합계 4개, 교통 조건은 27개입니다. T/X·1~3차로 등 기존 지원 범위 안의 새 기하 조합이며, 전혀 새로운 교차로 유형 전체를 시험한 것이 아닙니다.
3. 주 결과는 동일한 전체 27조건의 Fixed 비교입니다: 평균 대기행렬 39.859 → 32.273대(19.03% 감소), 도착 합계 8,356 → 8,709대(+353대). 대기시간 감소율로 바꾸지 마세요.
4. 세 도로망의 각 9조건 전체 감소율은 43.85%, 19.38%, 17.13%입니다. 연결망에 31.26%를 쓰면 표본 범위가 달라져 잘못됩니다.
5. Webster 5회는 시도 후 정체로 중단한 실패입니다. 미시도가 아닙니다. 전체 162시도 중 157정상 종료, 5실패, 미시도0. Webster 비교는 같은 완료22조건끼리만 하며 V2 17.519대 vs Webster21.919대로 20.07% 감소입니다. V2 전체27조건 평균과 Webster22조건 평균을 직접 비교하지 마세요.
6. Max-Pressure 전체27조건 평균31.483대보다 V2의 대기행렬은 2.51% 증가했습니다. 도착은 8,387 → 8,709대로 322대 늘었지만 이것으로 종합 우위라고 판정하지 마세요.
7. 학습 점수 대신 압력 점수를 쓴 규칙 중심 진단 대비 V2의 대기행렬은 2.61% 증가, 도착은321대 증가했습니다. 사전 학습 기여 기준은 미달입니다. 이 한계를 본문7에 짧게, 부록B에 구체적으로 남겨 주세요.
8. V2의 27/27 정상 종료는 차량100% 도착이 아닙니다. 예정9,037대 중 도착8,709대/미완료328대. 원래 전체 실험 프로토콜 통과나 임의의 모든 교차로·현장 적용 성능을 주장하지 마세요.
9. 웹 이미지는 실행 전0초의 대기 화면이며 기본 시연 설정이300초입니다. 성능 그래프는 별도900초 실험의 CSV에서 계산했습니다. 화면0대·0/300초를 성능 결과로 해석하지 마세요.
10. 정체 원인과 AI 취약성의 추가 검토는 후속 과제입니다. 시험한27조건에서 V2 충돌·teleport0을 확인했지만 앞으로 정체가 절대 없다고 말하면 안 됩니다. 이번5분 발표에서는 해당 진단을 중심 주제로 확대하지 마세요.

[이하 슬라이드별 원고·배치·대본]

'''
    outline=(OUT/'01_슬라이드구성_발표대본.md').read_text(encoding='utf-8')
    (OUT/'00_ChatGPT_제작요청.txt').write_text(intro+outline,encoding='utf-8')
    evidence=OUT/'evidence';evidence.mkdir(exist_ok=True)
    for filename in ['results.md','report-verification.json','web-trial.md','webster-cause-investigation.md']:
        shutil.copyfile(ROOT/'docs/unseen_transfer_20261028'/filename,evidence/filename)
    specs=evidence/'network_specs';specs.mkdir(exist_ok=True)
    for case in CASE_NAMES:
        shutil.copyfile(ROOT/'network/unseen_transfer_20261028'/case/'intersection.json',specs/(case+'.json'))
    shutil.copyfile(ROOT/'docs/unseen_transfer_20261028/road-layout-preview.png',ASSETS/'12_network_geometry_reference.png')


if __name__=='__main__':
    DATA.mkdir(parents=True,exist_ok=True);ASSETS.mkdir(parents=True,exist_ok=True)
    truth,pairs=prepare_data();charts(truth,pairs);handoff()
    print('PASS: source hashes,162 attempts,27 exact pairs,9 cells,3 full9-case aggregates,22-pair Webster comparison; chart/data exports complete')
