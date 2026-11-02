"""Presentation adapter for frozen new networks; never changes evaluation inputs."""
import copy
from pathlib import Path

# The builder locates the installed SUMO tools before the web runtime imports TraCI.
from tools import unseen_transfer_build as build
from tools.unseen_transfer_experiment import checked_evaluation
from transferable_signal.spec import digest, read_json
from .runtime import Demo, NAMES, SCENARIOS, map_data
from .release import DEFAULT_RELEASE, ROOT

NETWORKS = dict(gyeongju='기존 쇼케이스 · 경주 3교차로', zt_t1_short='새 도로망 A · 짧은 삼거리',
                zt_x31_asymmetric='새 도로망 B · 3:1 비대칭 사거리', zt_pair_short='새 도로망 C · 짧은 연결 2교차로')
JUNCTION_NAMES = dict(NAMES, zt_t1_short='새 삼거리', zt_x31_asymmetric='새 비대칭 사거리',
                      zt_a='남쪽 교차로', zt_b='북쪽 교차로')
PROFILE_NAMES = dict(quiet='한산', busy='혼잡', directional='방향 편중')
RESULTS = ROOT / 'results/runs/unseen_transfer_20261028'


def display_summary(value):
    controllers = {('v2' if n == 'smart_v2' else n): v for n,v in value['controllers'].items()}
    return dict(controllers=controllers, queue_change=value['smart_queue_change'])


class TrialDemo(Demo):
    trial_view = True

    def __init__(self, output=ROOT/'results/runs/unseen_web_20261029', release=DEFAULT_RELEASE):
        super().__init__(output, release)
        self.original_path, self.original_suite, self.original_cases = self.suite_path, self.suite, self.cases
        self.original_comparison = self.comparison
        self.fingerprint = checked_evaluation(build.DEFAULT_INPUT, require_review=True)
        certificate = read_json(build.BUNDLE/'report-verification.json')
        if (digest(RESULTS/'reviewed-followup-summary.json') != certificate['summary_sha256']
                or digest(RESULTS/'reviewed-followup-comparison.csv') != certificate['comparison_sha256']):
            raise ValueError('Audited trial results changed')
        self.trial_result = read_json(RESULTS/'reviewed-followup-summary.json')
        if (self.trial_result['unattempted_runs'] or self.trial_result['fingerprint'] != self.fingerprint
                or self.identity['model_sha256'] != self.fingerprint['model_sha256']):
            raise ValueError('New-network results or the current model are inconsistent')
        self.trial_suite, self.trial_cases = build.preflight(build.DEFAULT_INPUT/'suite.json')
        self.ready_state = copy.deepcopy(self.state)
        self.junction_names = JUNCTION_NAMES.copy()
        self.selected_case = None
        self.select_network('zt_t1_short')

    def select_network(self, case):
        if not isinstance(case, str) or case not in NETWORKS:
            raise ValueError('Unknown trial network')
        with self.lock:
            if self.worker is not None and self.worker.is_alive():
                raise RuntimeError('Stop the current run before changing the network')
            if case == 'gyeongju':
                suite_path, suite, cases = self.original_path, self.original_suite, self.original_cases
                scenarios = SCENARIOS.copy()
                comparison = self.original_comparison
            else:
                suite_path, suite, cases = build.DEFAULT_INPUT/'suite.json', self.trial_suite, self.trial_cases
                scenarios = {s['id']: PROFILE_NAMES[s['profile']]+' · 표본 '+str(int(s['id'].rsplit('s',1)[1])+1)
                             for s in suite['scenarios'] if s['case'] == case}
                comparison = dict(overall=display_summary(self.trial_result['overall_common_completed']),
                    cases={k:display_summary(v) for k,v in self.trial_result['cases_common_completed'].items()})
            spec, network, _ = cases[case]
            drawing = map_data(network, spec, self.junction_names)
            self.suite_path, self.suite, self.cases = suite_path, suite, cases
            self.scenarios, self.comparison, self.map = scenarios, comparison, drawing
            self.selected_case, self.worker = case, None
            revision = self.state['revision']
            self.state = self._ready_state(case)
            self.state['revision'] = revision + 1
            self.state.update(case=case, scenario=None, controller='smart')
            return self.boot()

    def boot(self):
        with self.lock:
            result = super().boot()
            trial = self.selected_case != 'gyeongju'
            result.update(title=NETWORKS[self.selected_case], selected_case=self.selected_case, networks=NETWORKS,
                exposure='저장소 이력과 대조한 새 기하 · 추가 학습 0회' if trial else '기존 쇼케이스 · 새 교차로 시험과 구분',
                trial=trial, inference_modes=['local'])
            if trial:
                count = 9-sum(f['id'].startswith(self.selected_case+'_') for f in self.trial_result['failed'])
                result['evidence'] = dict(common_conditions=self.trial_result['common_complete_conditions'], case_conditions=count,
                    completed=self.trial_result['completed_runs'], failed=self.trial_result['failed_runs'],
                    controller_coverage=self.trial_result['controller_coverage'], primary_target_evaluable=False,
                    learning_target_met=self.trial_result['learned_contribution_target_on_complete_pairs'],
                    pairs=self.trial_result['paired_smart_comparisons'], failure_jobs=[f['id'] for f in self.trial_result['failed']])
            else:
                result['evidence'] = dict(common_conditions=24, case_conditions=6, completed=96, failed=0)
            return result

    def start(self, options):
        if not isinstance(options, dict):
            raise ValueError('Options object required')
        options = dict(options)
        with self.lock:
            if options.get('mode','local') != 'local':
                raise ValueError('This trial view uses PC inference')
            options.setdefault('scenario',next(iter(self.scenarios)))
            if options['scenario'] not in self.scenarios:
                raise ValueError('Scenario does not belong to the selected network')
            if self.selected_case != 'gyeongju':
                if checked_evaluation(build.DEFAULT_INPUT, require_review=True) != self.fingerprint:
                    raise ValueError('Frozen trial inputs or producer changed')
            return super().start(options)
