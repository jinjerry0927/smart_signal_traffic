"""Independent raw-trace arithmetic and policy/safety replay for unseen tests."""
import collections
import csv
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from tools.unseen_transfer_build import ROOT, BUNDLE
import numpy as np
from transferable_signal.spec import digest, read_json, write_json


class ReplayClock:
    """Separate replay implementation; does not call the production SignalClock."""
    def __init__(self, phases, limits):
        self.phases, self.limits = phases, limits
        self.current, self.pending = 0, None
        self.stage, self.elapsed, self.remaining = 'green', 0, 0
        self.red_age = [0.] * len(phases)

    def mask(self):
        n = len(self.phases)
        allowed = [False] * n
        if self.pending is not None:
            allowed[self.pending] = True
        elif self.elapsed < self.limits['min_green_s']:
            allowed[self.current] = True
        else:
            allowed = [True] * n
            others = [i for i in range(n) if i != self.current]
            oldest = max(others, key=lambda i: self.red_age[i])
            if self.red_age[oldest] >= self.limits['max_red_s']:
                allowed = [i == oldest for i in range(n)]
            elif self.elapsed >= self.limits['max_green_s']:
                allowed[self.current] = False
        return allowed

    def request(self, action):
        assert self.mask()[action], 'Illegal action in replay'
        if self.pending is None and action != self.current:
            self.pending, self.stage, self.remaining = action, 'yellow', self.limits['yellow_s']

    def tick(self):
        self.red_age = [v + 1 for v in self.red_age]
        if self.stage == 'green':
            self.elapsed += 1
            self.red_age[self.current] = 0.
        else:
            self.remaining -= 1
            if self.remaining == 0:
                if self.stage == 'yellow':
                    self.stage, self.remaining = 'all_red', self.limits['all_red_s']
                else:
                    self.current, self.pending = self.pending, None
                    self.stage, self.elapsed = 'green', 0
                    self.red_age[self.current] = 0.

    def snapshot(self):
        return dict(current=self.current, pending=self.pending, stage=self.stage, elapsed=self.elapsed,
                    remaining=self.remaining, red_age=self.red_age)

    def signal(self):
        state = self.phases[self.current]['state']
        return state if self.stage == 'green' else state.replace('G', 'y') if self.stage == 'yellow' else 'r' * len(state)


def model_scores(features, model):
    values = np.asarray(features, dtype=np.float32)
    context = np.tile(values.mean(axis=0), (len(values), 1))
    values = np.hstack([values, context])
    for index, layer in enumerate(model['layers']):
        values = np.matmul(values, np.asarray(layer['weights'], dtype=np.float32).T) + np.asarray(layer['bias'], dtype=np.float32)
        if index != len(model['layers']) - 1:
            values = np.where(values > 0, values, 0)
    return values[:, 0]


def best(values, mask):
    return max((i for i, ok in enumerate(mask) if ok), key=lambda i: (values[i], -i))


def replay_action(name, features, pressure, critical, clock, model, config, plans, jid):
    mask = clock.mask()
    if name == 'max_pressure':
        return best(pressure, mask)
    if name in ('fixed', 'webster'):
        if name == 'fixed':
            durations = [clock.limits['fixed_green_s']] * len(mask)
        else:
            if jid not in plans or (clock.stage == 'green' and clock.current == 0 and clock.elapsed == 0):
                # Preserve the frozen baseline's ndarray scalar arithmetic.
                # Python 3.12's compensated float-list sum can move a ceil
                # boundary by five seconds compared with np.float64 inputs.
                critical = np.asarray(critical, dtype=float)
                total, n = float(sum(critical)), len(mask)
                lo, hi, dt = (clock.limits[k] for k in ('min_green_s', 'max_green_s', 'decision_s'))
                if total <= 0:
                    plans[jid] = [clock.limits['fixed_green_s']] * n
                else:
                    lost = n * (clock.limits['yellow_s'] + clock.limits['all_red_s'])
                    cycle = n * hi + lost if total >= .95 else (1.5 * lost + 5) / (1 - total)
                    cycle = min(max(cycle, n * lo + lost), n * hi + lost)
                    plans[jid] = [min(hi, max(lo, math.ceil((cycle - lost) * v / total / dt) * dt)) for v in critical]
            durations = plans[jid]
        target = clock.current if clock.elapsed < durations[clock.current] else (clock.current + 1) % len(mask)
        return target if mask[target] else best(clock.red_age, mask)
    scores = pressure if name == 'rules_pressure' else model_scores(features, model)
    raw = best(scores, mask)
    if name == 'raw_smart':
        return raw
    f = np.asarray(features, dtype=float)
    density = float(f[0, 1] + sum(f[:, 1] - f[0, 1]) / len(f))
    others = [i for i, ok in enumerate(mask) if ok and i != clock.current]
    queue_guard = (clock.stage == 'green' and clock.elapsed >= config['guard_green_s']
                   and raw == clock.current and density <= config['guard_density_threshold']
                   and any(f[i, 0] >= f[clock.current, 0] + config['guard_queue_margin'] for i in others))
    action = best(pressure, mask) if queue_guard else raw
    low = density <= config['density_threshold']
    if low:
        action = best(pressure, mask)
    candidates = [i in others and f[i, 0] >= config['wait_queue_ratio'] and f[i, 4] >= config['wait_red_ratio'] for i in range(len(mask))]
    if (not low and clock.stage == 'green' and clock.elapsed >= config['wait_green_s']
            and action == clock.current and any(candidates)):
        action = best(pressure, candidates)
    return action


