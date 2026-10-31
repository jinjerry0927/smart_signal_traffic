"""Write a human-readable report from the audited interrupted/follow-up ledger."""
import collections
import argparse
import csv
import json
import math
import statistics
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal.spec import digest, read_json, write_json

OUT = ROOT / 'results/runs/unseen_transfer_20261028'
DOC = ROOT / 'docs/unseen_transfer_20261028'
LABELS = dict(fixed='Fixed', webster='Webster', max_pressure='Max-Pressure', smart_v2='SmartSignal V2', raw_smart='학습 모델 단독', rules_pressure='규칙 중심 진단')


def signed(value):
    return '계산 불가' if value is None else f'{value*100:+.2f}%'


def generate():
    summary = read_json(OUT / 'reviewed-followup-summary.json')
    if summary['status'] != 'all_planned_attempts_accounted_for' or summary['unattempted_runs']:
        raise ValueError('Report requires every planned attempt to be accounted for')
    assert summary['completed_runs'] + summary['failed_runs'] == 162
    assert summary['formal_primary_target_met'] is None and not summary['formal_primary_target_evaluable']
    assert summary['parameter_changes'] == summary['additional_training'] == summary['traffic_reruns'] == 0
    assert sum(c['completed'] for c in summary['controller_coverage'].values()) == summary['completed_runs']
    assert sum(c['failed'] for c in summary['controller_coverage'].values()) == summary['failed_runs']
    with (OUT / 'reviewed-followup-comparison.csv').open(encoding='utf-8-sig', newline='') as f:
        attempts = list(csv.DictReader(f))
    assert len(attempts) == 162 and len({r['job'] for r in attempts}) == 162
    successful = [r for r in attempts if r['outcome'] == 'completed']
    assert len(successful) == summary['completed_runs']
    pairs = summary['paired_smart_comparisons']
    learned = summary['learned_contribution_target_on_complete_pairs']
    modes, warnings = collections.Counter(), collections.Counter()
    smart_rows = []
    for row in successful:
        name = row['job'].split('/')[-1]
        metrics = read_json(OUT / row['path'] / 'metrics.json')
        warnings[name] += len(metrics['warnings'])
        if name == 'smart_v2':
            modes.update(metrics['policy_mode_usage'])
            smart_rows.append(row)
    by_job = {r['job']:r for r in successful}
    condition_deltas = []
    repeats = collections.defaultdict(list)
    common_by_case=collections.Counter()
    for row in smart_rows:
        scenario = row['job'].rsplit('/',1)[0]
        baseline = by_job.get(scenario+'/fixed')
        if baseline:
            q, b = float(row['mean_queue_vehicles']), float(baseline['mean_queue_vehicles'])
            if b <= 0:
                raise ValueError('A zero queue baseline needs explicit undefined-ratio reporting')
            condition_deltas.append((q/b-1, scenario, q, b))
            meta=read_json(OUT / row['path'] / 'manifest.json')['scenario']
            repeats[(meta['case'],meta['profile'])].append((q/b-1,int(row['arrived']),int(baseline['arrived'])))
            if all(scenario+'/'+name in by_job for name in LABELS):
                common_by_case[meta['case']]+=1
    adverse = [r for r in condition_deltas if r[0] > 1e-12]
    lines = ['# 새 교차로 동결 모델 적용 — 비교 결과', '',
        f'최종 확인일: 2026-10-29 / 시험 기준 동결: 2026-10-28. 도로망 제작·호환·평가 준비·별도 검토와 예정162개 비교의 시도 및 원시 검증을 마쳤다. 완료{summary["completed_runs"]}회, 실패{summary["failed_runs"]}회, 미시도0회다.', '',
        '**추가 학습·모델/규칙/수요 변경·실패 재시도는 모두0회다.** 기존 seed43/V2/wait35를 그대로 사용했다.', '',
        '![실제 SUMO 차로 좌표로 작성한 새 시험 도로망](road-layout-preview.png)', '',
        '## 제작과 호환성', '',
        '| 도로망 | 쇼케이스와의 실질적 차이 | 확인 |', '|---|---|---|',
        '| 짧은 접근로 삼거리 | 삼거리·편도1차로·비대칭 접근 길이 | 신호1개/녹색3후보·실제 최단 접근134.8m |',
        '| 3:1 비대칭 사거리 | 남북3/동서1차로·비대칭 길이 | 신호1개/녹색4후보·실제 최단 접근118.4m |',
        '| 짧은 연결2교차로 | 신호2개·중심 간격160m·연결 편도2차로 | 각4후보·양방향 연결 차로129.6m |', '',
        '27개 명시적 수요의0초 로드, 보호 신호/공유 차로/전 차로 서비스, OD·차종 접근을 확인했다. 별도600초 기능 검사3회에서144/144대가 도착했고 충돌·teleport·경고0이었다. 모든 녹색 후보를 실제 관찰하고 매초 관측·신호·차량 지표를 독립 재계산했다.', '',
        '## 실패와 시험 해석', '',
        '원래 본 배치는127회 완료 후128번째 연결 구간 혼잡 Webster에서 버스의300초 연속 대기/yield teleport로 중단했다. 실패 운행은504초 관측까지 기록했으며900초 완료 지표로 취급하지 않았다.', '',
        '별도 중단 검토 후 미시도34회만 별도 후속 폴더에 수집했다. 각 후속 배치도 오류·충돌·teleport·기록 불일치 시 전체 중단하고 재검토했다. 원래 실패 배치·원시 기록·이전 검증기 버전을 모두 보존했다.', '',
        '**원래27조건 전체의 사전 주 목표 통과를 주장할 수 없다.** 아래는 중단 후 별도 수집까지 포함한 서술적 비교이며, 실패를 뺀 숫자만으로 원래 시험을 성공으로 바꾸지 않는다. 제어별 완료 조건 수와 비교에 사용한 짝의 수를 함께 적었다.', '',
        '| 제어 | 900초 완료 | 실패 | 완료 운행의 경고 수 |', '|---|---:|---:|---:|']
    for name, c in summary['controller_coverage'].items():
        lines.append(f'| {LABELS[name]} | {c["completed"]}/27 | {c["failed"]} | {warnings[name]} |')
    lines += ['', '실패 목록:', '']
    for f in summary['failed']:
        lines.append(f'- `{f["id"]}`: {f["outcome"]}, 관측{f["event"]["time_s"]}초, 충돌{f["event"]["collisions"]}/teleport{f["event"]["teleports"]}. 신호·관측·정책 prefix 재생 {f["prefix_replay"]}.')
    lines += ['', '첫 실패는 경로의 연결 차로가 연속이며 필수 차로 변경도 없었다. 연결 차로 혼잡과 상·하류 녹색 시점의 불일치에 부합하지만 정확한 양보 간격·선행차 원인은 위치/간격 자료가 없어 미확정이다. [진단](teleport-diagnosis.json), [중단 검토](teleport-review-01.md), [SUMO teleport 설명](https://sumo.dlr.de/docs/Simulation/Why_Vehicles_are_teleporting.html).', '',
        '## 동일 조건끼리의 SmartSignal 비교', '',
        '각 행은 SmartSignal과 해당 비교 제어가 모두900초 완료한 조건만 짝지었다. 조건 수가 다른 행의 평균은 서로 다른 표본이므로 전체27조건의 동일 평균으로 간주하지 않는다. 변화율은 음수일수록 대기행렬이 작다.', '',
        '| 비교 제어 | 완료 짝 수 | Smart 평균 대기행렬(대) | 비교 평균(대) | Smart 변화율 | 도착 수 Smart / 비교 |', '|---|---:|---:|---:|---:|---:|']
    for name,p in pairs.items():
        lines.append(f'| {LABELS[name]} | {p["paired_conditions"]} | {p["smart_mean_queue"]:.3f} | {p["baseline_mean_queue"]:.3f} | {signed(p["smart_queue_change"])} | {p["smart_arrived"]} / {p["baseline_arrived"]} |')
    lines += ['', f'학습 기여 사전 목표(규칙 진단 대비 대기행렬2% 이상 감소·도착 비감소)는 완료 짝에서 **{"충족" if learned else "미충족"}**이다. 학습 점수를 호출하거나 새 도로망에서 실행된 사실만으로 학습의 성능 기여를 입증했다고 하지 않는다.', '',
        f'Fixed 대비 개별 대기행렬 악화는 공통 완료{len(condition_deltas)}조건 중{len(adverse)}조건이다. 최악 조건:', '']
    for delta, scenario, q, b in sorted(condition_deltas, reverse=True)[:3]:
        lines.append(f'- `{scenario}`: Smart {q:.3f}대 / Fixed {b:.3f}대, {signed(delta)}.')
    lines += ['', '## 6제어가 모두 완료한 조건의 구간별 결과', '',
        f'이 표는 공통 완료{summary["common_complete_conditions"]}조건만 사용한다. 중단 때문에 제외된 어려운 조건이 있어 원래 전체 목표의 판정 자료로 대체하지 않는다.', '',
        '| 도로망 | 공통 완료 조건 | Smart 대기행렬(대) | Fixed 대비 | Webster 대비 | Max-Pressure 대비 |', '|---|---:|---:|---:|---:|---:|']
    for case,v in summary['cases_common_completed'].items():
        ch=v['smart_queue_change']
        lines.append(f'| `{case}` | {common_by_case[case]}/9 | {v["controllers"]["smart_v2"]["mean_queue"]:.3f} | {signed(ch["fixed"])} | {signed(ch["webster"])} | {signed(ch["max_pressure"])} |')
    lines += ['', '## 수요 표본 간 변동', '',
        '각 칸은 해당 도로망·수요의 Smart/Fixed 공통 완료 표본이다. 변화율은 표본별 비율의 평균±표본 표준편차이며, 위의 전체 평균 비율과 계산 단위가 다르다. 최대3개 표본의 서술 통계로 신뢰구간/유의성이나 학습 초기값 반복을 주장하지 않는다.', '',
        '| 도로망 | 수요 | 완료 짝 / 예정3 | Fixed 대비 변화율 평균 ± SD | 표본 최소~최대 | 도착 수 Smart / Fixed |', '|---|---|---:|---:|---:|---:|']
    for (case,profile),values in sorted(repeats.items()):
        ds=[v[0] for v in values]
        sd=f'{statistics.stdev(ds)*100:.2f}%p' if len(ds)>1 else '계산 불가'
        lines.append(f'| `{case}` | {profile} | {len(values)}/3 | {signed(statistics.mean(ds))} ± {sd} | {signed(min(ds))} ~ {signed(max(ds))} | {sum(v[1] for v in values)} / {sum(v[2] for v in values)} |')
    lines += ['', '## 실행 근거와 재현', '',
        f'- 실제 교통 시도: 기능3회 + 예비12회 + 비교162회 = {summary["total_traffic_attempts"]}회. 0초 로드27회는 교통 운행과 구분한다.',
        '- 새 기능9검사·기존 이력 감사9검사·후속 보호5검사를 통과했다. 기능/예비/완료 비교의 독립 재생과 원시 파일 SHA를 확인했다. 실패 운행은 별도 prefix 검사로 기록한다.',
        '- 예비8번째 Webster 검증기의 수치 경계 불일치를 수정했으며 원래8개 운행의 기록/생산 소스를 유지한 재감사만 수행했다. 이 오류를 통과로 덮어쓰거나 교통을 재운행하지 않았다.',
        '- 실행 환경: Python3.12.14 / NumPy2.3.5 / SUMO1.26.0. 원래17개 기준 소스와 모델/정책 해시는 유지했다.',
        f'- Smart 실행 분기 집계: `{dict(modes)}`. 분기 이름은 진단 정보이며 학습 효과의 비율이나 인과 추정으로 해석하지 않는다.',
        '- 가상 수요·지원된 T/X·1~3차로의 새 기하에 대한 결과다. 미지원 교차로/실제 현장/모든 조건의 우수성을 주장하지 않는다.', '',
        '이력 대조는 확인 가능한 모델 메타데이터·입력·문서 근거를 사용했다. 과거 원시 근거5개는 이 컴퓨터에 없으므로 모든 과거 운행을 재감사했다는 주장은 하지 않는다. 새 입력의 중복 제외·고정 해시와 실제 이번 원시 검증은 별도로 남겼다.', '',
        '[제작·검사·실행 명령](execution.md), [별도 평가 검토](evaluation-review.md), [동결 명세](protocol.json).', '',
        '[기계 판독 결과](../../results/runs/unseen_transfer_20261028/reviewed-followup-summary.json), [모든162시도 CSV](../../results/runs/unseen_transfer_20261028/reviewed-followup-comparison.csv).', '',
        '재현 확인은 번들 Python의 `-X utf8 -B`로 `tools/audit_unseen_transfer.py verify`와 마지막 후속 번호의 `tools/report_unseen_transfer.py --verify --round N`을 사용한다. 후자는 원시/부모 배치·검토·소스 SHA와 모든162행, 짝별 평균·변화율·도착 수를 읽기 전용으로 재검증한다. 운행 명령은 기존 출력 경로에 재실행하지 않는다.', '',
        '다음 연구 작업은 처리량과 대기행렬의 상충·실패 상황을 바탕으로 별도 개발 환경에서 개선 가설을 정하는 것이다. 이 시험에서 본 결과로 규칙을 바꾸고 같은 표본을 다시 미사용 최종 시험으로 주장하지 않는다.', '']
    path = DOC/'results.md'
    if path.exists():
        raise FileExistsError('Preserve previous report')
    path.write_text('\n'.join(lines), encoding='utf-8')
    write_json(DOC/'report-verification.json', dict(status='PASS', summary_sha256=digest(OUT/'reviewed-followup-summary.json'),
        comparison_sha256=digest(OUT/'reviewed-followup-comparison.csv'), report_sha256=digest(path),
        report_source_sha256=digest(Path(__file__)), diagram_sha256=digest(DOC/'road-layout-preview.png'),
        attempts=162, completed=summary['completed_runs'], failed=summary['failed_runs'],
        original_full_protocol_pass_claim=False, failed_partial_metrics_excluded=True))
    print('PASS: report provenance/count/pairing safeguards; wrote',path)


