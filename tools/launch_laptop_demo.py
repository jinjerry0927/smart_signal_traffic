"""Start this laptop's rehearsal without replacing occupied ports or old runs."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlencode
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_demo.release import load_release

PI_FOLDER = '/home/james/smart_signal_pi_20261008_2f9ca287'
PI_IP = '10.64.217.27'


def check_environment():
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError('노트북은 Python 3.11로 실행하세요.')
    env = os.environ.copy()
    sumo = shutil.which('sumo')
    if not sumo:
        home = Path(env.get('SUMO_HOME', r'C:\Program Files (x86)\Eclipse\Sumo'))
        candidate = home / 'bin' / 'sumo.exe'
        if not candidate.is_file():
            raise RuntimeError('SUMO를 찾지 못했습니다. 매뉴얼의 환경 준비를 확인하세요.')
        sumo = str(candidate)
        env['SUMO_HOME'] = str(home)
        env['PATH'] = str(home / 'bin') + os.pathsep + env.get('PATH', '')
    version = subprocess.run([sumo, '--version'], capture_output=True, text=True, check=True).stdout
    if version.splitlines()[0].split()[-1] != '1.26.0':
        raise RuntimeError('이번 실습에는 SUMO 1.26.0을 사용하세요.')
    import numpy
    import sumolib
    import traci
    _, identity = load_release()
    return env, identity


def free_web_port():
    for port in range(8000, 8011):
        with socket.socket() as test:
            try:
                test.bind(('127.0.0.1', port))
            except OSError:
                continue
            return port
    raise RuntimeError('8000~8010 포트가 사용 중입니다. 불필요한 관제 창을 정리하세요.')


def health(ip, identity):
    try:
        with urlopen(f'http://{ip}:9998/health', timeout=2) as response:
            data = json.load(response)
    except (URLError, TimeoutError, ValueError):
        return None
    if (data.get('status') != 'ready' or data.get('identity') != identity
            or data.get('system') != 'Linux' or data.get('machine') != 'aarch64'):
        raise RuntimeError('9998번 서버의 모델 또는 장치가 다릅니다. 이 서버를 종료하거나 덮어쓰지 않습니다.')
    return data


def start_pi(ip, identity):
    ready = health(ip, identity)
    if ready:
        print('Pi 추론 서버가 이미 실행 중입니다: ' + ready['hostname'], flush=True)
        return None
    if os.name != 'nt':
        raise RuntimeError('이 실행 도우미는 Windows 노트북용입니다.')
    ssh = shutil.which('ssh')
    if not ssh:
        raise RuntimeError('Windows의 OpenSSH 클라이언트를 찾지 못했습니다.')
    command = ('cd ' + shlex.quote(PI_FOLDER)
               + ' && python3 -B -m signal_demo edge --release release --host 0.0.0.0 --port 9998')
    window = subprocess.Popen([ssh, '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
                               '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
                               '-t', 'james@' + ip, command], creationflags=subprocess.CREATE_NEW_CONSOLE)
    print('별도로 열린 SSH 창에 Pi 비밀번호를 직접 입력하세요. 그 창은 발표 중 열어 두세요.', flush=True)
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        if window.poll() is not None:
            raise RuntimeError('SSH 창이 종료됐습니다. 계정/비밀번호/서버 키 안내를 매뉴얼에서 확인하세요.')
        ready = health(ip, identity)
        if ready:
            print('실제 Pi 추론 서버 준비 완료: ' + ready['hostname'], flush=True)
            return window
        time.sleep(1)
    raise RuntimeError('Pi 서버 응답이 없습니다. SSH 창의 메시지를 확인한 뒤 다시 실행하세요.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pi', action='store_true', help='Pi 서버도 확인하거나 실행')
    parser.add_argument('--ip', help='현재 Pi IPv4 주소')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--check', action='store_true', help='환경과 포트만 확인하고 종료')
    args = parser.parse_args()
    env, identity = check_environment()
    port = free_web_port()
    if args.check:
        print(json.dumps(dict(python=sys.version.split()[0], sumo='1.26.0',
                              next_web_port=port, frozen_identity=identity['identity_sha256']), indent=2))
        return 0
    if args.pi:
        value = args.ip or input(f'휴대폰에서 확인한 현재 Pi IP [Enter = {PI_IP}]: ').strip() or PI_IP
        ip = str(ipaddress.IPv4Address(value))
        start_pi(ip, identity)
        print(f'웹 설정: Smart Signal / 원격 / Raspberry Pi / http://{ip}:9998', flush=True)
    else:
        print('웹 설정: Smart Signal / PC', flush=True)
    url = f'http://127.0.0.1:{port}'
    browser_url = url + '/?' + urlencode(dict(mode='edge' if args.pi else 'local',
                                              edge_url=f'http://{ip}:9998' if args.pi else '',
                                              speed=30, horizon=300))
    print('혼잡한 교통 / 30배 / 300초를 선택하고 실험 시작을 누르세요.', flush=True)
    print('웹 관제 주소: ' + browser_url, flush=True)
    print('발표 중 이 창을 열어 두세요. 종료할 때 Ctrl+C를 누르세요.', flush=True)
    child = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', str(ROOT / 'demo_web_pi.py'),
                              '--port', str(port), '--out', str(ROOT / 'results/runs/laptop_presentation')],
                             cwd=ROOT, env=env)
    if not args.no_browser:
        for attempt in range(30):
            if child.poll() is not None:
                return child.returncode
            try:
                with urlopen(url + '/api/state', timeout=1):
                    break
            except (URLError, TimeoutError):
                time.sleep(0.2)
        else:
            raise RuntimeError('웹 서버가 준비되지 않았습니다. 이 창의 메시지를 확인하세요.')
        webbrowser.open(browser_url)
    try:
        return child.wait()
    except KeyboardInterrupt:
        try:
            return child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.terminate()
            return child.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print('실행을 완료하지 못했습니다: ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
