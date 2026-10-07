"""Run one real, frozen validation case with read-only per-second diagnostics."""
import argparse
import csv
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import runner
from transferable_signal.environment import SignalEnvironment
from transferable_signal.model import SmartSignal
from transferable_signal.spec import digest, write_json
from transferable_signal.suite import load_suite


class DiagnosticEnvironment(SignalEnvironment):
    active = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostic_stream = None
        self.decision_stream = None
        self.last_diagnostic_time = None
        DiagnosticEnvironment.active = self

    def observe(self):
        result = super().observe()
        now = self.connection.simulation.getTime()
        if now == self.last_diagnostic_time:
            return result
        if self.metrics.teleports or self.metrics.collisions:
            raise RuntimeError(f'Stopped at {now}s: collision or teleport')
        error_log = self.output / 'sumo-errors.log'
        if error_log.exists() and error_log.stat().st_size:
            raise RuntimeError(f'Stopped at {now}s: SUMO warning/error; see {error_log}')
        if self.diagnostic_stream is None:
            self.diagnostic_stream = (self.output / 'diagnostic_seconds.csv').open('w', newline='', encoding='utf-8')
            self.diagnostic_writer = csv.writer(self.diagnostic_stream)
            self.diagnostic_writer.writerow(['time_s', 'junction', 'current_phase', 'stage', 'green_elapsed_s',
                                             'actual_state', 'phase', 'red_age_s', 'queued_vehicles', 'vehicles', 'mask'])
        for junction in self.spec['junctions']:
            jid = junction['id']
            clock = self.clocks[jid]
            value = result[jid]
            if not np.isfinite(value['features']).all() or not value['mask'].any():
                raise RuntimeError(f'Invalid observation at {now}s: {jid}')
            actual = self.connection.trafficlight.getRedYellowGreenState(jid)
            if actual != clock.state():
                raise RuntimeError(f'SUMO state differs from requested signal at {now}s: {jid}')
            for index, phase in enumerate(junction['phases']):
                samples = [self.observer.samples[lane] for lane in phase['incoming']]
                self.diagnostic_writer.writerow([now, jid, clock.current, clock.stage, clock.elapsed, actual,
                                                 index, clock.red_age[index], sum(s['queue'] for s in samples),
                                                 sum(s['vehicles'] for s in samples), bool(value['mask'][index])])
        self.last_diagnostic_time = now
        return result

    def close(self):
        try:
            super().close()
        finally:
            for stream in (self.diagnostic_stream, self.decision_stream):
                if stream is not None:
                    stream.close()


class DiagnosticPolicy(SmartSignal):
    def action(self, jid, observation, clock):
        action = super().action(jid, observation, clock)
        env = DiagnosticEnvironment.active
        if env.decision_stream is None:
            env.decision_stream = (env.output / 'diagnostic_decisions.jsonl').open('w', encoding='utf-8')
        record = dict(time_s=env.connection.simulation.getTime(), junction=jid, phase=clock.current,
                      stage=clock.stage, chosen=action, green_elapsed_s=clock.elapsed,
                      scores=self.scores(observation['features']).tolist(),
                      features=observation['features'].tolist(), mask=observation['mask'].tolist())
        env.decision_stream.write(json.dumps(record, allow_nan=False) + '\n')
        return action


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=ROOT / 'network/transferable_reliability/suite.json')
    parser.add_argument('--scenario', default='valid_t2_quiet_s00')
    parser.add_argument('--controller', choices=['fixed', 'smart'], required=True)
    parser.add_argument('--model', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    suite, cases = load_suite(args.suite)
    scenario = next(s for s in suite['scenarios'] if s['id'] == args.scenario)
    if scenario['split'] != 'validation':
        raise ValueError('Diagnostic runs accept validation cases only')
    if args.controller == 'smart':
        if args.model is None:
            raise ValueError('--model is required for smart')
        policy = SmartSignal(args.model)
        if cases[scenario['case']][0]['network_sha256'] in policy.data['training']['training_network_sha256']:
            raise ValueError('Diagnostic validation network occurred in training')
    runner.SignalEnvironment = DiagnosticEnvironment
    runner.SmartSignal = DiagnosticPolicy
    result = runner.run(args.suite, args.scenario, args.controller, args.out, args.model)
    trips = ET.parse(args.out / 'tripinfo.xml').getroot().findall('tripinfo')
    assert len(trips) == result['departed_vehicles']
    assert sum(float(trip.get('arrival')) >= 0 for trip in trips) == result['arrived_vehicles']
    assert result['sample_seconds'] == scenario['duration_s'] + scenario['drain_s']
    assert not result['teleport_events'] and not result['collision_vehicle_events']
    assert not (args.out / 'sumo-errors.log').read_text(encoding='utf-8').strip()
    write_json(args.out / 'diagnostics.json', dict(status='verified', tool_sha256=digest(__file__),
               diagnostic_only=True, observation_action_reward_unchanged=True,
               decision_latency_includes_logging=True, completed_raw_trips=result['arrived_vehicles']))
    print(json.dumps({k: result[k] for k in ('scenario', 'controller', 'mean_queue_vehicles',
                     'arrived_vehicles', 'scheduled_vehicles', 'mean_completed_waiting_time_s',
                     'teleport_events', 'collision_vehicle_events')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
