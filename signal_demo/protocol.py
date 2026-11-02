"""Bounded HTTP inference protocol; SUMO and clocks are owned by the PC."""
import json
import math
import time
import uuid
from urllib import request
from urllib.parse import urlsplit

import numpy as np

from transferable_signal.control import SignalClock
from transferable_signal.spec import DEFAULT_LIMITS
from .explanation import capture, validate

MAX_BODY = 512 * 1024


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


def strict_json(value):
    def invalid(item):
        raise ValueError('Non-finite JSON value: ' + item)
    return json.loads(value, parse_constant=invalid)


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid ' + name)
    return value


def pack_frame(observations, clocks):
    result = {}
    for jid, obs in observations.items():
        clock = clocks[jid]
        result[jid] = dict(observation={k: v.tolist() if isinstance(v, np.ndarray) else v
                                       for k, v in obs.items()},
                          clock=dict(phases=clock.phases, limits=clock.limits,
                                     current=clock.current, pending=clock.pending,
                                     stage=clock.stage, elapsed=clock.elapsed,
                                     remaining=clock.remaining, red_age=clock.red_age.tolist()))
    return result


def unpack_junction(value):
    c, obs = value['clock'], value['observation']
    phases = c['phases']
    if (not isinstance(phases, list) or not 2 <= len(phases) <= 32
            or c['limits'] != DEFAULT_LIMITS):
        raise ValueError('Invalid phase count or safety limits')
    states = [p['state'] for p in phases]
    if any(not isinstance(s, str) or not s or len(s) > 256 or set(s) - {'G', 'r'} for s in states):
        raise ValueError('Invalid phase state')
    if len({len(s) for s in states}) != 1:
        raise ValueError('Unequal phase state lengths')
    n = len(phases)
    clock = SignalClock(phases, DEFAULT_LIMITS)
    clock.current = integer(c['current'], 'current phase', 0, n - 1)
    clock.elapsed = integer(c['elapsed'], 'green elapsed', 0, 65)
    clock.remaining = integer(c['remaining'], 'remaining', 0, 3)
    clock.pending = None if c['pending'] is None else integer(c['pending'], 'pending', 0, n - 1)
    clock.stage = c['stage']
    if clock.stage not in ('green', 'yellow', 'all_red'):
        raise ValueError('Invalid signal stage')
    if clock.stage == 'green':
        if clock.pending is not None or clock.remaining:
            raise ValueError('Invalid green clock')
    elif (clock.pending is None or clock.pending == clock.current or clock.remaining < 1
          or clock.remaining > DEFAULT_LIMITS['yellow_s' if clock.stage == 'yellow' else 'all_red_s']):
        raise ValueError('Invalid transition clock')
    clock.red_age = np.asarray(c['red_age'], dtype=float)
    if (clock.red_age.shape != (n,) or not np.isfinite(clock.red_age).all()
            or (clock.red_age < 0).any() or (clock.red_age > 10000).any()):
        raise ValueError('Invalid red age')
    features = np.asarray(obs['features'], dtype=np.float32)
    pressure = np.asarray(obs['pressure'], dtype=float)
    mask_raw = np.asarray(obs['mask'], dtype=float)
    if (features.shape != (n, 7) or not np.isfinite(features).all()
            or (features < 0).any() or (features > 2).any()
            or pressure.shape != (n,) or not np.isfinite(pressure).all()
            or mask_raw.shape != (n,) or not np.isin(mask_raw, (0, 1)).all()
            or not np.array_equal(mask_raw.astype(bool), clock.mask())):
        raise ValueError('Invalid observation or mismatched safety mask')
    # Clock-derived features must refer to the very same decision instant.
    for i, row in enumerate(features):
        active = i == clock.current and clock.stage == 'green'
        expected = [min(2, clock.red_age[i] / DEFAULT_LIMITS['max_red_s']),
                    clock.elapsed / DEFAULT_LIMITS['max_green_s'] if active else 0, float(active)]
        if not np.allclose(row[4:], expected, rtol=0, atol=1e-6):
            raise ValueError('Observation/clock timestamp mismatch')
    return dict(features=features, pressure=pressure, mask=clock.mask()), clock


