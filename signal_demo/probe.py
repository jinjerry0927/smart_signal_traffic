"""Check a remote service against the local policy without running SUMO."""
from pathlib import Path
import time

import numpy as np

from transferable_signal.control import SignalClock
from transferable_signal.spec import DEFAULT_LIMITS, write_json
from .protocol import EdgeClient
from .release import DEFAULT_RELEASE, load_release


def probe(url, output, release=DEFAULT_RELEASE):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Probe output already exists: ' + str(output))
    policy, identity = load_release(release)
    client = EdgeClient(url, identity)
    health = client.check()
    rng = np.random.default_rng(43)
    clocks = {f'j{n}': SignalClock([dict(state='r' * i + 'G' + 'r' * (n - i - 1)) for i in range(n)],
                                dict(DEFAULT_LIMITS)) for n in (3, 4, 5)}
    delays, stages, records = [], set(), []
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
        expected = {jid: policy.action(jid, value, clocks[jid]) for jid, value in observations.items()}
        started = time.perf_counter()
        actual = client.action(observations, clocks)
        delays.append((time.perf_counter() - started) * 1000)
        if actual != expected:
            raise AssertionError(f'Remote/local action mismatch at frame {frame}')
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
                            stages=sorted(stages), mean_roundtrip_ms=float(np.mean(delays)),
                            p95_roundtrip_ms=float(np.percentile(delays, 95)), records=records))
    print(f'Remote parity passed: 300 actions, p95 roundtrip {np.percentile(delays, 95):.2f} ms', flush=True)
    return output
