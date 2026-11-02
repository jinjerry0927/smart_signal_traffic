"""Movement clearance and continuous-green limits, independent of TraCI and AI."""
import copy
import numpy as np

from transferable_signal.spec import validate_limits
from .spec import validate_junction, validate_state


class MovementClock:
    def __init__(self, junction, limits):
        validate_limits(limits)
        validate_junction(junction)
        self.junction, self.limits = copy.deepcopy(junction), dict(limits)
        self.phases = self.junction['phases']
        self.current, self.pending, self.stage = 0, None, 'green'
        self.elapsed, self.remaining = 0, 0
        self.greens = np.asarray([[s == 'G' for s in p['state']] for p in self.phases])
        self.green_age = np.zeros(self.greens.shape[1], dtype=int)
        self.movement_red_age = np.zeros(self.greens.shape[1], dtype=int)
        self.red_age = np.zeros(len(self.phases), dtype=float)
        self.forced_changes = 0

    def mask(self):
        mask = np.zeros(len(self.phases), dtype=bool)
        if self.pending is not None:
            mask[self.pending] = True
            return mask
        active = self.greens[self.current]
        clearance = self.limits['yellow_s'] + self.limits['all_red_s']
        lo, hi, dt = (self.limits[k] for k in ('min_green_s', 'max_green_s', 'decision_s'))
        for i, target in enumerate(self.greens):
            if i == self.current:
                mask[i] = bool(np.all(self.green_age[active] + dt <= hi))
            else:
                departing, retained = active & ~target, active & target
                mask[i] = (self.elapsed >= lo and np.all(self.green_age[departing] >= lo)
                           and np.all(self.green_age[retained] + clearance + lo <= hi))
        # Serve the oldest actual movement, not the last selected phase label.
        reserve = lo + clearance + dt
        oldest = int(self.movement_red_age.argmax())
        if self.movement_red_age[oldest] >= self.limits['max_red_s'] - reserve:
            serving = mask & self.greens[:, oldest]
            if serving.any():
                mask = serving
        if not mask.any():
            raise RuntimeError('No safe signal action; invalid timing/plan')
        return mask

    def request(self, target):
        if (isinstance(target, (bool, np.bool_)) or not isinstance(target, (int, np.integer))
                or not 0 <= target < len(self.phases) or not self.mask()[target]):
            raise ValueError(f'Illegal Realistic V2 signal request: {target}')
        if self.pending is None and target != self.current:
            self.pending, self.stage = int(target), 'yellow'
            self.remaining = self.limits['yellow_s']
        validate_state(self.junction, self.state())

    def state(self):
        before = self.phases[self.current]['state']
        if self.pending is None:
            return before
        after = self.phases[self.pending]['state']
        return ''.join('G' if a == b == 'G' else 'y' if a == 'G' and self.stage == 'yellow' else 'r'
                       for a, b in zip(before, after))

    def tick(self):
        state = self.state()
        active = np.asarray([s == 'G' for s in state])
        self.green_age = np.where(active, self.green_age + 1, 0)
        self.movement_red_age = np.where(active, 0, self.movement_red_age + 1)
        if self.stage == 'green':
            self.elapsed += 1
            # Fail-safe even if a caller forgets to request an action on time.
            if np.any(self.green_age[active] >= self.limits['max_green_s']):
                legal = np.flatnonzero(self.mask())
                target = max(legal, key=lambda i: (max(self.movement_red_age[self.greens[i]]), -i))
                self.request(int(target))
                self.forced_changes += 1
        else:
            self.remaining -= 1
            if self.remaining == 0:
                if self.stage == 'yellow':
                    self.stage, self.remaining = 'clearance', self.limits['all_red_s']
                else:
                    self.current, self.pending = self.pending, None
                    self.stage, self.elapsed = 'green', 0
        # A retained green never resets its timer when the phase label changes.
        now = np.asarray([s == 'G' for s in self.state()])
        self.green_age[~now] = 0
        self.movement_red_age[now] = 0
        self.red_age[:] = [max(self.movement_red_age[g]) for g in self.greens]
        validate_state(self.junction, self.state())
