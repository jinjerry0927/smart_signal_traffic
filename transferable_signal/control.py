"""Deterministic safety transitions; contains no TraCI or learning code."""
import numpy as np


class SignalClock:
    def __init__(self, phases, limits):
        self.phases, self.limits = phases, limits
        self.current = 0
        self.pending = None
        self.stage = 'green'
        self.elapsed = 0
        self.remaining = 0
        self.red_age = np.zeros(len(phases), dtype=float)

    def mask(self):
        mask = np.zeros(len(self.phases), dtype=bool)
        if self.pending is not None:
            mask[self.pending] = True
        elif self.elapsed < self.limits['min_green_s']:
            mask[self.current] = True
        else:
            mask[:] = True
            age = self.red_age.copy()
            age[self.current] = -1
            if age.max() >= self.limits['max_red_s']:
                mask[:] = False
                mask[int(age.argmax())] = True
            elif self.elapsed >= self.limits['max_green_s']:
                mask[self.current] = False
        return mask

    def request(self, target):
        if not isinstance(target, (int, np.integer)) or not 0 <= target < len(self.phases) or not self.mask()[target]:
            raise ValueError(f"Illegal signal request: {target}")
        if self.pending is None and target != self.current:
            self.pending = target
            self.stage = 'yellow'
            self.remaining = self.limits['yellow_s']

    def tick(self):
        self.red_age += 1
        if self.stage == 'green':
            self.elapsed += 1
            self.red_age[self.current] = 0
        else:
            self.remaining -= 1
            if self.remaining == 0:
                if self.stage == 'yellow':
                    self.stage = 'all_red'
                    self.remaining = self.limits['all_red_s']
                else:
                    self.current, self.pending = self.pending, None
                    self.stage, self.elapsed = 'green', 0
                    self.red_age[self.current] = 0

    def state(self):
        state = self.phases[self.current]['state']
        if self.stage == 'yellow':
            return state.replace('G', 'y')
        if self.stage == 'all_red':
            return 'r' * len(state)
        return state
