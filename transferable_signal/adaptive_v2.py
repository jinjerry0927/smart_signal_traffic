"""Frozen load gates and bounded waiting guards; the v1 policy stays unchanged."""
from types import MappingProxyType

import numpy as np

from . import FEATURES
from .adaptive import _mask
from .controllers import legal_best
from .model import SmartSignal


def _config(low_load_mode, green_s, red_ratio):
    return MappingProxyType(dict(
        version='adaptive-v2', low_load_mode=low_load_mode, density_threshold=.10,
        fixed_green_s=25, guard_green_s=25, guard_density_threshold=.12,
        guard_queue_margin=.01, wait_green_s=green_s,
        wait_queue_ratio=.01, wait_red_ratio=red_ratio, wait_high_load_only=True))


VARIANTS_V2 = MappingProxyType({
    'load_gate_pressure_wait40': _config('pressure', 40, .5),
    'load_gate_fixed_wait40': _config('fixed', 40, .5),
    'load_gate_pressure_wait35': _config('pressure', 35, .4),
})
VARIANTS = VARIANTS_V2


class AdaptiveSmartSignalV2(SmartSignal):
    """Use only the existing observation, trained scorer, and legal Clock mask."""

    def __init__(self, path, variant):
        if variant not in VARIANTS_V2:
            raise ValueError(f'Unknown adaptive v2 variant: {variant}')
        super().__init__(path)
        self.variant, self.config = variant, VARIANTS_V2[variant]
        self.last_decision = None

    def action(self, jid, observation, clock):
        features = np.asarray(observation['features'], dtype=float)
        if (features.ndim != 2 or features.shape[0] < 1
                or features.shape[1] != len(FEATURES) or not np.isfinite(features).all()):
            raise ValueError('Invalid adaptive v2 phase features')
        count = len(features)
        mask = _mask(observation['mask'], count)
        if (len(clock.phases) != count
                or not isinstance(clock.current, (int, np.integer))
                or isinstance(clock.current, (bool, np.bool_))
                or not 0 <= clock.current < count):
            raise ValueError('Invalid adaptive v2 current phase')
        pressure = np.asarray(observation['pressure'], dtype=float)
        if pressure.shape != (count,) or not np.isfinite(pressure).all():
            raise ValueError('Invalid adaptive v2 pressure')
        scores = np.asarray(self.scores(features), dtype=float)
        if scores.shape != (count,) or not np.isfinite(scores).all():
            raise ValueError('Invalid adaptive v2 AI scores')

        config = self.config
        raw_ai = legal_best(scores, mask.copy())
        # Center before averaging so identical boundary values remain identical
        # even on Windows, where longdouble has float64 precision.
        density_values = features[:, 1]
        density = float(density_values[0] + (density_values - density_values[0]).mean())
        other = mask.copy()
        other[clock.current] = False
        queue_guard = bool(
            clock.stage == 'green' and clock.elapsed >= config['guard_green_s']
            and raw_ai == clock.current and density <= config['guard_density_threshold']
            and np.any(features[other, 0] >= (
                features[clock.current, 0] + config['guard_queue_margin'])))
        v1_action = legal_best(pressure, mask.copy()) if queue_guard else raw_ai
        low_load = density <= config['density_threshold']
        if low_load and config['low_load_mode'] == 'pressure':
            action, mode = legal_best(pressure, mask.copy()), 'low_pressure'
        elif low_load:
            mode = 'low_fixed'
            if clock.elapsed < config['fixed_green_s'] and mask[clock.current]:
                action = clock.current
            else:
                following = (clock.current + 1) % count
                action = following if mask[following] else legal_best(clock.red_age, mask.copy())
        else:
            action, mode = v1_action, 'ai_queue_guard'

        base_action = action
        candidates = other & (features[:, 0] >= config['wait_queue_ratio']) \
            & (features[:, 4] >= config['wait_red_ratio'])
        wait_guard = bool(not low_load and clock.stage == 'green' and clock.elapsed >= config['wait_green_s']
                          and action == clock.current and candidates.any())
        if wait_guard:
            action = legal_best(pressure, candidates.copy())
        self.last_decision = dict(
            version=config['version'], variant=self.variant, action=action,
            raw_ai_action=raw_ai, v1_action=v1_action, base_action=base_action,
            mode=mode, low_load=bool(low_load), density_mean=density,
            v1_queue_guard_condition=queue_guard,
            queue_guard_triggered=bool(not low_load and queue_guard), wait_guard_triggered=wait_guard,
            candidate_mask=candidates.tolist())
        return action