def infer(policy, identity, payload):
    if payload.get('identity_sha256') != identity['identity_sha256']:
        raise ValueError('Model/contract identity differs')
    rid = payload.get('request_id')
    if not isinstance(rid, str) or not 1 <= len(rid) <= 80:
        raise ValueError('Invalid request ID')
    frame = payload.get('junctions')
    if (not isinstance(frame, dict) or not 1 <= len(frame) <= 16
            or any(not isinstance(j, str) or not 1 <= len(j) <= 80 for j in frame)):
        raise ValueError('Invalid junction IDs')
    decoded = {jid: unpack_junction(v) for jid, v in frame.items()}
    started = time.perf_counter()
    actions, diagnostics = {}, {}
    for jid, (obs, clock) in decoded.items():
        actions[jid] = policy.action(jid, obs, clock)
        diagnostics[jid] = capture(policy, obs, actions[jid])
    return dict(request_id=rid, identity_sha256=identity['identity_sha256'], actions=actions,
                inference_ms=(time.perf_counter() - started) * 1000, diagnostics=diagnostics)


class EdgeClient:
    def __init__(self, url, identity, timeout=1.0):
        if not isinstance(url, str):
            raise ValueError('Edge URL must be http://HOST:PORT')
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError('Edge URL must be http://HOST:PORT')
        self.url, self.identity, self.timeout = url.rstrip('/'), identity, timeout
        self.checked = False
        self.last_ms = None
        self.last_inference_ms = None
        self.last_diagnostics = {}
        self.diagnostic_error = None

    def _call(self, path, payload=None):
        req = request.Request(self.url + path, data=None if payload is None else json_bytes(payload),
                              headers={'Content-Type': 'application/json'})
        # Direct LAN connection; do not route Pi observations through a configured proxy.
        with request.build_opener(request.ProxyHandler({})).open(req, timeout=self.timeout) as response:
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise ValueError('Edge response too large')
            return strict_json(body)

    def check(self):
        health = self._call('/health')
        if (not isinstance(health, dict) or health.get('identity') != self.identity
                or not isinstance(health.get('hostname'), str) or not health['hostname']):
            raise ValueError('Edge has a different frozen model/contract')
        self.checked = True
        return health

    def action(self, observations, clocks):
        self.last_diagnostics, self.diagnostic_error = {}, None
        if not self.checked:
            self.check()
        rid = uuid.uuid4().hex
        started = time.perf_counter()
        reply = self._call('/infer', dict(request_id=rid, identity_sha256=self.identity['identity_sha256'],
                                         junctions=pack_frame(observations, clocks)))
        actions = reply.get('actions')
        if (reply.get('request_id') != rid or reply.get('identity_sha256') != self.identity['identity_sha256']
                or not isinstance(actions, dict) or set(actions) != set(clocks)):
            raise ValueError('Stale or mismatched edge response')
        for jid, action in actions.items():
            integer(action, 'edge action', 0, len(clocks[jid].phases) - 1)
            if not clocks[jid].mask()[action]:
                raise ValueError('Edge action violates safety mask')
        elapsed = reply.get('inference_ms')
        if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError('Invalid edge latency')
        self.last_ms = (time.perf_counter() - started) * 1000
        self.last_inference_ms = elapsed
        values = reply.get('diagnostics')
        if values is not None:
            try:
                if not isinstance(values, dict) or set(values) != set(actions):
                    raise ValueError('Diagnostic junctions differ')
                self.last_diagnostics = {jid: validate(values[jid], observations[jid], action)
                                         for jid, action in actions.items()}
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                self.last_diagnostics = {}
                self.diagnostic_error = str(exc)
        return actions
