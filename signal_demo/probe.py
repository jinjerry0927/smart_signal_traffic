"""Check a remote service against the local policy without running SUMO."""
from pathlib import Path
import time

import numpy as np

from transferable_signal.control import SignalClock
from transferable_signal.spec import DEFAULT_LIMITS, write_json
from .protocol import EdgeClient
from .explanation import capture
from .release import DEFAULT_RELEASE, load_release


def diagnostics_match(actual, expected):
    if set(actual) != set(expected):
        return False
    for jid, reference in expected.items():
        result = actual[jid]
        if result['version'] != reference['version']:
            return False
        # CPU/BLAS implementations may differ by a few float32 rounding units.
        if not np.allclose(result['scores'], reference['scores'], rtol=1e-5, atol=1e-6):
            return False
        a, b = result['decision'], reference['decision']
        if {k: v for k, v in a.items() if k != 'density_mean'} != {k: v for k, v in b.items() if k != 'density_mean'}:
            return False
        if not np.isclose(a['density_mean'], b['density_mean'], rtol=1e-10, atol=1e-10):
            return False
    return True


def probe(url, output, release=DEFAULT_RELEASE, require_diagnostics=False):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Probe output already exists: ' + str(output))
    policy, identity = load_release(release)
    client = EdgeClient(url, identity)
    health = client.check()
    rng = np.random.default_rng(43)
    clocks = {f'j{n}': SignalClock([dict(state='r' * i + 'G' + 'r' * (n - i - 1)) for i in range(n)],
                                dict(DEFAULT_LIMITS)) for n in (3, 4, 5)}
    delays, stages, records, diagnostic_matches = [], set(), [], 0
    for frame in range(100):
        observations = {}
        for jid, clock in clocks.items():
            n = len(clock.phases)
            f = rng.uniform(0, .4, (n, 7)).astype(np.float32)
            f[:, 4] = np.minimum(2, clock.red_age / DEFAULT_LIMITS['max_red_s'])
            f[:, 5:] = 0
            if clock.stage == 'green':
                f[clock.current, 5:] = [clock.elapsed / DEFAULT_LIMITS['max_green_s'], 1]
            observations[jid] = dict(features=f, pressure=rng.uniform(-5, 20, n), mask=clock.mask())
            stages.add(clock.stage)
        expected, expected_diagnostics = {}, {}
        for jid, value in observations.items():
            expected[jid] = policy.action(jid, value, clocks[jid])
            expected_diagnostics[jid] = capture(policy, value, expected[jid])
        started = time.perf_counter()
        actual = client.action(observations, clocks)
        delays.append((time.perf_counter() - started) * 1000)
        if actual != expected:
            raise AssertionError(f'Remote/local action mismatch at frame {frame}')
        if client.last_diagnostics:
            if not diagnostics_match(client.last_diagnostics, expected_diagnostics):
                raise AssertionError(f'Remote/local diagnostics mismatch at frame {frame}')
            diagnostic_matches += len(actual)
        elif require_diagnostics:
            raise AssertionError('Remote does not provide validated decision diagnostics')
        records.append(dict(frame=frame, expected=expected, actual=actual))
        for jid, clock in clocks.items():
            clock.request(actual[jid])
            # Probe all transition stages as well as ordinary decision instants.
            for _ in range(1 + frame % 5):
                clock.tick()
    if stages != {'green', 'yellow', 'all_red'}:
        raise AssertionError('All three safety stages must be covered')
    write_json(output, dict(status='passed', remote_url=url, remote_health=health,
                            identity=identity, frames=100, matched_actions=300,
                            matched_diagnostic_actions=diagnostic_matches,
                            diagnostic_score_rtol=1e-5, diagnostic_score_atol=1e-6,
                            stages=sorted(stages), mean_roundtrip_ms=float(np.mean(delays)),
                            p95_roundtrip_ms=float(np.percentile(delays, 95)), records=records))
    print(f'Remote parity passed: 300 actions, {diagnostic_matches} diagnostic matches, p95 roundtrip {np.percentile(delays, 95):.2f} ms', flush=True)
    return output
