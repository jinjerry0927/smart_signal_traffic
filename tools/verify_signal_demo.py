"""Sequential, bounded integration checks with real SUMO and HTTP inference."""
import argparse
import csv
from pathlib import Path
import sys
import threading
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_demo.edge import make_server
from signal_demo.runtime import Demo
from transferable_signal.spec import digest, read_json, write_json


def wait_until(check, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.025)
    raise AssertionError('Timed out waiting for demo state')


def terminal(demo):
    snapshot = demo.snapshot()
    return snapshot if snapshot['status'] in ('completed', 'stopped', 'failed') else None


def complete(demo):
    state = wait_until(lambda: terminal(demo))
    demo.worker.join(timeout=5)
    assert state['status'] == 'completed', state
    out = Path(state['output'])
    metrics = read_json(out / 'metrics.json')
    assert not metrics['teleport_events'] and not metrics['collision_vehicle_events']
    manifest = read_json(out / 'manifest.json')
    assert manifest['status'] == 'completed'
    assert metrics['sample_seconds'] == manifest['horizon_s']
    with (out / 'decisions.csv').open(encoding='utf-8', newline='') as stream:
        decisions = list(csv.DictReader(stream))
    with (out / 'signals.csv').open(encoding='utf-8', newline='') as stream:
        signals = list(csv.DictReader(stream))
    assert len(decisions) == len(signals) == manifest['horizon_s'] // 5 * 3
    trips = [dict(t.attrib) for t in ET.parse(out / 'tripinfo.xml').getroot().findall('tripinfo')]
    return dict(path=str(out), metrics=metrics, manifest=manifest, decisions=decisions, signals=signals, trips=trips)


def verify(output):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(str(output))
    output.mkdir(parents=True, exist_ok=False)
    frozen = list((ROOT / 'transferable_signal').glob('*.py')) + [ROOT / 'configs/smart_signal_default.json']
    frozen += list((ROOT / 'results/runs/final_signal_wave10_20261007/release').glob('*'))
    frozen += list((ROOT / 'network/transferable_v1/gyeongju').glob('*'))
    before = {str(p.relative_to(ROOT)): digest(p) for p in frozen if p.is_file()}
    server = make_server(port=0)
    service = threading.Thread(target=server.serve_forever, daemon=True)
    service.start()
    url = 'http://127.0.0.1:' + str(server.server_port)
    demo = Demo(output / 'runs')
    reports = []
    try:
        for scenario in ('gyeongju_quiet', 'gyeongju_busy'):
            pair = []
            for mode in ('local', 'edge'):
                demo.start(dict(scenario=scenario, mode=mode, edge_url=url, horizon_s=180, speed=60))
                result = complete(demo)
                assert not result['manifest']['fallback_events']
                assert all(r['inference'] == mode for r in result['decisions'])
                pair.append(result)
                print(f'{scenario}/{mode}: {result["metrics"]["sample_seconds"]} seconds, {len(result["decisions"])} decisions', flush=True)
            local, remote = pair
            assert local['metrics'] == remote['metrics'], 'PC/edge metrics differ'
            assert local['signals'] == remote['signals'], 'PC/edge signal trace differs'
            assert local['trips'] == remote['trips'], 'PC/edge trip records differ'
            decision_signature = lambda r: [(v['time_s'], v['junction'], v['action']) for v in r['decisions']]
            assert decision_signature(local) == decision_signature(remote)
            reports.append(dict(scenario=scenario, local=local['path'], edge=remote['path'],
                                seconds=180, decisions_per_run=len(local['decisions']), exact_parity=True))

        # HTTP failure during operation becomes a visible, sticky local fallback.
        demo.start(dict(mode='edge', edge_url=url, horizon_s=90, speed=20))
        wait_until(lambda: demo.snapshot()['time_s'] >= 15)
        server.shutdown()
        server.server_close()
        service.join(timeout=3)
        result = complete(demo)
        assert demo.snapshot()['link'] == 'fallback'
        assert result['manifest']['fallback_events']
        modes = {r['inference'] for r in result['decisions']}
        assert modes == {'local', 'edge'}
        reports.append(dict(check='mid_run_disconnect', path=result['path'], visible_fallback=True))
        print('Mid-run disconnect: same-policy local fallback verified', flush=True)

        demo.start(dict(mode='edge', edge_url=url, horizon_s=60, speed=60))
        result = complete(demo)
        assert demo.snapshot()['link'] == 'fallback' and result['manifest']['fallback_events']
        assert all(r['inference'] == 'local' for r in result['decisions'])
        reports.append(dict(check='startup_disconnect', path=result['path'], visible_fallback=True))

        demo.start(dict(horizon_s=60, speed=5))
        wait_until(lambda: demo.snapshot()['status'] == 'running' and demo.snapshot()['time_s'] >= 2)
        demo.command('pause')
        wait_until(lambda: demo.snapshot()['status'] == 'paused')
        paused = demo.snapshot()['time_s']
        time.sleep(.35)
        assert demo.snapshot()['time_s'] == paused, 'SUMO advanced while paused'
        demo.command('resume')
        wait_until(lambda: demo.snapshot()['time_s'] > paused)
        demo.command('stop')
        state = wait_until(lambda: terminal(demo))
        demo.worker.join(timeout=5)
        assert state['status'] == 'stopped', state
        manifest = read_json(Path(state['output']) / 'manifest.json')
        assert manifest['status'] == 'stopped'
        reports.append(dict(check='pause_resume_stop', path=state['output'], paused_time_s=paused,
                            final_time_s=state['time_s'], status='stopped'))
        print('Pause/resume/stop: simulation freeze and separate stopped records verified', flush=True)
        after = {name: digest(ROOT / name) for name in before}
        assert before == after, 'Frozen source/input/release changed'
        write_json(output / 'verification.json', dict(status='passed', checks=reports, frozen_sha256=before,
                                                     physical_pi_verified=False, edge_host='PC loopback'))
        print('Integration passed: ' + str(output / 'verification.json'), flush=True)
    finally:
        demo.close()
        if service.is_alive():
            server.shutdown()
            server.server_close()
            service.join(timeout=3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    verify(parser.parse_args().out)
