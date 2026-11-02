"""Presentation bridge for the opt-in V2; no change to the Legacy Pi protocol."""
from realistic_signal.environment import RealisticEnvironment
from .visualization import VehicleFrames


class RealisticLiveEnvironment(RealisticEnvironment):
    def __init__(self, *args, on_frame, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_frame, self.last_frame = on_frame, -1
        self.vehicle_frames = VehicleFrames()

    def observe(self):
        observation = super().observe()
        now = self.connection.simulation.getTime()
        if now != self.last_frame:
            self.last_frame = now
            self.on_frame(self, observation)
        return observation


def explain_constraints(detail, clock):
    detail['constraint'] = ('이동별 최소·연속 최대녹색과 미서비스 시간을 검사한 마스크입니다. '
                            '종료 이동 황색 3초·소거 2초 동안 공통 이동은 계속 녹색입니다.')
    if clock.pending is not None:
        detail['constraint'] = '이동별 전환 중이며 다음 그룹만 선택할 수 있습니다. 유지 이동은 녹색을 계속합니다.'
    detail['model_compatibility'] = 'experimental_transfer_unvalidated'
    detail['movement_green_age_s'] = clock.green_age.tolist()
    detail['movement_red_age_s'] = clock.movement_red_age.tolist()
    detail['forced_changes'] = clock.forced_changes
    detail['explanation'] += ' Realistic V2의 행동 의미가 달라 기존 모델의 성능은 미검증입니다.'
    if sum(p['legal'] for p in detail['phases']) == 1:
        detail['explanation'] = detail['constraint'] + ' 선택 가능한 후보가 하나입니다.'
    for p, active in zip(detail['phases'], clock.greens):
        p['green_elapsed_s'] = int(max(clock.green_age[active])) if p['index'] == clock.current and clock.stage == 'green' else 0