class RawObserver:
    def __init__(self, spec):
        self.spec, self.previous, self.events, self.rates = spec, {}, {}, {}

    def sample(self, frame):
        edges = {}
        for lid, lane in self.spec['lanes'].items():
            edges.setdefault(lane['edge'], set()).update(frame['lanes'][lid][2])
        now, window = frame['time_s'], self.spec['limits']['arrival_window_s']
        for edge, ids in edges.items():
            events = self.events.setdefault(edge, collections.deque())
            if edge in self.previous:
                events.append((now, len(ids - self.previous[edge])))
            self.previous[edge] = ids
            while events and events[0][0] <= now - window:
                events.popleft()
            self.rates[edge] = sum(n for _, n in events) * 3600 / max(1, min(window, now))

    def features(self, frame, junction, clock):
        lanes, raw = self.spec['lanes'], frame['lanes']
        incoming = sorted({m['incoming'] for m in junction['movements']})
        counts = collections.Counter(lanes[l]['edge'] for l in incoming)
        receiver = {l: sorted({m['outgoing'] for m in junction['movements'] if m['incoming'] == l}) for l in incoming}
        mean = lambda v: sum(v) / len(v)
        features, pressures, critical = [], [], []
        for index, phase in enumerate(junction['phases']):
            source = phase['incoming']
            queue = [raw[l][0] / lanes[l]['capacity_vehicles'] for l in source]
            density = [raw[l][1] / lanes[l]['capacity_vehicles'] for l in source]
            arrival = [self.rates[lanes[l]['edge']] / counts[lanes[l]['edge']] / self.spec['limits']['saturation_vph_per_lane'] for l in source]
            downstream = [mean([raw[d][1] / lanes[d]['capacity_vehicles'] for d in receiver[l]]) for l in source]
            active = index == clock.current and clock.stage == 'green'
            features.append([mean(queue), mean(density), mean(arrival), mean(downstream),
                             clock.red_age[index] / self.spec['limits']['max_red_s'],
                             clock.elapsed / self.spec['limits']['max_green_s'] if active else 0, float(active)])
            pressures.append(sum(raw[l][0] - mean([raw[d][0] for d in receiver[l]]) for l in source))
            critical.append(max(arrival, default=0))
        return np.clip(np.asarray(features, dtype=np.float32), 0, 2), pressures, critical


def close_number(actual, expected, name):
    if expected is None:
        assert actual is None, name
    else:
        assert math.isfinite(actual) and math.isclose(actual, expected, abs_tol=1e-6, rel_tol=1e-7), (name, actual, expected)


