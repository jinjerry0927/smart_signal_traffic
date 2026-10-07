"""Shared observation-only guards; keep the validated V2/model immutable."""
from types import MappingProxyType
import numpy as np
from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2
from transferable_signal.controllers import legal_best

VARIANTS = MappingProxyType({
    'all_density25': MappingProxyType(dict(version='common-rules-v3', guard_green_s=25,
        queue_margin=.01, balanced_low=False, hold_until_s=25, pressure_margin=2)),
    'balanced25': MappingProxyType(dict(version='common-rules-v3', guard_green_s=25,
        queue_margin=.01, balanced_low=True, hold_until_s=25, pressure_margin=2)),
    'balanced20': MappingProxyType(dict(version='common-rules-v3', guard_green_s=20,
        queue_margin=.01, balanced_low=True, hold_until_s=25, pressure_margin=2)),
})


class CommonSignalPolicy:
    def __init__(self, path, variant):
        if variant not in VARIANTS:
            raise ValueError(f'Unknown common rule variant: {variant}')
        self.base = AdaptiveSmartSignalV2(path, 'load_gate_pressure_wait35')
        self.data, self.variant, self.config = self.base.data, variant, VARIANTS[variant]
        self.last_decision = None

    def action(self, jid, observation, clock):
        # V2 checks finite features/scores/pressure and the mask before evaluation.
        action = self.base.action(jid, observation, clock)
        features = np.asarray(observation['features'], dtype=float)
        pressure = np.asarray(observation['pressure'], dtype=float)
        mask = np.asarray(observation['mask'], dtype=bool).copy()
        if not np.array_equal(mask, clock.mask()):
            raise ValueError('Observation differs from the safety clock mask')
        current, config = clock.current, self.config
        other = mask.copy()
        other[current] = False
        details = dict(self.base.last_decision)
        mode, guarded, held = 'v2', False, False
        if config['balanced_low'] and details['low_load']:
            # Pressure magnitude is normalized within this live observation.
            # Age boosts occupied approaches only; empty arms gain no age bonus.
            occupied = features[:, 1] > 0
            priority = (pressure / (1 + np.max(np.abs(pressure)))
                        + .5 * features[:, 1] + .25 * features[:, 2]
                        + .5 * features[:, 4] * occupied)
            action, mode = legal_best(priority, mask.copy()), 'balanced_low'
            held = bool(clock.stage == 'green' and mask[current]
                        and clock.elapsed < config['hold_until_s'] and other.any()
                        and np.max(pressure[other]) <= pressure[current] + config['pressure_margin'])
            if held:
                action, mode = current, 'small_pressure_hold'
        # Eligible alternatives only: the old guard could select the current
        # phase again, and its density cap disabled protection under congestion.
        candidates = other & (features[:, 0] >= features[current, 0] + config['queue_margin'])
        if (clock.stage == 'green' and clock.elapsed >= config['guard_green_s']
                and action == current and candidates.any()):
            action, guarded, mode = legal_best(pressure, candidates.copy()), True, 'all_density_guard'
        if not mask[action]:
            raise ValueError('Common rule selected an illegal phase')
        self.last_decision = dict(details, version=config['version'], variant=self.variant,
            v2_action=details['action'], action=int(action), common_mode=mode,
            common_guard=guarded, small_pressure_hold=held, common_candidates=candidates.tolist())
        return int(action)
