"""Export a separate Pi folder without SUMO or the old project files."""
from pathlib import Path
import shutil

from transferable_signal.spec import digest, write_json
from .release import DEFAULT_RELEASE, INFERENCE_FILES, ROOT, load_release


def export(output, release=DEFAULT_RELEASE):
    output, release = Path(output).resolve(), Path(release).resolve()
    _, identity = load_release(release)
    if output.exists():
        raise FileExistsError('Bundle destination already exists: ' + str(output))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'transferable_signal').mkdir()
    (output / 'signal_demo').mkdir()
    (output / 'release').mkdir()
    for name in INFERENCE_FILES:
        shutil.copy2(ROOT / 'transferable_signal' / name, output / 'transferable_signal' / name)
    for name in ('__init__.py', '__main__.py', 'edge.py', 'protocol.py', 'release.py', 'probe.py', 'explanation.py'):
        shutil.copy2(ROOT / 'signal_demo' / name, output / 'signal_demo' / name)
    for name in ('model.json', 'policy.json'):
        shutil.copy2(release / name, output / 'release' / name)
    (output / 'README.txt').write_text(
        'Smart Signal Pi inference (Python 3.9+ and NumPy)\n'
        'cd <this folder>\n'
        'python3 -B -m signal_demo edge --release release --host 0.0.0.0 --port 9998\n'
        'Keep this terminal open. Ctrl+C stops only this service.\n'
        'No SUMO, Torch, OS reset, or old project changes required.\n', encoding='utf-8')
    write_json(output / 'bundle.json', dict(identity=identity, files={str(p.relative_to(output)).replace('\\', '/'): digest(p)
                       for p in sorted(output.rglob('*')) if p.is_file()}))
    print('Pi bundle: ' + str(output), flush=True)
    return output