def verify_run(output, spec, fingerprint=None, write=True, verification_name='verification.json'):
    output = Path(output)
    metadata, metrics = read_json(output / 'manifest.json'), read_json(output / 'metrics.json')
    assert metadata['status'] == metrics['status'] == 'completed', 'Run did not complete'
    if fingerprint is not None:
        assert metadata['fingerprint'] == fingerprint, 'Run provenance differs'
    for name, expected in metadata['files_sha256'].items():
        assert digest(output / name) == expected, 'Run output changed: ' + name
    scenario = metadata['scenario']
    horizon = scenario['duration_s'] + scenario['drain_s']
    model = read_json(ROOT / 'results/runs/final_signal_wave10_20261007/release/model.json')
    config = read_json(BUNDLE / 'protocol.json')['policy_config']
    clocks = {j['id']: ReplayClock(j['phases'], spec['limits']) for j in spec['junctions']}
    observer, plans = RawObserver(spec), {}
    decisions = {}
    with (output / 'decisions.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            record = json.loads(line)
            assert record['time_s'] not in decisions
            decisions[record['time_s']] = record
    assert set(decisions) == set(range(0, horizon, 5)), 'Missing/extra decision timestamps'
    arrived, departed, waits, queues, signal_records = set(), set(), [], [], []
    frame_count = action_count = 0
    with (output / 'frames.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            frame = json.loads(line)
            now = frame['time_s']
            assert now == frame_count and now <= horizon, 'Missing/extra raw frame'
            observer.sample(frame)
            assert not frame['collisions'] and not frame['teleports'], 'Unsafe raw event'
            assert set(frame['clocks']) == set(frame['observations']) == set(clocks)
            for junction in spec['junctions']:
                jid, clock = junction['id'], clocks[junction['id']]
                assert frame['clocks'][jid] == clock.snapshot(), ('Clock replay differs', now, jid)
                assert frame['signals'][jid] == clock.signal(), ('Signal replay differs', now, jid)
                observed = frame['observations'][jid]
                features, pressure, critical = observer.features(frame, junction, clock)
                np.testing.assert_allclose(observed['features'], features, atol=1e-6, rtol=1e-6)
                np.testing.assert_allclose(observed['pressure'], pressure, atol=1e-7)
                np.testing.assert_allclose(observed['critical_ratio'], critical, atol=1e-7)
                assert observed['mask'] == clock.mask(), 'Legal action mask differs'
                if now and now % 5 == 0:
                    signal_records.append((now, jid, clock.current, clock.stage, clock.elapsed, frame['signals'][jid]))
                if now in decisions:
                    decision = decisions[now]['junctions'][jid]
                    assert decision['clock'] == clock.snapshot()
                    np.testing.assert_allclose(decision['features'], features, atol=1e-6, rtol=1e-6)
                    np.testing.assert_allclose(decision['pressure'], pressure, atol=1e-7)
                    assert decision['mask'] == clock.mask()
                    # Raw reconstruction is checked above with float32-aware
                    # tolerance. Replay the exact serialized controller inputs,
                    # so harmless summation-order rounding cannot create a
                    # fictitious argmax/ceil discrepancy.
                    exact_features = np.asarray(observed['features'], dtype=np.float32)
                    exact_pressure = np.asarray(observed['pressure'], dtype=float)
                    exact_critical = np.asarray(observed['critical_ratio'], dtype=float)
                    expected = replay_action(metadata['controller'], exact_features, exact_pressure, exact_critical, clock, model, config, plans, jid)
                    assert decision['action'] == expected, ('Controller replay differs', now, jid, decision['action'], expected)
                    if decision['scores'] is not None:
                        scores = exact_pressure if metadata['controller'] == 'rules_pressure' else model_scores(exact_features, model)
                        np.testing.assert_allclose(decision['scores'], scores, atol=1e-6, rtol=1e-6)
                    clock.request(expected)
                    action_count += 1
            if now:
                departed.update(frame['departed'])
                arrived.update(frame['arrived'])
                queues.append(sum(v[0] < .1 for v in frame['vehicles'].values()))
                waits.append(sum(v[1] for v in frame['vehicles'].values()))
            for clock in clocks.values():
                clock.tick()
            frame_count += 1
    assert frame_count == horizon + 1
    with (output / 'signals.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == len(signal_records)
    for row, expected in zip(rows, signal_records):
        actual = (int(float(row['time_s'])), row['junction'], int(row['phase']), row['stage'], int(row['green_elapsed_s']), row['state'])
        assert actual == expected, 'Production signal CSV differs from raw replay'
    trips = [t for t in ET.parse(output / 'tripinfo.xml').getroot().findall('tripinfo') if float(t.get('arrival')) >= 0]
    assert {t.get('id') for t in trips} == arrived and arrived <= departed
    mean = lambda v: sum(v) / len(v) if v else None
    recomputed = dict(mean_queue_vehicles=mean(queues), max_queue_vehicles=max(queues, default=0),
        mean_system_total_waiting_time_s=mean(waits), sample_seconds=horizon,
        arrived_vehicles=len(arrived), departed_vehicles=len(departed), scheduled_vehicles=scenario['scheduled'],
        not_departed_vehicles=scenario['scheduled'] - len(departed), departed_not_arrived_vehicles=len(departed - arrived),
        running_vehicles_end=len(frame['vehicles']), pending_vehicles_end=len(frame['pending']),
        collision_vehicle_events=0, teleport_events=0,
        mean_completed_waiting_time_s=mean([float(t.get('waitingTime')) for t in trips]),
        mean_completed_travel_time_s=mean([float(t.get('duration')) for t in trips]),
        mean_completed_time_loss_s=mean([float(t.get('timeLoss')) for t in trips]),
        p95_completed_waiting_time_s=float(np.percentile([float(t.get('waitingTime')) for t in trips], 95)) if trips else None)
    for key, value in recomputed.items():
        close_number(metrics[key], value, key)
    warnings = [line for line in (output / 'sumo-errors.log').read_text(encoding='utf-8').splitlines() if 'Warning:' in line]
    assert metrics['warnings'] == warnings
    assert 'Error:' not in (output / 'sumo-errors.log').read_text(encoding='utf-8')
    record = dict(status='PASS', raw_seconds=horizon, frames=frame_count, actions=action_count,
                  warnings=warnings, recomputed_metrics=recomputed,
                  manifest_sha256=digest(output / 'manifest.json'), verification_source_sha256=digest(Path(__file__)))
    if write:
        write_json(output / verification_name, record)
    return record
