"""User-invoked shared Double DQN training; no training on import."""
from collections import deque
import csv
import math
import os
import random
from pathlib import Path

import numpy as np
import torch
from torch import nn

from . import VERSION, FEATURES
from .model import inputs
from .environment import SignalEnvironment
from .spec import digest, read_json, write_json
from .suite import load_suite


class PhaseScorer(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(2 * len(FEATURES), 32), nn.ReLU(),
                                    nn.Linear(32, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, features):
        return self.layers(features).squeeze(-1)


def update_batch(model, target, optimizer, batch, gamma=.99):
    states, actions, rewards, next_states, masks, dones = zip(*batch)
    lengths = [len(s) for s in states]
    next_lengths = [len(s) for s in next_states]
    features = torch.from_numpy(np.concatenate([inputs(s) for s in states]))
    next_features = torch.from_numpy(np.concatenate([inputs(s) for s in next_states]))
    values = model(features).split(lengths)
    chosen = torch.stack([v[a] for v, a in zip(values, actions)])
    with torch.no_grad():
        online = model(next_features).split(next_lengths)
        frozen = target(next_features).split(next_lengths)
        best = [int(v.masked_fill(~torch.as_tensor(mask), -torch.inf).argmax()) for v, mask in zip(online, masks)]
        bootstrap = torch.stack([v[a] for v, a in zip(frozen, best)])
        expected = torch.tensor(rewards, dtype=torch.float32) + gamma * bootstrap * (1-torch.tensor(dones, dtype=torch.float32))
    loss = nn.functional.smooth_l1_loss(chosen, expected)
    optimizer.zero_grad()
    loss.backward()
    nn.utils.clip_grad_norm_(model.parameters(), 5)
    optimizer.step()
    return float(loss.detach())


def export_model(model, destination, training):
    layers = [dict(weights=layer.weight.detach().cpu().tolist(), bias=layer.bias.detach().cpu().tolist())
              for layer in model.layers if isinstance(layer, nn.Linear)]
    write_json(destination, dict(version=VERSION, features=list(FEATURES), layers=layers, training=training))


CSV_COLUMNS = ['episode', 'scenario', 'reward', 'epsilon', 'updates', 'last_loss']
CHECKPOINT_VERSION = 1


def _atomic_checkpoint(path, payload):
    temporary = path.with_suffix('.pt.tmp')
    try:
        with temporary.open('wb') as stream:
            torch.save(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _csv_rows(rows):
    return [[str(value) if value is not None else '' for value in row] for row in rows]


def _write_csv(path, rows):
    temporary = path.with_suffix('.csv.tmp')
    try:
        with temporary.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_manifest(path, metadata):
    """Preserve the previous readable manifest if a write is interrupted."""
    temporary = path.with_suffix('.json.tmp')
    try:
        write_json(temporary, metadata)
        with temporary.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _pack_replay(replay):
    """Padded tensors avoid pickled NumPy objects and thousands of tensor files."""
    count = len(replay)
    width = max((max(len(row[0]), len(row[3])) for row in replay), default=0)
    states = np.zeros((count, width, len(FEATURES)), dtype=np.float32)
    following = np.zeros_like(states)
    masks = np.zeros((count, width), dtype=bool)
    lengths = np.zeros((count, 2), dtype=np.int64)
    actions, rewards, dones = [], [], []
    for index, (state, action, reward, next_state, mask, done) in enumerate(replay):
        n, m = len(state), len(next_state)
        states[index, :n], following[index, :m], masks[index, :m] = state, next_state, mask
        lengths[index] = n, m
        actions.append(action)
        rewards.append(reward)
        dones.append(done)
    return dict(states=torch.from_numpy(states), next_states=torch.from_numpy(following),
                masks=torch.from_numpy(masks), lengths=torch.from_numpy(lengths),
                actions=torch.tensor(actions, dtype=torch.int64),
                rewards=torch.tensor(rewards, dtype=torch.float64), dones=torch.tensor(dones, dtype=torch.bool))


def _unpack_replay(packed):
    required = {'states', 'next_states', 'masks', 'lengths', 'actions', 'rewards', 'dones'}
    if not isinstance(packed, dict) or set(packed) != required or any(not isinstance(v, torch.Tensor) for v in packed.values()):
        raise ValueError('Invalid checkpoint replay')
    values = {key: value.cpu().numpy() for key, value in packed.items()}
    states, following = values['states'], values['next_states']
    count = len(states)
    if (states.ndim != 3 or states.shape != following.shape or states.shape[2] != len(FEATURES)
            or count > 20000 or values['masks'].shape != states.shape[:2]
            or values['lengths'].shape != (count, 2)
            or any(values[key].shape != (count,) for key in ('actions', 'rewards', 'dones'))
            or states.dtype != np.float32 or following.dtype != np.float32
            or values['masks'].dtype != bool or values['dones'].dtype != bool
            or values['lengths'].dtype != np.int64 or values['actions'].dtype != np.int64
            or not all(np.isfinite(values[key]).all() for key in ('states', 'next_states', 'rewards'))):
        raise ValueError('Invalid checkpoint replay shape or values')
    replay = deque(maxlen=20000)
    for index in range(count):
        n, m = values['lengths'][index]
        action = int(values['actions'][index])
        mask = values['masks'][index, :m]
        if not (1 <= n <= states.shape[1] and 1 <= m <= states.shape[1] and 0 <= action < n and mask.any()):
            raise ValueError('Invalid checkpoint replay transition')
        replay.append((states[index, :n].copy(), action, float(values['rewards'][index]),
                       following[index, :m].copy(), mask.copy(), bool(values['dones'][index])))
    return replay


def _training_contract(metadata):
    algorithm_files = ('learning.py', 'model.py', 'environment.py', 'control.py', 'spec.py')
    return dict(version=VERSION, features=list(FEATURES), seed=metadata['seed'], batch_size=metadata['batch_size'],
                suite_sha256=metadata['suite_sha256'], frozen_demand=metadata['frozen_demand'],
                scenarios=metadata['scenarios'], training_network_sha256=metadata['training_network_sha256'],
                sumo=metadata['sumo'], algorithm_code={key: metadata['code_sha256'][key] for key in algorithm_files},
                algorithm=dict(name='DoubleDQN', gamma=.99, adam_lr=.001, gradient_clip=5,
                               target_every_updates=100, replay_capacity=20000))


def _load_checkpoint(path, output, contract, episodes):
    if path.suffix == '.json':
        raise ValueError('JSON inference models cannot resume training; use checkpoint.pt')
    if path != output / 'checkpoint.pt' or not output.is_dir():
        raise ValueError('Resume requires this output directory\'s checkpoint.pt')
    try:
        saved = torch.load(path, map_location='cpu', weights_only=True)
    except Exception as exc:
        raise ValueError(f'Cannot read training checkpoint: {exc}') from exc
    required = {'checkpoint_version', 'output', 'contract', 'metadata', 'rows', 'online', 'target',
                'optimizer', 'replay', 'random_state', 'torch_rng_state'}
    if (not isinstance(saved, dict) or set(saved) != required or saved.get('checkpoint_version') != CHECKPOINT_VERSION
            or saved.get('output') != str(output) or saved.get('contract') != contract):
        raise ValueError('Checkpoint output, suite/data, seed, batch size or algorithm differs')
    metadata, rows = saved['metadata'], saved['rows']
    completed = metadata.get('completed_episodes')
    if (not isinstance(completed, int) or isinstance(completed, bool) or completed < 0
            or episodes < metadata['episodes'] or episodes < completed or len(rows) != completed
            or not isinstance(metadata['exploration_episodes'], int) or metadata['exploration_episodes'] < 1):
        raise ValueError('Invalid checkpoint episode history or decreased episode target')
    previous_updates = 0
    for index, row in enumerate(rows):
        expected_epsilon = max(.05, 1 - index / max(1, metadata['exploration_episodes'] * .8))
        if (len(row) != len(CSV_COLUMNS) or row[0] != index or row[1] not in contract['scenarios']
                or row[3] != expected_epsilon or not isinstance(row[4], int) or row[4] < previous_updates
                or not math.isfinite(row[2]) or (row[5] is not None and not math.isfinite(row[5]))):
            raise ValueError('Invalid checkpoint CSV history')
        previous_updates = row[4]
    if previous_updates != metadata['updates']:
        raise ValueError('Checkpoint update count differs from history')
    csv_path = output / 'training.csv'
    if not csv_path.exists():
        raise ValueError('Resume training.csv is missing')
    with csv_path.open(newline='', encoding='utf-8') as stream:
        actual = list(csv.reader(stream))
    expected = [CSV_COLUMNS] + _csv_rows(rows)
    # Checkpoint is committed before CSV, so a crash can leave a valid old prefix.
    if not actual or actual != expected[:len(actual)] or len(actual) > len(expected):
        raise ValueError('training.csv differs from the checkpoint; original files preserved')
    manifest = read_json(output / 'manifest.json')
    if (manifest.get('suite_sha256') != contract['suite_sha256'] or manifest.get('seed') != contract['seed']
            or manifest.get('batch_size') != contract['batch_size']
            or manifest.get('completed_episodes', 0) > completed):
        raise ValueError('Output manifest does not belong to the checkpoint')
    return saved


def _validate_weights(values):
    if isinstance(values, torch.Tensor):
        if values.is_floating_point() and not torch.isfinite(values).all():
            raise ValueError('Checkpoint contains nonfinite training weights')
    elif isinstance(values, dict):
        for value in values.values():
            _validate_weights(value)
    elif isinstance(values, (tuple, list)):
        for value in values:
            _validate_weights(value)


def train(suite_path, output, episodes=30, seed=42, batch_size=32, resume=None, stop_after=None):
    """Episodes is the total target; stop_after bounds this call at complete episodes.

    Resume only from output/checkpoint.pt. Extending the target keeps the original
    epsilon horizon, replay, optimizer and RNG; JSON exports are inference only.
    """
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in (episodes, batch_size)):
        raise ValueError('episodes and batch_size must be positive integers')
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ValueError('seed must be an integer')
    if stop_after is not None and (not isinstance(stop_after, int) or isinstance(stop_after, bool) or stop_after < 1):
        raise ValueError('stop_after must be a positive integer')
    suite_path, output = Path(suite_path).resolve(), Path(output).resolve()
    suite, cases = load_suite(suite_path)
    scenarios = [s for s in suite['scenarios'] if s['split'] == 'train']
    if not scenarios:
        raise ValueError("No training scenarios")
    # Train function cannot select validation or test cases.
    from .runner import provenance
    metadata = provenance(suite_path, suite, 'training', None)
    metadata.update(seed=seed, episodes=episodes, exploration_episodes=episodes, batch_size=batch_size,
                    reward='negative mean inbound queue/storage, averaged over each 5-second action',
                    scenarios=[s['id'] for s in scenarios], updates=0, completed_episodes=0, status='started')
    metadata['training_network_sha256'] = sorted({cases[s['case']][0]['network_sha256'] for s in scenarios})
    contract = _training_contract(metadata)
    saved = _load_checkpoint(Path(resume).resolve(), output, contract, episodes) if resume is not None else None
    if saved is None:
        output.mkdir(parents=True, exist_ok=False)
    rng = random.Random(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    model, target = PhaseScorer(), PhaseScorer()
    target.load_state_dict(model.state_dict())
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    replay = deque(maxlen=20000)
    updates = 0
    rows = []
    if saved is not None:
        try:
            for key in ('online', 'target', 'optimizer'):
                _validate_weights(saved[key])
            expected_groups = [{k: v for k, v in group.items() if k != 'params'} for group in optimizer.state_dict()['param_groups']]
            saved_groups = [{k: v for k, v in group.items() if k != 'params'} for group in saved['optimizer']['param_groups']]
            if expected_groups != saved_groups:
                raise ValueError('Checkpoint optimizer settings differ from the algorithm')
            model.load_state_dict(saved['online'])
            target.load_state_dict(saved['target'])
            optimizer.load_state_dict(saved['optimizer'])
            replay = _unpack_replay(saved['replay'])
            rng.setstate(saved['random_state'])
            torch.set_rng_state(saved['torch_rng_state'])
        except Exception as exc:
            raise ValueError(f'Invalid training checkpoint state: {exc}') from exc
        metadata, rows = saved['metadata'], saved['rows']
        updates = metadata['updates']
        metadata.update(episodes=episodes, status='started')
        metadata.pop('error', None)
        metadata.pop('partial_updates', None)
    start = metadata['completed_episodes']
    end = min(episodes, start + stop_after) if stop_after is not None else episodes

    def checkpoint():
        _atomic_checkpoint(output / 'checkpoint.pt', dict(checkpoint_version=CHECKPOINT_VERSION,
            output=str(output), contract=contract, metadata=metadata, rows=rows,
            online=model.state_dict(), target=target.state_dict(), optimizer=optimizer.state_dict(),
            replay=_pack_replay(replay), random_state=rng.getstate(), torch_rng_state=torch.get_rng_state()))
        _write_csv(output / 'training.csv', rows)
        _write_manifest(output / 'manifest.json', metadata)

    _write_manifest(output / 'manifest.json', metadata)
    checkpoint()
    try:
        for episode in range(start, end):
            scenario = scenarios[episode % len(scenarios)] if episode < len(scenarios) else rng.choice(scenarios)
            spec, network, _ = cases[scenario['case']]
            episode_output = output / f'episode_{episode:04d}'
            retry = 0
            while episode_output.exists():
                retry += 1
                episode_output = output / f'episode_{episode:04d}_retry_{retry:03d}'
            env = None
            total, loss = 0.0, None
            epsilon = max(.05, 1 - episode / max(1, metadata['exploration_episodes'] * .8))
            try:
                env = SignalEnvironment(spec, network, suite_path.parent / scenario['routes'],
                                        episode_output, scenario['duration_s'], seed=seed + episode)
                obs = env.reset()
                done = False
                while not done:
                    actions = {}
                    for jid, value in obs.items():
                        valid = np.flatnonzero(value['mask'])
                        if rng.random() < epsilon:
                            actions[jid] = int(rng.choice(valid.tolist()))
                        else:
                            with torch.no_grad():
                                q = model(torch.from_numpy(inputs(value['features']))).numpy()
                            actions[jid] = int(np.where(value['mask'], q, -np.inf).argmax())
                    next_obs, reward, done = env.step(actions)
                    for jid, value in obs.items():
                        replay.append((value['features'].copy(), actions[jid], reward[jid],
                                       next_obs[jid]['features'].copy(), next_obs[jid]['mask'].copy(), done))
                    total += sum(reward.values()) / len(reward)
                    if len(replay) >= batch_size:
                        loss = update_batch(model, target, optimizer, rng.sample(list(replay), batch_size))
                        updates += 1
                        if updates % 100 == 0:
                            target.load_state_dict(model.state_dict())
                    obs = next_obs
                write_json(env.output / 'metrics.json', env.finish())
            finally:
                if env is not None:
                    env.close()
            metadata['updates'] = updates
            metadata['completed_episodes'] = episode + 1
            rows.append([episode, scenario['id'], total, epsilon, updates, loss])
            checkpoint()
            if updates:
                export_model(model, output / f'model_episode_{episode+1:04d}.json', metadata)
            if episode == start or (episode + 1) % 10 == 0 or episode + 1 == end:
                print(f'Training episode {episode+1}/{episodes}: {updates} updates', flush=True)
        if end < episodes:
            metadata['status'] = 'paused'
            if updates:
                export_model(model, output / 'model.json', metadata)
            checkpoint()
            return metadata
        if not updates:
            raise RuntimeError("No gradient updates completed; no trained model was produced")
        metadata['status'] = 'completed'
        export_model(model, output / 'model.json', metadata)
        checkpoint()
        return metadata
    except BaseException as exc:
        # The checkpoint retains the last complete episode, not partial gradients.
        metadata.update(status='paused' if isinstance(exc, KeyboardInterrupt) else 'failed',
                        error=f'{type(exc).__name__}: {exc}', partial_updates=updates)
        _write_manifest(output / 'manifest.json', metadata)
        raise