def verify(number):
    from tools import unseen_transfer_followup as followup
    fp, jobs, attempts, parents = followup.ledger(number+1)
    assert len(attempts) == len(jobs) == 162
    certificate = read_json(DOC/'report-verification.json')
    for key, path in [('summary_sha256',OUT/'reviewed-followup-summary.json'),
                      ('comparison_sha256',OUT/'reviewed-followup-comparison.csv'),
                      ('report_sha256',DOC/'results.md'),('report_source_sha256',Path(__file__)),
                      ('diagram_sha256',DOC/'road-layout-preview.png')]:
        assert digest(path) == certificate[key], ('Report artifact changed',str(path))
    summary = read_json(OUT/'reviewed-followup-summary.json')
    assert summary['fingerprint'] == fp and summary['parents'] == parents
    with (OUT/'reviewed-followup-comparison.csv').open(encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    assert len(rows) == 162
    completed = {}
    for a,r in zip(attempts,rows):
        assert (a['id'],a['outcome'],a['path']) == (r['job'],r['outcome'],r['path'])
        if a['outcome']=='completed':
            m=a['record']['metrics']
            assert float(r['mean_queue_vehicles']) == m['mean_queue_vehicles']
            assert int(r['arrived'])==m['arrived_vehicles'] and int(r['scheduled'])==m['scheduled_vehicles']
            assert int(r['remaining']) == m['not_departed_vehicles']+m['departed_not_arrived_vehicles']
            scenario,name=r['job'].split('/')
            completed[(scenario,name)]=r
        else:
            assert all(not r[key] for key in ['mean_queue_vehicles','arrived','scheduled','remaining'])
    for name,pair in summary['paired_smart_comparisons'].items():
        keys=[s for s,n in completed if n==name and (s,'smart_v2') in completed]
        smart=[completed[(s,'smart_v2')] for s in keys]
        base=[completed[(s,name)] for s in keys]
        q=sum(float(r['mean_queue_vehicles']) for r in smart)/len(keys)
        b=sum(float(r['mean_queue_vehicles']) for r in base)/len(keys)
        assert len(keys)==pair['paired_conditions']
        assert math.isclose(q,pair['smart_mean_queue'],abs_tol=1e-10)
        assert math.isclose(b,pair['baseline_mean_queue'],abs_tol=1e-10)
        assert math.isclose((q-b)/b,pair['smart_queue_change'],abs_tol=1e-10)
        assert sum(int(r['arrived']) for r in smart)==pair['smart_arrived']
        assert sum(int(r['arrived']) for r in base)==pair['baseline_arrived']
    assert summary['formal_primary_target_met'] is None and not summary['formal_primary_target_evaluable']
    print('PASS: all 162 attempt rows, raw/parent/review/source hashes, pair averages/changes/arrivals and honest incomplete-protocol label; traffic runs 0')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify',action='store_true')
    parser.add_argument('--round',type=int)
    args=parser.parse_args()
    if args.verify:
        if args.round is None:
            parser.error('--verify requires the last --round number')
        verify(args.round)
    else:
        generate()
