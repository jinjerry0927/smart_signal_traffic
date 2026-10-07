"""Load the exact frozen release using only NumPy (also works on Pi)."""
import hashlib
import json
from pathlib import Path

from transferable_signal import FEATURES, VERSION
from transferable_signal.adaptive_v2 import AdaptiveSmartSignalV2, VARIANTS_V2
from transferable_signal.spec import DEFAULT_LIMITS, digest, read_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASE = ROOT / 'results/runs/final_signal_wave10_20261007/release'
MODEL_SHA = '8eca32d46b9cc285162cf0e70fceb1a8d696254bf72b6954d0a764a482c8b999'
PROFILE_SHA = 'a375c53e085959d9521bf6090b9bac85cf257133241fd28c07eeb82d158379f3'
VARIANT = 'load_gate_pressure_wait35'
INFERENCE_FILES = ('__init__.py', 'control.py', 'controllers.py', 'model.py',
                   'spec.py', 'adaptive.py', 'adaptive_v2.py')


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def load_release(directory=DEFAULT_RELEASE):
    directory = Path(directory).resolve()
    model, profile = directory / 'model.json', directory / 'policy.json'
    if digest(model) != MODEL_SHA or digest(profile) != PROFILE_SHA:
        raise ValueError('Frozen model or policy SHA256 differs')
    data = read_json(profile)
    if (data['version'] != VERSION or data['features'] != list(FEATURES)
            or data['limits'] != DEFAULT_LIMITS or data['variant'] != VARIANT
            or data['policy_config'] != dict(VARIANTS_V2[VARIANT])):
        raise ValueError('Frozen observation or policy contract differs')
    source_hashes = {}
    for name in INFERENCE_FILES:
        relative = 'transferable_signal/' + name
        actual = digest(ROOT / relative)
        if actual != data['source_sha256'][relative]:
            raise ValueError('Frozen inference source differs: ' + relative)
        source_hashes[relative] = actual
    policy = AdaptiveSmartSignalV2(model, VARIANT)
    if policy.data['training'].get('seed') != 43:
        raise ValueError('Frozen model seed differs')
    identity = dict(protocol='smart-signal-edge-v1', model_sha256=MODEL_SHA,
                    policy_sha256=PROFILE_SHA, variant=VARIANT, version=VERSION,
                    features=list(FEATURES), limits=DEFAULT_LIMITS.copy(),
                    source_sha256=source_hashes)
    identity['identity_sha256'] = canonical_hash(identity)
    return policy, identity
