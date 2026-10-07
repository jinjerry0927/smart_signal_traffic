"""Replay one frozen adaptive run with read-only second/decision diagnostics."""
import argparse
import csv
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from transferable_signal import FEATURES, runner
from transferable_signal.adaptive import AdaptiveSmartSignal, VARIANTS
from transferable_signal.environment import SignalEnvironment
from transferable_signal.spec import digest, read_json, write_json
from transferable_signal.suite import load_suite
from tools.run_adaptive_wave import SafetyHold, audit_run, warning_signatures

DEFAULT_SUITE = ROOT / 'network/transferable_reliability/suite.json'
DEFAULT_WAVE = ROOT / 'results/runs/adaptive_wave1_20261004'
LATENCY_METRICS = {'mean_decision_ms_per_junction', 'p95_decision_ms_per_junction'}


def compare_metrics(actual, reference):
    """Logging affects wall-clock decision time only; all traffic results must match."""
    if set(actual) != set(reference):
        raise ValueError('Diagnostic and original metric keys differ')
    for key in sorted(set(actual) - LATENCY_METRICS):
        value, expected = actual[key], reference[key]
        if isinstance(value, (float, int)) and not isinstance(value, bool):
            matches = (isinstance(expected, (float, int))
                       and math.isfinite(value) and math.isfinite(expected)
                       and math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-8))
        else:
            matches = value == expected
        if not matches:
            raise ValueError(f'Diagnostic changes original traffic metric: {key}: {value} != {expected}')


class AdaptiveDiagnosticEnvironment(SignalEnvironment):
    active = None
    focus_vehicle = None
    focus_start = 145.
    focus_end = 175.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.diagnostic_stream = self.decision_stream = self.focus_stream = None
        self.last_diagnostic_time = None
        self.checked_seconds = 0
        self.decision_count = 0
        self.focus_count = 0
        self.warning_details = set()
        AdaptiveDiagnosticEnvironment.active = self

    def _streams(self):
        if self.diagnostic_stream is not None:
            return
        self.diagnostic_stream = (self.output / 'diagnostic_seconds.csv').open(
            'w', newline='', encoding='utf-8')
        self.diagnostic_writer = csv.writer(self.diagnostic_stream)
        self.diagnostic_writer.writerow([
            'time_s', 'junction', 'current_phase', 'stage', 'green_elapsed_s',
            'actual_state', 'phase', 'red_age_s', 'queued_vehicles', 'vehicles',
            'mask', 'pressure', 'critical_ratio', *FEATURES])

    def _focus(self, now):
        if not self.focus_vehicle or not self.focus_start <= now <= self.focus_end:
            return
        if self.focus_stream is None:
            self.focus_stream = (self.output / 'vehicle_focus.jsonl').open('w', encoding='utf-8')
        vehicles = self.connection.vehicle
        vid = self.focus_vehicle
        record = dict(time_s=now, vehicle=vid, present=vid in vehicles.getIDList())
        if record['present']:
            leader = vehicles.getLeader(vid, 100.)
            leader_id, gap = leader if leader is not None else ('', -1.)
            record.update(lane=vehicles.getLaneID(vid), speed=vehicles.getSpeed(vid),
                          acceleration=vehicles.getAcceleration(vid),
                          lane_position_m=vehicles.getLanePosition(vid),
                          leader_id=leader_id, leader_gap_m=gap,
                          leader_speed=vehicles.getSpeed(leader_id) if leader_id else None)
            for key, value in record.items():
                if isinstance(value, (int, float)) and not math.isfinite(value):
                    raise SafetyHold(f'Invalid focus vehicle value at {now}s: {key}')
        self.focus_stream.write(json.dumps(record, allow_nan=False) + '\n')
        self.focus_stream.flush()
        self.focus_count += 1

    def _check_warnings(self):
        for name in ('sumo-errors.log', 'sumo.log'):
            path = self.output / name
            if path.exists():
                self.warning_details.update(warning_signatures(path))

    def observe(self):
        result = super().observe()
        now = self.connection.simulation.getTime()
        if now == self.last_diagnostic_time:
            return result
        self._streams()
        for junction in self.spec['junctions']:
            jid, count = junction['id'], len(junction['phases'])
            clock, value = self.clocks[jid], result[jid]
            features = np.asarray(value['features'])
            mask = np.asarray(value['mask'])
            pressure = np.asarray(value['pressure'])
            critical = np.asarray(value['critical_ratio'])
            if (features.shape != (count, len(FEATURES)) or not np.isfinite(features).all()
                    or mask.shape != (count,) or mask.dtype != np.dtype(bool) or not mask.any()
                    or not np.array_equal(mask, clock.mask())
                    or pressure.shape != (count,) or not np.isfinite(pressure).all()
                    or critical.shape != (count,) or not np.isfinite(critical).all()
                    or not np.isfinite(value['queue_ratio'])
                    or clock.current not in range(count)
                    or clock.stage not in ('green', 'yellow', 'all_red')
                    or not np.isfinite(clock.elapsed) or not np.isfinite(clock.red_age).all()):
                raise SafetyHold(f'Invalid diagnostic observation/clock at {now}s: {jid}')
            actual = self.connection.trafficlight.getRedYellowGreenState(jid)
            if actual != clock.state():
                raise SafetyHold(f'SUMO state differs from clock at {now}s: {jid}')
            for index, phase in enumerate(junction['phases']):
                samples = [self.observer.samples[lane] for lane in phase['incoming']]
                self.diagnostic_writer.writerow([
                    now, jid, clock.current, clock.stage, clock.elapsed, actual,
                    index, clock.red_age[index], sum(s['queue'] for s in samples),
                    sum(s['vehicles'] for s in samples), bool(mask[index]),
                    pressure[index], critical[index], *features[index]])
        self.diagnostic_stream.flush()
        self._focus(now)
        self.checked_seconds += 1
        self.last_diagnostic_time = now
        # Keep the affected second's read-only trace before stopping on any new
        # warning type. Emergency-braking lines are retained, never hidden.
        self._check_warnings()
        if self.metrics.teleports or self.metrics.collisions:
            raise SafetyHold(f'Collision or teleport observed at {now}s')
        return result

    def close(self):
        try:
            super().close()
        finally:
            for stream in (self.diagnostic_stream, self.decision_stream, self.focus_stream):
                if stream is not None and not stream.closed:
                    stream.close()


