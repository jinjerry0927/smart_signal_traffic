"""Presentation diagnostics from the exact decision frame; never selects actions."""
import copy

import numpy as np

from transferable_signal.controllers import legal_best

DIAGNOSTIC_VERSION = 'decision-view-v1'


def capture(policy, observation, action):
    if not hasattr(policy, 'scores'):
        return None
    decision = copy.deepcopy(policy.last_decision)
    scores = np.asarray(policy.scores(observation['features']), dtype=float)
    return validate(dict(version=DIAGNOSTIC_VERSION, scores=scores.tolist(), decision=decision),
                    observation, action)


def validate(value, observation, action):
    """Validate optional display data independently of mandatory action checks."""
    if not isinstance(value, dict) or value.get('version') != DIAGNOSTIC_VERSION:
        raise ValueError('Unsupported decision diagnostics')
    count = len(observation['features'])
    scores = np.asarray(value['scores'], dtype=float)
    decision = value['decision']
    if scores.shape != (count,) or not np.isfinite(scores).all() or not isinstance(decision, dict):
        raise ValueError('Invalid decision diagnostics')
    for name in ('action', 'raw_ai_action'):
        if type(decision.get(name)) is not int or not 0 <= decision[name] < count:
            raise ValueError('Invalid diagnostic action')
    if (decision['action'] != action
            or decision['raw_ai_action'] != legal_best(scores, observation['mask'])):
        raise ValueError('Diagnostic/action mismatch')
    for name in ('low_load', 'queue_guard_triggered', 'wait_guard_triggered'):
        if type(decision.get(name)) is not bool:
            raise ValueError('Invalid diagnostic guard')
    if decision.get('mode') not in ('low_pressure', 'low_fixed', 'ai_queue_guard'):
        raise ValueError('Invalid diagnostic mode')
    return dict(version=DIAGNOSTIC_VERSION, scores=scores.tolist(), decision=copy.deepcopy(decision))


def snapshot(observation, clock, action, diagnostics, time_s, source, samples=None, controller='smart'):
    """Use raw clock seconds alongside normalized model inputs (not vehicle waits)."""
    features = np.asarray(observation['features'], dtype=float)
    mask = np.asarray(observation['mask'], dtype=bool)
    decision = diagnostics['decision'] if diagnostics else None
    scores = diagnostics['scores'] if diagnostics else None
    ranks = {}
    if scores is not None:
        for rank, index in enumerate(sorted(np.flatnonzero(mask), key=lambda i: (-scores[i], i)), 1):
            ranks[int(index)] = rank
    phases = []
    for index, phase in enumerate(clock.phases):
        incoming = phase.get('incoming', [])
        phases.append(dict(index=index, phase_id=phase.get('id', str(index)),
                           features=features[index].tolist(), legal=bool(mask[index]),
                           pressure=float(observation['pressure'][index]),
                           score=scores[index] if scores else None, rank=ranks.get(index),
                           red_age_s=float(clock.red_age[index]),
                           green_elapsed_s=clock.elapsed if clock.stage == 'green' and index == clock.current else 0,
                           incoming=incoming,
                           queued_vehicles=sum(samples[l]['queue'] for l in incoming) if samples is not None else None))
    limits = clock.limits
    if clock.pending is not None:
        constraint = '황색/전체 적색 전환 중에는 목표 신호만 선택할 수 있습니다.'
    elif clock.elapsed < limits['min_green_s']:
        constraint = f"최소 녹색 {limits['min_green_s']}초까지 현재 신호를 유지합니다."
    elif max((age for i, age in enumerate(clock.red_age) if i != clock.current), default=0) >= limits['max_red_s']:
        constraint = '미서비스 시간 상한에 도달한 신호를 우선합니다.'
    elif clock.elapsed >= limits['max_green_s']:
        constraint = f"최대 녹색 {limits['max_green_s']}초에 도달해 현재 신호는 재선택할 수 없습니다."
    else:
        constraint = '전환 제약 내에서 모든 신호 후보를 비교할 수 있습니다.'
    if controller != 'smart':
        reason = {'fixed': 'Fixed 순환 제어', 'webster': 'Webster 주기 제어',
                  'max_pressure': 'Max-Pressure 압력 제어'}[controller]
        explanation = '기존 제어기의 실제 선택과 관측입니다. 이 실행에서는 Double DQN을 추론하지 않습니다.'
    elif decision is None:
        reason, explanation = '판단 정보 미제공', '실제 선택과 관측을 표시합니다. AI 평가/보정 기록은 제공되지 않았습니다.'
    elif decision['wait_guard_triggered']:
        reason = '긴 대기 보호 보정'
        explanation = (f"현재 녹색 {clock.elapsed}초와 미서비스/대기 조건이 보정 기준을 충족했습니다. "
                       f"AI 추천 그룹 {decision['raw_ai_action'] + 1}에서 최종 그룹 {action + 1}로 보정했습니다.")
    elif decision['queue_guard_triggered']:
        reason = '대기열 불균형 보정'
        explanation = 'AI의 현재 신호 유지 추천에 대기열 불균형 조건이 작동해 압력 기반 선택을 적용했습니다.'
    elif decision['mode'] == 'low_pressure':
        reason = '저부하 압력 제어'
        explanation = '진입 차량 밀도가 저부하 기준 이하이므로, 선택 가능한 후보 중 압력이 가장 큰 신호를 선택했습니다.'
    elif decision['mode'] == 'low_fixed':
        reason = '저부하 순환 제어'
        explanation = '저부하 순환 규칙과 합법 행동 제한에 따라 신호를 선택했습니다.'
    else:
        reason = 'AI 점수 기반 선택'
        explanation = '모델이 각 후보의 7개 관측값과 전체 후보 평균을 평가하고, 선택 가능한 후보 중 최고 점수의 신호를 추천했습니다.'
    if mask.sum() == 1 and diagnostics:
        reason = '전환 제약에 따른 선택'
        explanation = constraint + ' 이 시점에는 선택 가능한 후보가 하나입니다.'
    return dict(time_s=float(time_s), source=source, current_phase=clock.current,
                stage=clock.stage, elapsed_s=clock.elapsed, action=int(action),
                raw_ai_action=decision['raw_ai_action'] if decision else None,
                controller=controller,
                diagnostic_available=diagnostics is not None, reason=reason, explanation=explanation,
                constraint=constraint, phases=phases, diagnostics=diagnostics)
