"""Opt-in TraCI adapter. Importing or checking contracts never starts SUMO."""
import csv
import uuid
import numpy as np
import sumolib
import traci

from transferable_signal.environment import Observations, SignalEnvironment
from .control import MovementClock
from .spec import VERSION


class MovementObservations(Observations):
    def junction(self, junction, clock):
        limits, lanes = self.spec['limits'], self.spec['lanes']
        incoming = sorted({m['incoming'] for m in junction['movements']})
        counts = {lanes[l]['edge']: sum(lanes[k]['edge'] == lanes[l]['edge'] for k in incoming) for l in incoming}
        features, pressures, flows = [], [], []
        for i, phase in enumerate(junction['phases']):
            source = phase['incoming']
            # Red left-turn receivers must not contaminate the through candidate.
            downstream = {l: {m['outgoing'] for m in junction['movements']
                             if m['incoming'] == l and phase['state'][m['index']] == 'G'} for l in source}
            queue = [self.samples[l]['queue'] / lanes[l]['capacity_vehicles'] for l in source]
            density = [self.samples[l]['vehicles'] / lanes[l]['capacity_vehicles'] for l in source]
            arrival = [self.rates[lanes[l]['edge']] / counts[lanes[l]['edge']] / limits['saturation_vph_per_lane'] for l in source]
            receiver = [np.mean([self.samples[d]['vehicles'] / lanes[d]['capacity_vehicles'] for d in downstream[l]]) for l in source]
            pressure = sum(self.samples[l]['queue'] - np.mean([self.samples[d]['queue'] for d in downstream[l]]) for l in source)
            active = i == clock.current and clock.stage == 'green'
            elapsed = max(clock.green_age[clock.greens[i]]) if active else 0
            features.append([np.mean(queue), np.mean(density), np.mean(arrival), np.mean(receiver),
                             clock.red_age[i] / limits['max_red_s'], elapsed / limits['max_green_s'], float(active)])
            pressures.append(pressure)
            flows.append(max(arrival, default=0))
        return dict(features=np.clip(np.asarray(features, dtype=np.float32), 0, 2), mask=clock.mask(),
                    pressure=np.asarray(pressures), critical_ratio=np.asarray(flows),
                    queue_ratio=float(np.mean([self.samples[l]['queue'] / lanes[l]['capacity_vehicles'] for l in incoming])))


class RealisticEnvironment(SignalEnvironment):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.spec['version'] != VERSION:
            raise ValueError('RealisticEnvironment requires a Realistic V2 contract')
        self.observer = MovementObservations(self.spec)

    def reset(self):
        if self.connection is not None:
            raise RuntimeError('Create a new environment for each episode')
        self.output.mkdir(parents=True, exist_ok=True)
        self.log = (self.output / 'sumo.log').open('w', encoding='utf-8')
        label = 'realistic-v2-' + uuid.uuid4().hex
        command = [sumolib.checkBinary('sumo-gui' if self.gui else 'sumo'), '-n', str(self.network),
                   '-r', str(self.routes), '--seed', str(self.seed), '--step-length', '1', '--end', str(self.horizon_s),
                   '--no-step-log', 'true', '--duration-log.disable', 'true',
                   '--tripinfo-output', str(self.output / 'tripinfo.xml'), '--tripinfo-output.write-unfinished', 'true',
                   '--error-log', str(self.output / 'sumo-errors.log')]
        if self.gui:
            command.extend(['--start', '--quit-on-end'])
            for name, flag in [('view.xml', '--gui-settings-file'), ('labels.add.xml', '--additional-files')]:
                if (self.network.parent / name).exists():
                    command.extend([flag, str(self.network.parent / name)])
        try:
            traci.start(command, label=label, stdout=self.log, doSwitch=False)
            self.connection = traci.getConnection(label)
            self.clocks = {j['id']: MovementClock(j, self.spec['limits']) for j in self.spec['junctions']}
            for jid, clock in self.clocks.items():
                self.connection.trafficlight.setRedYellowGreenState(jid, clock.state())
            self.trace_file = (self.output / 'signals.csv').open('w', newline='', encoding='utf-8')
            self.trace = csv.writer(self.trace_file)
            self.trace.writerow(['time_s', 'junction', 'phase', 'stage', 'green_elapsed_s', 'queue_ratio', 'state',
                                 'pending', 'remaining_s', 'requested_action', 'forced_changes'])
            self.observer.sample(self.connection)
            return self.observe()
        except Exception:
            self.close()
            raise

    def step(self, actions):
        if set(actions) != set(self.clocks):
            raise ValueError('An action is required for each junction')
        for jid, target in actions.items():
            clock = self.clocks[jid]
            if (isinstance(target, (bool, np.bool_)) or not isinstance(target, (int, np.integer))
                    or not 0 <= target < len(clock.phases) or not clock.mask()[target]):
                raise ValueError('Illegal Realistic V2 action: ' + jid)
        for jid, target in actions.items():
            self.clocks[jid].request(target)
        accumulated = {jid: 0.0 for jid in self.clocks}
        steps = 0
        for second in range(self.spec['limits']['decision_s']):
            now = self.connection.simulation.getTime()
            if now >= self.horizon_s:
                break
            for jid, clock in self.clocks.items():
                self.connection.trafficlight.setRedYellowGreenState(jid, clock.state())
                # Each row describes the actual signal for [time_s, time_s+1).
                self.trace.writerow([now, jid, clock.current, clock.stage, clock.elapsed,
                                     self.observe_queue(jid), clock.state(), clock.pending, clock.remaining,
                                     actions[jid] if second == 0 else '', clock.forced_changes])
            self.trace_file.flush()
            self.connection.simulationStep()
            self.metrics.sample(self.connection)
            if self.metrics.collisions or self.metrics.teleports:
                raise RuntimeError('Collision or teleport observed; Realistic V2 run stopped')
            self.observer.sample(self.connection)
            for jid, clock in self.clocks.items():
                clock.tick()
                self.connection.trafficlight.setRedYellowGreenState(jid, clock.state())
            for jid, value in self.observe().items():
                accumulated[jid] -= value['queue_ratio']
            steps += 1
        return self.observe(), {j: r / max(1, steps) for j, r in accumulated.items()}, self.connection.simulation.getTime() >= self.horizon_s

    def observe_queue(self, jid):
        j = next(j for j in self.spec['junctions'] if j['id'] == jid)
        return self.observer.junction(j, self.clocks[jid])['queue_ratio']
