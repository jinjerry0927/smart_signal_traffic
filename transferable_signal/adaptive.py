"""Frozen observation-only SmartSignal candidates with unchanged safety masks."""
from types import MappingProxyType

import numpy as np

from . import FEATURES
from .controllers import legal_best
from .model import SmartSignal


VARIANTS = MappingProxyType({
    'queue_guard': MappingProxyType(dict(
        version='adaptive-v1', density_threshold=.12,
        guard_green_s=25, queue_margin=.01)),
    'pressure_ai': MappingProxyType(dict(
        version='adaptive-v1', ai_weight=.25)),
    'adaptive_pressure_ai': MappingProxyType(dict(
        version='adaptive-v1', density_low=.10, density_high=.25,
        maximum_ai_weight=.8)),
})


def _mask(value, count):
    """Copy a finite binary mask, rejecting silently coerced malformed values."""
    try:
        numeric = np.asarray(value, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid adaptive action mask') from exc
    if (numeric.shape != (count,) or not np.isfinite(numeric).all()
            or not np.isin(numeric, (0, 1)).all() or not numeric.any()):
        raise ValueError('Invalid adaptive action mask')
    return numeric.astype(bool, copy=True)


def _normalize_legal(values, mask):
    """Center and scale legal scores; illegal candidates cannot alter the scale."""
    values = np.asarray(values, dtype=float)
    if values.shape != mask.shape or not np.isfinite(values).all() or not mask.any():
        raise ValueError('Invalid adaptive scores')
    legal = values[mask].copy()
    # Rescale first so finite but very large raw values cannot overflow the mean.
    magnitude = float(np.max(np.abs(legal)))
    if magnitude:
        legal /= magnitude
    legal -= legal.mean()
    radius = float(np.max(np.abs(legal)))
    if radius:
        legal /= radius
    normalized = np.zeros(values.shape, dtype=float)
    normalized[mask] = legal
    return normalized


class AdaptiveSmartSignal(SmartSignal):
    """Reuse trained phase scores with one fixed junction-independent rule."""

    def __init__(self, path, variant):
        if variant not in VARIANTS:
            raise ValueError(f'Unknown adaptive variant: {variant}')
        super().__init__(path)
        self.variant = variant
        self.config = VARIANTS[variant]
        self.last_decision = None

    def action(self, jid, observation, clock):
        features = np.asarray(observation['features'], dtype=float)
        if (features.ndim != 2 or features.shape[0] < 1
                or features.shape[1] != len(FEATURES)
                or not np.isfinite(features).all()):
            raise ValueError('Invalid adaptive phase features')
        count = len(features)
        mask = _mask(observation['mask'], count)
        if (len(clock.phases) != count
                or not isinstance(clock.current, (int, np.integer))
                or isinstance(clock.current, (bool, np.bool_))
                or not 0 <= clock.current < count):
            raise ValueError('Invalid adaptive current phase')
        pressure = np.asarray(observation['pressure'], dtype=float)
        if pressure.shape != (count,) or not np.isfinite(pressure).all():
            raise ValueError('Invalid adaptive pressure')
        q_scores = np.asarray(self.scores(features), dtype=float)
        if q_scores.shape != (count,) or not np.isfinite(q_scores).all():
            raise ValueError('Invalid adaptive AI scores')

        raw_ai_action = legal_best(q_scores, mask.copy())
        density = float(features[:, 1].mean())
        triggered, ai_weight = False, None
        if self.variant == 'queue_guard':
            action = raw_ai_action
            other = mask.copy()
            other[clock.current] = False
            triggered = bool(
                clock.stage == 'green'
                and clock.elapsed >= self.config['guard_green_s']
                and action == clock.current
                and density <= self.config['density_threshold']
                and np.any(features[other, 0] >= (
                    features[clock.current, 0] + self.config['queue_margin'])))
            if triggered:
                action = legal_best(pressure, mask.copy())
        else:
            p_norm = _normalize_legal(pressure, mask)
            q_norm = _normalize_legal(q_scores, mask)
            if self.variant == 'pressure_ai':
                ai_weight = self.config['ai_weight']
                combined = p_norm + ai_weight * q_norm
            else:
                config = self.config
                fraction = np.clip((density - config['density_low']) / (
                    config['density_high'] - config['density_low']), 0., 1.)
                ai_weight = config['maximum_ai_weight'] * float(fraction)
                combined = (1 - ai_weight) * p_norm + ai_weight * q_norm
            action = legal_best(combined, mask.copy())

        self.last_decision = dict(
            version=self.config['version'], variant=self.variant,
            action=action, raw_ai_action=raw_ai_action,
            density_mean=density, ai_weight=ai_weight,
            queue_guard_triggered=triggered)
        return action
