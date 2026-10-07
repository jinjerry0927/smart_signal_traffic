"""Correct the t600 audit boundary; reuse one immutable completed raw run."""
import argparse
import ast
import copy
import inspect
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import diagnose_gyeongju_drain as original
from transferable_signal.spec import digest, read_json

LOGGER_SHA256 = 'c47c9022fb1927e4418e91d73232c52a0530306bd2e258ff3639f504789e05e4'
DEFAULT_OUTPUT = ROOT / 'results/runs/gyeongju_drain_wave4_20261004_retry01'
RECOVERED = Path('runs/gyeongju_busy_s00/fixed')


def corrected_auditor():
    """Compile verified repository code with exactly one guard constant changed."""
    if digest(Path(original.__file__)) != LOGGER_SHA256:
        raise ValueError('Original logging driver changed')
    source = inspect.getsource(original._audit_trace)
    old = "int(row['time_s']) >= 600 and accounted != scheduled"
    if source.count(old) != 1:
        raise ValueError('Expected single t600 audit guard was not found')
    corrected = source.replace(old, old.replace('600', '601'), 1)
    before, after = ast.parse(source), ast.parse(corrected)
    changes = []
    for left, right in zip(ast.walk(before), ast.walk(after)):
        if ast.dump(left) != ast.dump(right) and isinstance(left, ast.Constant):
            changes.append((left.value, right.value))
    expected = copy.deepcopy(before)
    guard = next(node for node in ast.walk(expected) if isinstance(node, ast.BoolOp) and ast.unparse(node) == old)
    guard.values[0].comparators[0].value = 601
    if changes != [(600, 601)] or ast.dump(expected) != ast.dump(after):
        raise ValueError('Retry correction changed more than the single audit guard')
    namespace = dict(vars(original))
    exec(compile(after, str(Path(__file__)), 'exec'), namespace)
    return namespace['_audit_trace']


def recovery_provenance(base_fingerprint):
    source = original.DEFAULT_OUTPUT.resolve()
    metadata = read_json(source / 'manifest.json')
    if (metadata.get('fingerprint') != base_fingerprint or metadata.get('status') != 'safety_hold' or
            base_fingerprint.get('diagnostic_tool_sha256') != LOGGER_SHA256 or
            metadata.get('started_run_attempts') != 1 or metadata.get('expected_runs') != 16 or
            metadata.get('error') != 'ValueError: Per-second vehicle accounting changed' or metadata.get('records') != [] or
            metadata.get('active_run') != dict(scenario='gyeongju_busy_s00', controller='fixed')):
        raise ValueError('Only the reviewed first completed audit-boundary failure may be recovered')
    directory = source / RECOVERED
    files = sorted(path for path in directory.rglob('*') if path.is_file())
    if not files or any(path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()) for path in files):
        raise ValueError('Recovery raw path contains unsupported external files')
    run = read_json(directory / 'manifest.json')
    if run.get('status') != 'completed' or run.get('diagnostic_tool_sha256') != LOGGER_SHA256:
        raise ValueError('Recovered raw run has incomplete or changed logging provenance')
    return dict(source_wave=str(source), directory=str(RECOVERED),
                source_manifest_sha256=digest(source / 'manifest.json'),
                prepared_suite_sha256=digest(source / 'prepared_input/suite.json'),
                raw_sha256={str(path.relative_to(directory)): digest(path) for path in files},
                original_input_directory=str((source / 'prepared_input').resolve()))


def run_retry(output=DEFAULT_OUTPUT, resume=False, max_wall_seconds=2700):
    auditor = corrected_auditor()
    original_suite, cases, freeze, selected, reference, base = original._preflight(original.wave3.DEFAULT_SUITE, original.DEFAULT_REFERENCE)
    recovered = recovery_provenance(base)
    fingerprint = dict(base, audit_wrapper_sha256=digest(Path(__file__)), logging_driver_sha256=LOGGER_SHA256,
                       accounting_boundary='t600 can include unattempted insertions; full count required at t601 onward',
                       recovered_run=recovered)
    data = original_suite, cases, freeze, selected, reference, fingerprint
    output = Path(output).resolve()
    exists = output.exists()
    if exists and not resume:
        raise FileExistsError('Retry output exists; use --resume with identical frozen inputs')
    if not exists and resume:
        raise FileNotFoundError('No retry wave to resume')
    with patch.object(original, '_audit_trace', auditor), patch.object(original, '_preflight', return_value=data):
        if not exists:
            output.mkdir(parents=True)
            suite = original._prepare_input(output, original.wave3.DEFAULT_SUITE, original_suite, selected)
            if digest(suite) != recovered['prepared_suite_sha256']:
                raise ValueError('Recovered and retry prepared inputs differ')
            shutil.copytree(Path(recovered['source_wave']) / RECOVERED, output / RECOVERED)
            copied = output / RECOVERED
            if any(digest(copied / name) != value for name, value in recovered['raw_sha256'].items()):
                raise ValueError('Copied raw bytes differ from the immutable first run')
            # Audit only the copy: prefix audit writes a derived JSON file.
            scenario = next(item for item in selected if item['id'] == 'gyeongju_busy_s00')
            record = original.audit_drain_run(copied, dict(scenario, drain_s=1200), 'fixed', suite, freeze,
                reference[(scenario['id'], 'fixed')], fingerprint, output)
            metadata = dict(status='started', fingerprint=fingerprint,
                records=[dict(scenario=scenario['id'], controller='fixed', **record)],
                expected_runs=16, actual_new_runs=1, started_run_attempts=0, prepared_suite_sha256=digest(suite))
            original.common.atomic_json(output / 'manifest.json', metadata)
        result = original.run_drain_wave(output, resume=True, max_wall_seconds=max_wall_seconds)
    result.update(verified_runs=len(result['records']), reused_runs=1, actual_new_runs=len(result['records']) - 1)
    original.common.atomic_json(output / 'manifest.json', result)
    if result['status'] == 'completed':
        summary = read_json(output / 'summary.json')
        summary.update(verified_runs=16, reused_runs=1, actual_new_runs=15, recovered_run=recovered)
        original.common.atomic_json(output / 'summary.json', summary)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--max-wall-seconds', type=float, default=2700)
    args = parser.parse_args()
    result = run_retry(args.out, args.resume, args.max_wall_seconds)
    print(f"Drain retry {result['status']}: {result['verified_runs']}/16 verified, {result['actual_new_runs']} new, 1 reused", flush=True)


if __name__ == '__main__':
    main()
