"""Stateless NumPy inference service. No SUMO installation needed on Pi."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import threading
import platform
import numpy as np

from .protocol import MAX_BODY, infer, json_bytes, strict_json
from .release import DEFAULT_RELEASE, load_release


def make_server(host='127.0.0.1', port=9998, release=DEFAULT_RELEASE):
    policy, identity = load_release(release)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data):
            body = json_bytes(data)
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/health':
                self.reply(200, dict(status='ready', hostname=socket.gethostname(), identity=identity,
                                     system=platform.system(), machine=platform.machine(),
                                     python=platform.python_version(), numpy=np.__version__))
            else:
                self.reply(404, dict(error='Not found'))

        def do_POST(self):
            if self.path != '/infer':
                self.reply(404, dict(error='Not found'))
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('Invalid request size')
                self.connection.settimeout(2)
                payload = strict_json(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('JSON object required')
                with lock:
                    result = infer(policy, identity, payload)
                self.reply(200, result)
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                self.reply(400, dict(error=str(exc)))
            except TimeoutError:
                self.reply(408, dict(error='Request timed out'))

    return ThreadingHTTPServer((host, port), Handler)


def serve(host, port, release):
    server = make_server(host, port, release)
    print(f'Edge ready: http://{host}:{server.server_port} ({socket.gethostname()})', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