class AdaptiveDiagnosticPolicy(AdaptiveSmartSignal):
    def action(self, jid, observation, clock):
        action = super().action(jid, observation, clock)
        env = AdaptiveDiagnosticEnvironment.active
        if env.decision_stream is None:
            env.decision_stream = (env.output / 'diagnostic_decisions.jsonl').open('w', encoding='utf-8')
        record = dict(time_s=env.connection.simulation.getTime(), junction=jid,
                      phase=clock.current, stage=clock.stage, chosen=action,
                      raw_ai=self.last_decision['raw_ai_action'],
                      green_elapsed_s=clock.elapsed, red_age_s=clock.red_age.tolist(),
                      scores=self.scores(observation['features']).tolist(),
                      pressure=observation['pressure'].tolist(),
                      features=observation['features'].tolist(),
                      mask=observation['mask'].tolist(), last_decision=dict(self.last_decision))
        env.decision_stream.write(json.dumps(record, allow_nan=False) + '\n')
        env.decision_stream.flush()
        env.decision_count += 1
        return action


def diagnose(suite_path, scenario_id, model_path, variant, output,
             focus_vehicle=None, focus_start=145., focus_end=175., reference_wave=DEFAULT_WAVE):
    if variant not in VARIANTS:
        raise ValueError(f'Unknown adaptive variant: {variant}')
    if (not math.isfinite(focus_start) or not math.isfinite(focus_end)
            or focus_start < 0 or focus_end < focus_start):
        raise ValueError('Invalid focus time window')
    suite_path, model_path = Path(suite_path).resolve(), Path(model_path).resolve()
    output, reference_wave = Path(output).resolve(), Path(reference_wave).resolve()
    suite, cases = load_suite(suite_path)
    found = [s for s in suite['scenarios'] if s['id'] == scenario_id]
    if len(found) != 1 or found[0]['split'] != 'validation':
        raise ValueError('Use exactly one known validation scenario')
    scenario = found[0]
    spec = cases[scenario['case']][0]
    policy = AdaptiveSmartSignal(model_path, variant)
    trained = policy.data['training'].get('training_network_sha256', [])
    if not trained or spec['network_sha256'] in trained:
        raise ValueError('Validation contamination or missing training provenance')
    seed, model_hash, suite_hash = policy.data['training']['seed'], digest(model_path), digest(suite_path)
    wave = read_json(reference_wave / 'manifest.json')
    fingerprint = wave['fingerprint']
    if (fingerprint['suite_sha256'] != suite_hash
            or fingerprint.get('adaptive_sha256') != digest(ROOT / 'transferable_signal/adaptive.py')
            or not any(m['seed'] == seed and m['sha256'] == model_hash for m in fingerprint['models'])):
        raise ValueError('Original wave suite/model/adaptive code differs')
    records = [r for r in wave['records'] if r['row']['scenario'] == scenario_id
               and r['row']['variant'] == variant and r['row']['model_seed'] == seed]
    if len(records) != 1:
        raise ValueError('Exactly one original wave result is required')
    relative = Path(records[0]['directory'])
    reference = (reference_wave / relative).resolve()
    if not reference.is_relative_to(reference_wave):
        raise ValueError('Original wave result path leaves the wave directory')
    annotation = dict(variant=variant, config=dict(VARIANTS[variant]))
    hashes = dict(fingerprint['core_sha256'], **{'adaptive.py': fingerprint['adaptive_sha256']})
    for name, expected in hashes.items():
        source = ROOT / ('evaluation_support.py' if name == 'evaluation_support.py' else f'transferable_signal/{name}')
        if digest(source) != expected:
            raise ValueError(f'Original wave code changed: {name}')
    runtime = {key: fingerprint[key] for key in ('sumo', 'python')}
    original, original_warnings = audit_run(reference, scenario, spec, suite_hash, 'smart',
                                           model_hash, hashes, runtime, annotation)
    if output.exists():
        raise FileExistsError(f'Diagnostic output already exists: {output}')
    original_provenance = runner.provenance

    def annotated_provenance(*args, **kwargs):
        return dict(original_provenance(*args, **kwargs), adaptive_policy=annotation,
                    diagnostic_tool_sha256=digest(__file__))

    with patch.object(AdaptiveDiagnosticEnvironment, 'focus_vehicle', focus_vehicle), \
            patch.object(AdaptiveDiagnosticEnvironment, 'focus_start', focus_start), \
            patch.object(AdaptiveDiagnosticEnvironment, 'focus_end', focus_end), \
            patch.object(runner, 'SignalEnvironment', AdaptiveDiagnosticEnvironment), \
            patch.object(runner, 'SmartSignal', lambda path: AdaptiveDiagnosticPolicy(path, variant)), \
            patch.object(runner, 'provenance', side_effect=annotated_provenance):
        try:
            result = runner.run(suite_path, scenario_id, 'smart', output, model_path)
            metrics, warnings = audit_run(output, scenario, spec, suite_hash, 'smart',
                                         model_hash, hashes, runtime, annotation)
            compare_metrics(metrics, original)
            env = AdaptiveDiagnosticEnvironment.active
            horizon = scenario['duration_s'] + scenario['drain_s']
            expected_rows = (horizon + 1) * sum(len(j['phases']) for j in spec['junctions'])
            with (output / 'diagnostic_seconds.csv').open(encoding='utf-8', newline='') as stream:
                rows = list(csv.DictReader(stream))
            if len(rows) != expected_rows or env.checked_seconds != horizon + 1:
                raise ValueError('Incomplete per-second diagnostic trace')
            actual_times = {float(row['time_s']) for row in rows}
            if actual_times != set(range(horizon + 1)):
                raise ValueError('Diagnostic trace has missing/extra seconds')
            if set(warnings) != set(original_warnings):
                raise ValueError('Diagnostic warning signatures differ from original wave')
            trips = ET.parse(output / 'tripinfo.xml').getroot().findall('tripinfo')
            completed = sum(float(t.get('arrival')) >= 0 for t in trips)
            verification = dict(status='verified', tool_sha256=digest(__file__), diagnostic_only=True,
                observation_action_reward_unchanged=True, decision_latency_includes_logging=True,
                reference_wave=str(reference_wave), reference_run=str(reference),
                original_metrics_sha256=digest(reference / 'metrics.json'),
                traffic_metrics_match=True, checked_seconds=env.checked_seconds,
                phase_rows=len(rows), decisions=env.decision_count,
                focus_vehicle=focus_vehicle, focus_start=focus_start, focus_end=focus_end,
                focus_samples=env.focus_count, raw_trips=len(trips), completed_raw_trips=completed,
                warning_details=warnings, unexpected_warning_count=0,
                teleport_events=metrics['teleport_events'], collision_vehicle_events=metrics['collision_vehicle_events'])
            write_json(output / 'diagnostics.json', verification)
            print(json.dumps(dict(status='verified', scenario=scenario_id, variant=variant, seed=seed,
                queue=result['mean_queue_vehicles'], arrived=result['arrived_vehicles'],
                scheduled=result['scheduled_vehicles'], checked_seconds=env.checked_seconds,
                emergency_braking=len(warnings), focus_samples=env.focus_count), ensure_ascii=False), flush=True)
            return verification
        except BaseException as exc:
            if output.exists():
                write_json(output / 'diagnostics.json', dict(status='failed', diagnostic_only=True,
                    tool_sha256=digest(__file__), reference_run=str(reference),
                    error=f'{type(exc).__name__}: {exc}'))
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--scenario', required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--variant', choices=list(VARIANTS), default='queue_guard')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--focus-vehicle')
    parser.add_argument('--focus-start', type=float, default=145.)
    parser.add_argument('--focus-end', type=float, default=175.)
    parser.add_argument('--reference-wave', type=Path, default=DEFAULT_WAVE)
    args = parser.parse_args()
    diagnose(args.suite, args.scenario, args.model, args.variant, args.out,
             args.focus_vehicle, args.focus_start, args.focus_end, args.reference_wave)


if __name__ == '__main__':
    main()
