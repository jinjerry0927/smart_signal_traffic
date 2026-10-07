"""Independent baselines, all constrained by the same SignalClock."""
import math
import numpy as np


def legal_best(scores, mask):
    scores = np.asarray(scores, dtype=float)
    if scores.shape != mask.shape or not np.isfinite(scores).all() or not mask.any():
        raise ValueError("Invalid scores or action mask")
    return int(np.where(mask, scores, -np.inf).argmax())


def webster_greens(ratios, limits):
    """Measured critical lane ratios; bounded Webster approximation, not an oracle."""
    n, total = len(ratios), float(sum(ratios))
    lo, hi, dt = (limits[k] for k in ('min_green_s', 'max_green_s', 'decision_s'))
    if total <= 0:
        return [limits['fixed_green_s']] * n
    lost = n * (limits['yellow_s'] + limits['all_red_s'])
    cycle = n * hi + lost if total >= .95 else (1.5 * lost + 5) / (1 - total)
    cycle = min(max(cycle, n * lo + lost), n * hi + lost)
    return [min(hi, max(lo, math.ceil((cycle - lost) * y / total / dt) * dt)) for y in ratios]


class Baseline:
    def __init__(self, name, limits):
        if name not in ('fixed', 'webster', 'max_pressure'):
            raise ValueError(name)
        self.name, self.limits, self.plans = name, limits, {}

    def action(self, jid, observation, clock):
        mask = observation['mask']
        if self.name == 'max_pressure':
            return legal_best(observation['pressure'], mask)
        if self.name == 'fixed':
            durations = [self.limits['fixed_green_s']] * len(clock.phases)
        else:
            if jid not in self.plans or (clock.stage == 'green' and clock.current == 0 and clock.elapsed == 0):
                self.plans[jid] = webster_greens(observation['critical_ratio'], self.limits)
            durations = self.plans[jid]
        target = clock.current if clock.elapsed < durations[clock.current] else (clock.current + 1) % len(clock.phases)
        if mask[target]:
            return target
        return legal_best(clock.red_age, mask)
