"""Local presentation server, with no additional web framework dependencies."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .protocol import MAX_BODY, json_bytes, strict_json


def make_server(demo, host='127.0.0.1', port=8000):
    if host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Presentation control server binds to localhost only')
    page = Path(__file__).with_name('dashboard.html').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, data, html=False):
            body = data if html else json_bytes(data)
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self.reply(200, page, True)
            elif self.path == '/api/state':
                self.reply(200, demo.snapshot())
            elif self.path == '/api/boot':
                self.reply(200, demo.boot())
            else:
                self.reply(404, dict(error='Not found'))

        def do_POST(self):
            origin = urlsplit(self.headers.get('Origin', ''))
            if origin.netloc and (origin.scheme != 'http' or origin.hostname not in ('localhost', '127.0.0.1')
                                  or origin.port != self.server.server_port):
                self.reply(403, dict(error='Origin rejected'))
                return
            try:
                if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('application/json required')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= MAX_BODY:
                    raise ValueError('Invalid body size')
                self.connection.settimeout(2)
                payload = strict_json(self.rfile.read(size))
                if self.path == '/api/start':
                    data = demo.start(payload)
                elif self.path == '/api/command':
                    data = demo.command(payload['action'])
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

    return ThreadingHTTPServer((host, port), Handler)


def serve(host, port, output, release):
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
