"""Small shared phase scorer, with NumPy-only deployment inference."""
import numpy as np

from . import VERSION, FEATURES
from .spec import read_json
from .controllers import legal_best


def inputs(features):
    features = np.asarray(features, dtype=np.float32)
    if features.ndim != 2 or features.shape[1] != len(FEATURES) or not np.isfinite(features).all():
        raise ValueError("Invalid phase features")
    context = np.broadcast_to(features.mean(axis=0), features.shape)
    return np.concatenate((features, context), axis=1)


class SmartSignal:
    def __init__(self, path):
        self.data = read_json(path)
        if self.data.get('version') != VERSION or self.data.get('features') != list(FEATURES):
            raise ValueError("Model is incompatible; old single-intersection models cannot be used")
        if self.data.get('training', {}).get('updates', 0) <= 0:
            raise ValueError("A trained model is required; random initialization is not a SmartSignal result")
        self.layers = []
        width = 2 * len(FEATURES)
        for layer in self.data['layers']:
            weights, bias = np.asarray(layer['weights'], dtype=np.float32), np.asarray(layer['bias'], dtype=np.float32)
            if weights.ndim != 2 or weights.shape[1] != width or bias.shape != (weights.shape[0],) or not np.isfinite(weights).all() or not np.isfinite(bias).all():
                raise ValueError("Invalid model layer")
            width = weights.shape[0]
            self.layers.append((weights, bias))
        if len(self.layers) != 3 or width != 1:
            raise ValueError("Expected a three-layer scalar phase scorer")

    def scores(self, features):
        value = inputs(features)
        for i, (weights, bias) in enumerate(self.layers):
            value = value @ weights.T + bias
            if i < len(self.layers) - 1:
                value = np.maximum(value, 0)
        return value[:, 0]

    def action(self, jid, observation, clock):
        return legal_best(self.scores(observation['features']), observation['mask'])
