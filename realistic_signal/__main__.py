"""python -m realistic_signal: offline check/audit; explicit user-only run."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Realistic V2 (experimental; Legacy V1 preserved)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('check', help='Offline contract, route and frozen-release hash checks; no SUMO')
    build = sub.add_parser('build', help='Offline netconvert to a NEW directory; no traffic simulation')
    build.add_argument('--out', type=Path, required=True)
    run = sub.add_parser('run', help='USER EXECUTION ONLY: advances SUMO')
    run.add_argument('--scenario', choices=('quiet', 'busy', 'directional'), default='busy')
    run.add_argument('--controller', choices=('fixed', 'webster', 'max_pressure', 'smart'), default='fixed')
    run.add_argument('--horizon', type=int, default=900)
    run.add_argument('--gui', action='store_true')
    run.add_argument('--out', type=Path, required=True)
    audit = sub.add_parser('audit', help='Offline replay of saved signals and safety metrics; no SUMO')
    audit.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'build':
        from .build import build
        result = build(args.out)
        result = dict(network_sha256=result['network_sha256'], simulation_run=False)
    else:
        from .runner import check, run, audit
        if args.command == 'check':
            result = check()
        elif args.command == 'audit':
            result = audit(args.run)
        else:
            result = run(args.out, args.scenario, args.controller, args.horizon, args.gui)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
