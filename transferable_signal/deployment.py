"""Validation-selected deployment factory; never changes the raw policy globally."""
import hashlib
import json
from pathlib import Path

from . import VERSION, FEATURES
from .adaptive_v2 import AdaptiveSmartSignalV2, VARIANTS_V2
from .spec import DEFAULT_LIMITS, digest
from .suite import ROOT


PROFILE_PATH = ROOT / 'configs/smart_signal_default.json'
# Anchor the selected profile independently of its editable fields.
PROFILE_IDENTITY_SHA256 = '9cf9be01abf2099c14e47be139430300106c6bcc3786f92789bc3b8190877cfe'


def validated_policy(model_path=None):
    """Verify the frozen deployment contract before constructing V2 inference."""
    if not PROFILE_PATH.is_file():
        raise ValueError(f'Validated profile is missing: {PROFILE_PATH}')
    profile_bytes = PROFILE_PATH.read_bytes()
    profile_sha = hashlib.sha256(profile_bytes).hexdigest()
    profile = json.loads(profile_bytes)
    profile_identity_sha = hashlib.sha256(json.dumps(
        profile, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    if profile_identity_sha != PROFILE_IDENTITY_SHA256:
        raise ValueError('Validated profile SHA256 changed; restore the frozen profile')
    if profile['version'] != VERSION or profile['features'] != list(FEATURES):
        raise ValueError('Validated observation version/features changed')
    if profile['limits'] != DEFAULT_LIMITS:
        raise ValueError('Validated DEFAULT_LIMITS changed')
    variant = profile['variant']
    if variant not in VARIANTS_V2 or dict(VARIANTS_V2[variant]) != profile['policy_config']:
        raise ValueError('Validated policy variant/config changed')
    for relative, expected in profile['source_sha256'].items():
        source = ROOT / relative
        if not source.is_file() or digest(source) != expected:
            raise ValueError(f'Validated inference source SHA256 changed: {relative}')
    model_path = (ROOT / profile['model']['path'] if model_path is None else Path(model_path)).resolve()
    if not model_path.is_file():
        raise ValueError(f'Validated model is missing: {model_path}; restore seed43 or use --smart-policy raw --model PATH')
    model_sha = digest(model_path)
    if model_sha != profile['model']['sha256']:
        raise ValueError('Validated model SHA256 differs from seed43; use --smart-policy raw --model PATH for another model')
    policy = AdaptiveSmartSignalV2(model_path, variant)
    if policy.data['training'].get('seed') != profile['model']['seed']:
        raise ValueError('Validated model seed differs from the frozen selection')
    identity = dict(mode='validated', profile_version=profile['profile_version'],
                    profile_path=str(PROFILE_PATH.resolve()), profile_sha256=profile_sha,
                    profile_identity_sha256=profile_identity_sha,
                    seed=profile['model']['seed'], model_path=str(model_path), model_sha256=model_sha,
                    variant=variant, config=dict(policy.config), version=policy.config['version'],
                    features=profile['features'], limits=profile['limits'],
                    source_sha256=profile['source_sha256'], selection=profile['selection'],
                    factory_sha256=digest(Path(__file__)))
    return policy, model_path, identity
