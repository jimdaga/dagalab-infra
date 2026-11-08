import http.client, importlib.util, pathlib, socket, sys, threading, time, unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'files'))
spec = importlib.util.spec_from_file_location('server', HERE.parent / 'files' / 'server.py')
sv = importlib.util.module_from_spec(spec); spec.loader.exec_module(sv)


class ServerTest(unittest.TestCase):
    def start(self, command):
        self.procs = []
        srv = sv.make_server('127.0.0.1', 0, command, on_spawn=self.procs.append)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1]

    def test_streams_command_output(self):
        port = self.start(lambda seconds: ['sh', '-c', f'printf "seconds=%s" {seconds}'])
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        c.request('GET', '/guide.ts')
        r = c.getresponse()
        self.assertEqual((r.status, r.getheader('Content-Type')), (200, 'video/mp2t'))
        self.assertEqual(r.read(), b'seconds=60')

    def test_seconds_query(self):
        port = self.start(lambda seconds: ['sh', '-c', f'printf {seconds}'])
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        c.request('GET', '/guide.ts?seconds=15')
        self.assertEqual(c.getresponse().read(), b'15')

    def test_health_and_404(self):
        port = self.start(lambda seconds: ['true'])
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
        c.request('GET', '/health'); r = c.getresponse()
        self.assertEqual((r.status, r.read()), (200, b'ok'))
        c.request('GET', '/nope'); r = c.getresponse(); r.read()
        self.assertEqual(r.status, 404)

    def test_disconnect_kills_renderer(self):
        port = self.start(lambda seconds: ['yes', 'x' * 1000])     # endless output, like a live render
        s = socket.create_connection(('127.0.0.1', port))
        s.sendall(b'GET /guide.ts HTTP/1.1\r\nHost: x\r\n\r\n')
        self.assertTrue(s.recv(65536))
        s.close()
        deadline = time.time() + 5
        while time.time() < deadline and self.procs[0].poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(self.procs[0].poll(), 'renderer still running after the client left')


if __name__ == '__main__':
    unittest.main()
