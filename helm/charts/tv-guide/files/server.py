#!/usr/bin/env python3
"""TV Guide sidecar: http://127.0.0.1:8410/guide.ts renders the guide (render.py) for as long as the client reads.

Runs next to ErsatzTV in its pod; the remote stream definition (streams/tv-guide.yml) points `url:` here. A `script:`
stream would be simpler, but ErsatzTV 26.12's scanner can't probe one (its signed internal URL 404s across processes),
so the channel would have no video stream. Each request gets its own renderer, killed when the client goes away:
nothing renders while nobody watches. /health answers the kubelet probes.
"""
import os, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RENDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'render.py')
CHUNK = 64 * 1024


def render_command(seconds):
    return [sys.executable, RENDER, '--seconds', str(seconds)]


def make_server(host, port, command=render_command, on_spawn=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass                                       # ErsatzTV probes and plays a lot; keep the log quiet

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == '/health':
                self.send_response(200); self.send_header('Content-Length', '2'); self.end_headers()
                self.wfile.write(b'ok')
                return
            if url.path != '/guide.ts':
                self.send_error(404)
                return
            try:
                seconds = max(1, min(3600, int(parse_qs(url.query).get('seconds', ['60'])[0])))
            except ValueError:
                seconds = 60
            proc = subprocess.Popen(command(seconds), stdout=subprocess.PIPE)
            if on_spawn:
                on_spawn(proc)
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp2t')
                self.end_headers()
                while chunk := proc.stdout.read1(CHUNK):
                    self.wfile.write(chunk)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                self.close_connection = True

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == '__main__':
    make_server('127.0.0.1', int(os.environ.get('TVG_PORT', '8410'))).serve_forever()
