import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from nexus_research.providers import ProviderError, request_json


class CancellationTests(unittest.TestCase):
    def test_inflight_http_request_is_interrupted_on_cancel(self):
        entered, release, cancel = threading.Event(), threading.Event(), threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                entered.set()
                release.wait(5)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                except OSError:
                    pass

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with ThreadPoolExecutor(1) as pool:
                result = pool.submit(
                    request_json, f"http://127.0.0.1:{server.server_port}/", {}, {}, 10, cancel
                )
                self.assertTrue(entered.wait(2))
                started = time.monotonic()
                cancel.set()
                with self.assertRaises(ProviderError):
                    result.result(timeout=2)
                self.assertLess(time.monotonic() - started, 2)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
