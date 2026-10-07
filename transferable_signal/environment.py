"""TraCI adapter for variable-size junctions. Only reset performs a zero-time load."""
from collections import deque
import csv
from pathlib import Path
import uuid

import numpy as np
import sumolib
import traci

from evaluation_support import EpisodeMetrics
from .control import SignalClock


class Observations:
    def __init__(self, spec):
        self.spec = spec
        self.previous = {}
        self.entries = {}
        self.started = None
        self.samples = {}
        self.rates = {}

    def sample(self, connection):
        now = connection.simulation.getTime()
        if self.started is None:
            self.started = now
        edge_ids = {}
        for lid, data in self.spec['lanes'].items():
            self.samples[lid] = dict(queue=connection.lane.getLastStepHaltingNumber(lid),
                                     vehicles=connection.lane.getLastStepVehicleNumber(lid))
            edge_ids.setdefault(data['edge'], set()).update(connection.lane.getLastStepVehicleIDs(lid))
        window = self.spec['limits']['arrival_window_s']
        for edge, ids in edge_ids.items():
            events = self.entries.setdefault(edge, deque())
            if edge in self.previous:
                events.append((now, len(ids - self.previous[edge])))
            self.previous[edge] = ids
            while events and events[0][0] <= now - window:
                events.popleft()
            elapsed = max(1, min(window, now - self.started))
            self.rates[edge] = sum(n for _, n in events) * 3600 / elapsed

    def junction(self, junction, clock):
        limits, lanes = self.spec['limits'], self.spec['lanes']
        incoming = sorted({m['incoming'] for m in junction['movements']})
        lanes_per_edge = {}
        for lid in incoming:
            edge = lanes[lid]['edge']
            lanes_per_edge[edge] = lanes_per_edge.get(edge, 0) + 1
        downstream = {lid: sorted({m['outgoing'] for m in junction['movements'] if m['incoming'] == lid}) for lid in incoming}
        features, pressures, flows = [], [], []
        for index, phase in enumerate(junction['phases']):
            source = phase['incoming']
            queue = [self.samples[l]['queue'] / lanes[l]['capacity_vehicles'] for l in source]
            density = [self.samples[l]['vehicles'] / lanes[l]['capacity_vehicles'] for l in source]
            arrival = [self.rates[lanes[l]['edge']] / lanes_per_edge[lanes[l]['edge']] /
                       limits['saturation_vph_per_lane'] for l in source]
            receiver = [np.mean([self.samples[d]['vehicles'] / lanes[d]['capacity_vehicles'] for d in downstream[l]]) for l in source]
            # Shared-lane destinations are equally weighted; no future route-file lookup.
            pressure = sum(self.samples[l]['queue'] - np.mean([self.samples[d]['queue'] for d in downstream[l]]) for l in source)
            active = index == clock.current and clock.stage == 'green'
            features.append([np.mean(queue), np.mean(density), np.mean(arrival), np.mean(receiver),
                             clock.red_age[index] / limits['max_red_s'],
                             clock.elapsed / limits['max_green_s'] if active else 0, float(active)])
            pressures.append(pressure)
            flows.append(max(arrival, default=0))
        return dict(features=np.clip(np.asarray(features, dtype=np.float32), 0, 2),
                    mask=clock.mask(), pressure=np.asarray(pressures), critical_ratio=np.asarray(flows),
                    queue_ratio=float(np.mean([self.samples[l]['queue'] / lanes[l]['capacity_vehicles'] for l in incoming])))


