"""Seal the validation-selected V2 research reference after independent audits."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import final_signal_wave as wave
from transferable_signal.deployment import validated_policy, PROFILE_PATH
from transferable_signal.spec import digest, read_json


def freeze():
    release = wave.OUTPUT / 'release'
    if release.exists(): raise FileExistsError('Preserve an existing final release')
    chosen = read_json(wave.OUTPUT / 'selection.json')
    if chosen['chosen'] != 'v2' or not chosen['fallback_used'] or not chosen['frozen_before_final']:
        raise ValueError('This release seals the validation-selected existing V2 only')
    reports = {}
    artifacts = {}
    for stage, expected in (('validation', 144), ('final', 96)):
        path = ROOT / f'.codex-artifacts/final-wave10-{stage}-independent-20261007.json'
        report = read_json(path)
        meta = read_json(wave.OUTPUT / stage / 'manifest.json')
        if report['status'] != 'PASS' or report['runs'] != expected or meta['status'] != 'completed' or meta['actual_new_runs'] != expected:
            raise ValueError('Both completed independent audits are required')
        for record in meta['records']:
            for filename, hashed in record['raw_sha256'].items():
                if digest(wave.OUTPUT / record['directory'] / filename) != hashed:
                    raise ValueError('Audited original record changed before release')
        reports[stage] = report
        for p in (path, wave.OUTPUT / stage / 'manifest.json', wave.INPUT / stage / 'suite.json'):
            artifacts[str(p.relative_to(ROOT))] = digest(p)
    summary = read_json(wave.OUTPUT / 'final_summary.json')
    if reports['validation']['summary'] != chosen or reports['final']['summary'] != summary or summary['chosen'] != 'v2' or summary['final_reselection'] is not False:
        raise ValueError('Frozen selection or final result differs from independent evidence')
    with wave.context(): proof = wave.preflight(preserved=True)
    policy, model, identity = validated_policy()
    for p in (wave.OUTPUT / 'selection.json', wave.OUTPUT / 'final_summary.json', wave.INPUT / 'proof.json', PROFILE_PATH):
        artifacts[str(p.relative_to(ROOT))] = digest(p)
    release.mkdir()
    shutil.copy2(model, release / 'model.json')
    shutil.copy2(PROFILE_PATH, release / 'policy.json')
    loaded, copied_path, copied_identity = validated_policy(release / 'model.json')
    if digest(copied_path) != identity['model_sha256'] or copied_identity['config'] != identity['config'] or copied_identity['variant'] != identity['variant']:
        raise ValueError('Copied final model/policy does not match the running default')
    command = ('python -B -m transferable_signal run --scenario gyeongju_busy --controller smart '
        f'--model {(release / "model.json").relative_to(ROOT).as_posix()} --out results/runs/smart_final_demo_01')
    result = dict(release_version='smart-signal-research-final-v1', status='finalized_research_reference',
        seed=identity['seed'], variant=identity['variant'], policy_version=identity['version'],
        model_path=str((release / 'model.json').relative_to(ROOT)), model_sha256=identity['model_sha256'],
        policy_path=str((release / 'policy.json').relative_to(ROOT)), policy_sha256=digest(release / 'policy.json'),
        features=identity['features'], limits=identity['limits'], additional_training=0,
        selected_on_validation_only=True, final_reselection=False, unchanged_default=True,
        inference_source_sha256=identity['source_sha256'], factory_sha256=identity['factory_sha256'],
        release_builder_sha256=digest(Path(__file__)), evidence_sha256=artifacts,
        validation_runs=144, final_runs=96, safety_events=dict(collisions=0,teleports=0),
        emergency_braking_warnings={s:len(r['warnings']) for s,r in reports.items()},
        final_target_met=summary['target_met'], final_summary=summary,
        scope='Supported phase/observation contract; synthetic demand, no field performance certification',
        command=command)
    with (release / 'release.json').open('x', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)
    with (release / 'README.md').open('x', encoding='utf-8') as f:
        f.write('# SmartSignal 최종 연구 기준 v1\n\n'
            '검증으로 선정한 기존 seed43 / load_gate_pressure_wait35(V2)를 동결했습니다.\n'
            '새30초 후보는 재검증에서 기각했습니다. 기본 smart 명령은 같은 모델/규칙을 사용합니다.\n\n'
            '## VS Code에서 실행\n\n프로젝트 폴더를 열고 터미널에서 다음을 실행합니다.\n\n'
            '```powershell\n' + command + '\n```\n\n'
            'SUMO 화면은 --gui를 추가합니다. 결과 폴더가 이미 있으면 새 이름을 사용합니다.\n\n'
            '## 확정 범위\n\n새48검증 조건144회와 미사용24최종 조건96회, 독립 원시 감사 이후 확정했습니다.\n'
            '추가 학습/최종 결과 재선정은 없습니다. model.json은 학습 가중치, policy.json은 공통 규칙/안전 설정입니다.\n'
            'release.json에 정확한 모델/정책/소스/근거 SHA와 성능·경고·한계를 기록했습니다.\n'
            '최종 성능 기준 통과 여부: ' + str(summary['target_met']) + '.\n'
            '가상 수요 연구 기준이며 현장 성능이나 모든 교차로의 최적성을 보장하지 않습니다.\n')
    assert digest(release / 'model.json') == digest(model)
    assert digest(release / 'policy.json') == digest(PROFILE_PATH)
    assert validated_policy()[2] == identity
    print(json.dumps({k: result[k] for k in ('status','seed','variant','validation_runs','final_runs','final_target_met','emergency_braking_warnings')}, ensure_ascii=False))
    return result


if __name__ == '__main__': freeze()
