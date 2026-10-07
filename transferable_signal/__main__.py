"""Prepare/check never advance simulation time. Run/train/compare do."""
import argparse
from pathlib import Path
import subprocess

import sumolib

from .suite import prepare, load_suite, ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='Build networks and frozen demand; no simulation')
    prep.add_argument('--config', type=Path, default=ROOT / 'configs/transferable_signal.json')
    prep.add_argument('--out', type=Path, required=True)
    check = commands.add_parser('check', help='Static validation; optionally load SUMO at time zero')
    check.add_argument('--suite', type=Path, default=ROOT / 'network/transferable_v1/suite.json')
    check.add_argument('--load', action='store_true')
    validation = commands.add_parser('prepare-validation', help='Freeze diverse validation demand; no simulation')
    validation.add_argument('--source-suite', type=Path, default=ROOT / 'network/transferable_v1/suite.json')
    validation.add_argument('--samples', type=int, default=5)
    validation.add_argument('--out', type=Path, required=True)
    models = commands.add_parser('compare-models', help='USER EXECUTION: compare independent models on validation')
    models.add_argument('--suite', type=Path, required=True)
    models.add_argument('--models', nargs='+', type=Path, required=True)
    models.add_argument('--out', type=Path, required=True)
    for name in ('run', 'compare', 'train'):
        sub = commands.add_parser(name, help=f'USER EXECUTION: {name} advances traffic simulation')
        sub.add_argument('--suite', type=Path, default=ROOT / 'network/transferable_v1/suite.json')
        sub.add_argument('--out', type=Path, required=True)
        if name == 'train':
            sub.add_argument('--episodes', type=int, default=30)
            sub.add_argument('--seed', type=int, default=42)
            sub.add_argument('--resume', type=Path, help='Resume this output folder from its checkpoint.pt')
            sub.add_argument('--stop-after', type=int, help='Pause after this many additional episodes')
        else:
            sub.add_argument('--model', type=Path)
            sub.add_argument('--smart-policy', choices=['validated', 'raw'], default='validated',
                             help='validated: frozen seed43 V2 (default); raw: requires --model')
            if name == 'run':
                sub.add_argument('--scenario', required=True)
                sub.add_argument('--controller', choices=['fixed', 'webster', 'max_pressure', 'smart'], default='fixed')
                sub.add_argument('--gui', action='store_true')
            else:
                sub.add_argument('--split', choices=['train', 'validation', 'test'], default='validation')
                sub.add_argument('--controllers', nargs='+', choices=['fixed', 'webster', 'max_pressure', 'smart'])
    args = parser.parse_args()
    if args.command == 'prepare':
        suite = prepare(args.config, args.out)
        print(f"Prepared {len(suite['cases'])} networks, {len(suite['scenarios'])} frozen scenarios. No simulation run.")
    elif args.command == 'check':
        suite, cases = load_suite(args.suite)
        if args.load:
            for scenario in suite['scenarios']:
                _, network, _ = cases[scenario['case']]
                subprocess.run([sumolib.checkBinary('sumo'), '-n', str(network), '-r', str(args.suite.resolve().parent / scenario['routes']),
                                '--end', '0', '--no-step-log', 'true', '--duration-log.disable', 'true'], check=True, timeout=30)
        print(f"CHECK PASS: {len(cases)} networks / {len(suite['scenarios'])} scenarios; " + ('zero-time load only.' if args.load else 'static only.'))
    elif args.command == 'train':
        from .learning import train
        result = train(args.suite, args.out, args.episodes, args.seed, resume=args.resume, stop_after=args.stop_after)
        print(f"Training {result['status']}: {result['updates']} updates, {result['completed_episodes']} episodes.")
    elif args.command == 'prepare-validation':
        from .validation import prepare_validation
        result = prepare_validation(args.source_suite, args.out, args.samples)
        print(f"Prepared {len(result['scenarios'])} frozen validation scenarios. No simulation run.")
    elif args.command == 'compare-models':
        from .validation import compare_models
        compare_models(args.suite, args.models, args.out)
        print(f"Model comparison saved to {args.out / 'comparison.csv'}")
    elif args.command == 'run':
        from .runner import run
        try:
            print(run(args.suite, args.scenario, args.controller, args.out, args.model, args.gui,
                      smart_policy=args.smart_policy))
        except (ValueError, FileNotFoundError, FileExistsError) as exc:
            parser.error(str(exc))
    else:
        from .runner import compare
        try:
            compare(args.suite, args.split, args.out, args.model, args.controllers, smart_policy=args.smart_policy)
        except (ValueError, FileNotFoundError, FileExistsError) as exc:
            parser.error(str(exc))
        print(f"Comparison saved to {args.out / 'comparison.csv'}")


if __name__ == '__main__':
    main()
