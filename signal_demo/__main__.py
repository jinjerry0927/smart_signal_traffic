"""python -m signal_demo web | edge | bundle (imports SUMO only for web)."""
import argparse
from pathlib import Path
from .release import DEFAULT_RELEASE, ROOT


def main():
    parser = argparse.ArgumentParser(description='Smart Signal presentation prototype')
    sub = parser.add_subparsers(dest='command', required=True)
    web = sub.add_parser('web')
    web.add_argument('--host', default='127.0.0.1')
    web.add_argument('--port', type=int, default=8000)
    web.add_argument('--out', type=Path)
    web.add_argument('--release', type=Path, default=DEFAULT_RELEASE)
    web.add_argument('--unseen', action='store_true', help='Show the audited new-intersection trial mode')
    edge = sub.add_parser('edge')
    edge.add_argument('--host', default='127.0.0.1')
    edge.add_argument('--port', type=int, default=9998)
    edge.add_argument('--release', type=Path, default=DEFAULT_RELEASE)
    bundle = sub.add_parser('bundle')
    bundle.add_argument('--out', required=True, type=Path)
    bundle.add_argument('--release', type=Path, default=DEFAULT_RELEASE)
    probe_parser = sub.add_parser('probe')
    probe_parser.add_argument('--url', required=True)
    probe_parser.add_argument('--out', required=True, type=Path)
    probe_parser.add_argument('--release', type=Path, default=DEFAULT_RELEASE)
    probe_parser.add_argument('--require-diagnostics', action='store_true')
    args = parser.parse_args()
    if args.command == 'web':
        from .web import serve
        output = args.out or ROOT / 'results/runs' / ('unseen_web_20261029' if args.unseen else 'web_pi_prototype')
        serve(args.host, args.port, output, args.release, unseen=args.unseen)
    elif args.command == 'edge':
        from .edge import serve
        serve(args.host, args.port, args.release)
    elif args.command == 'bundle':
        from .bundle import export
        export(args.out, args.release)
    else:
        from .probe import probe
        probe(args.url, args.out, args.release, args.require_diagnostics)


if __name__ == '__main__':
    main()