class SignalEnvironment:
    def __init__(self, spec, network, routes, output, horizon_s, seed=42, gui=False):
        self.spec, self.network, self.routes = spec, Path(network), Path(routes)
        self.output, self.horizon_s, self.seed, self.gui = Path(output), horizon_s, seed, gui
        self.connection = None
        self.log = None
        self.trace_file = None
        self.clocks = {}
        self.metrics = EpisodeMetrics()
        self.observer = Observations(spec)
        self.scheduled = 0
        import xml.etree.ElementTree as ET
        self.scheduled = len(ET.parse(self.routes).getroot().findall('vehicle'))

    def reset(self):
        if self.connection is not None:
            raise RuntimeError("Create a new environment for each episode")
        self.output.mkdir(parents=True, exist_ok=True)
        self.log = (self.output / 'sumo.log').open('w', encoding='utf-8')
        label = 'transferable-' + uuid.uuid4().hex
        command = [sumolib.checkBinary('sumo-gui' if self.gui else 'sumo'), '-n', str(self.network),
                   '-r', str(self.routes), '--seed', str(self.seed), '--step-length', '1',
                   '--end', str(self.horizon_s), '--no-step-log', 'true', '--duration-log.disable', 'true',
                   '--tripinfo-output', str(self.output / 'tripinfo.xml'), '--tripinfo-output.write-unfinished', 'true',
                   '--error-log', str(self.output / 'sumo-errors.log')]
        if self.gui and (self.network.parent / 'labels.add.xml').exists():
            command.extend(['--additional-files', str(self.network.parent / 'labels.add.xml')])
        if self.gui:
            command.extend(['--start', '--quit-on-end'])
            if (self.network.parent / 'view.xml').exists():
                command.extend(['--gui-settings-file', str(self.network.parent / 'view.xml')])
        try:
            traci.start(command, label=label, stdout=self.log, doSwitch=False)
            self.connection = traci.getConnection(label)
            for j in self.spec['junctions']:
                clock = SignalClock(j['phases'], self.spec['limits'])
                self.clocks[j['id']] = clock
                self.connection.trafficlight.setRedYellowGreenState(j['id'], clock.state())
            self.trace_file = (self.output / 'signals.csv').open('w', newline='', encoding='utf-8')
            self.trace = csv.writer(self.trace_file)
            self.trace.writerow(['time_s', 'junction', 'phase', 'stage', 'green_elapsed_s', 'queue_ratio', 'state'])
            self.observer.sample(self.connection)
            return self.observe()
        except Exception:
            self.close()
            raise

    def observe(self):
        return {j['id']: self.observer.junction(j, self.clocks[j['id']]) for j in self.spec['junctions']}

    def step(self, actions):
        if set(actions) != set(self.clocks):
            raise ValueError("An action is required for each junction")
        for jid, action in actions.items():
            if not isinstance(action, (int, np.integer)) or not 0 <= action < len(self.clocks[jid].phases) or not self.clocks[jid].mask()[action]:
                raise ValueError(f"Invalid action for {jid}")
        for jid, action in actions.items():
            self.clocks[jid].request(action)
            self.connection.trafficlight.setRedYellowGreenState(jid, self.clocks[jid].state())
        accumulated = {jid: 0.0 for jid in self.clocks}
        steps = 0
        for _ in range(self.spec['limits']['decision_s']):
            if self.connection.simulation.getTime() >= self.horizon_s:
                break
            self.connection.simulationStep()
            self.metrics.sample(self.connection)
            self.observer.sample(self.connection)
            for jid, clock in self.clocks.items():
                clock.tick()
                self.connection.trafficlight.setRedYellowGreenState(jid, clock.state())
            observation = self.observe()
            for jid, value in observation.items():
                accumulated[jid] -= value['queue_ratio']
            steps += 1
        observation = self.observe()
        now = self.connection.simulation.getTime()
        for jid, clock in self.clocks.items():
            self.trace.writerow([now, jid, clock.current, clock.stage, clock.elapsed,
                                 observation[jid]['queue_ratio'], clock.state()])
        return observation, {j: r / max(1, steps) for j, r in accumulated.items()}, now >= self.horizon_s

    def finish(self):
        running = self.connection.vehicle.getIDCount()
        pending = len(self.connection.simulation.getPendingVehicles())
        self.close()
        return self.metrics.finish(self.output / 'tripinfo.xml', running, pending, self.scheduled)

    def close(self):
        try:
            if self.connection is not None:
                self.connection.close()
        finally:
            self.connection = None
            if self.trace_file:
                self.trace_file.close()
            if self.log:
                self.log.close()
