"""Local presentation server, with no additional web framework dependencies."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .protocol import EdgeClient, MAX_BODY, json_bytes, strict_json


def make_server(demo, host='127.0.0.1', port=8000):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Presentation control server binds to localhost only')
    page = Path(__file__).with_name('unseen_dashboard.html' if getattr(demo, 'trial_view', False) else 'dashboard.html').read_bytes()
    renderer = Path(__file__).with_name('traffic_map.js').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data, html=False, content_type=None):
            body = data if html or content_type else json_bytes(data)
            self.send_response(code)
            self.send_header('Content-Type', content_type or ('text/html; charset=utf-8' if html else 'application/json; charset=utf-8'))
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if urlsplit(self.path).path in ('/', '/index.html'):
                self.reply(200, page, True)
            elif self.path == '/traffic-map.js':
                self.reply(200, renderer, content_type='text/javascript; charset=utf-8')
            elif self.path == '/api/state':
                self.reply(200, demo.snapshot())
            elif self.path == '/api/boot':
                self.reply(200, demo.boot())
            else:
                self.reply(404, dict(error='Not found'))

        def do_POST(self):
            try:
                origin = urlsplit(self.headers.get('Origin', ''))
                if origin.netloc and (origin.scheme != 'http' or origin.hostname not in ('localhost', '127.0.0.1')
                                      or origin.port != self.server.server_port):
                    self.reply(403, dict(error='Origin rejected'))
                    return
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('application/json required')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('Invalid body size')
                self.connection.settimeout(2)
                payload = strict_json(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError('JSON object required')
                if self.path == '/api/start':
                    data = demo.start(payload)
                elif self.path == '/api/command':
                    data = demo.command(payload['action'])
                elif self.path == '/api/network':
                    data = demo.select_network(payload['network'])
                elif self.path == '/api/speed':
                    data = demo.set_speed(payload['speed'])
                elif self.path == '/api/edge-check':
                    data = demo.check_edge(payload['url'])
                elif self.path == '/api/check-edge':
                    client = EdgeClient(payload['edge_url'], demo.identity)
                    try:
                        data = dict(status='ready', url=client.url, health=client.check())
                    except Exception as exc:
                        data = dict(status='unavailable', url=client.url, error=f'{type(exc).__name__}: {exc}')
                elif self.path == '/api/select-network':
                    if not getattr(demo, 'trial_view', False):
                        raise ValueError('This endpoint requires the new-intersection trial mode')
                    data = demo.select_network(payload['case'])
                else:
                    self.reply(404, dict(error='Not found'))
                    return
                self.reply(200, data)
            except RuntimeError as exc:
                self.reply(409, dict(error=str(exc)))
            except (ValueError, KeyError, TypeError) as exc:
                self.reply(400, dict(error=str(exc)))
            except TimeoutError:
                self.reply(408, dict(error='Request timed out'))
            except OSError as exc:
                self.reply(502, dict(error=f'Inference server connection failed: {exc}'))

    return ThreadingHTTPServer((host, port), Handler)


def serve(host, port, output, release, unseen=False):
    if unseen:
        from .unseen import TrialDemo as Demo
    else:
        from .runtime import Demo
    demo = Demo(output, release)
    server = make_server(demo, host, port)
    print(f'Smart Signal: http://{host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        demo.close()
