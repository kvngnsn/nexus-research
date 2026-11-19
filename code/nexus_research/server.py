import json
import mimetypes
import os
import secrets
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .chemistry import canonical, svg
from .config import OUTPUT_ROOT, RESEARCH_ROOT, WEB_ROOT
from .research import Research

ROOT = WEB_ROOT
TOKEN = secrets.token_urlsafe(24)
WORKSPACE_LOCK = Lock()


def workspace(server):
    with WORKSPACE_LOCK:
        if not hasattr(server, "research"):
            server.research = Research(RESEARCH_ROOT, default_output=OUTPUT_ROOT)
    return server.research


class Handler(BaseHTTPRequestHandler):
    def send(self, data, status=200, mime="application/json"):
        body = (
            (json.dumps(data, allow_nan=False) if mime == "application/json" else data).encode()
            if not isinstance(data, bytes)
            else data
        )
        self.send_response(status)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def trusted_host(self):
        return self.headers.get("Host") in {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }

    def do_GET(self):
        if not self.trusted_host():
            return self.send({"error": "Local access only."}, 403)
        p = urlsplit(self.path)
        if p.path == "/api/workspace":
            return self.send({"token": TOKEN})
        if p.path.startswith("/api/research/"):
            if self.headers.get("X-Nexus-Token") != TOKEN:
                return self.send({"error": "Open the workspace first."}, 403)
            manager = workspace(self.server)
            try:
                if p.path == "/api/research/status":
                    return self.send(manager.snapshot())
                identity = parse_qs(p.query).get("id", [""])[0]
                if p.path == "/api/research/run":
                    return self.send(manager.store.run(identity))
                if p.path == "/api/research/export":
                    return self.send(manager.export(identity))
                return self.send({"error": "Not found."}, 404)
            except ValueError as exc:
                return self.send({"error": str(exc)}, 400)
        if p.path == "/api/health":
            return self.send(
                {
                    "ok": True,
                    "app": "Nexus Research",
                    "version": __version__,
                    "subapps": ["research"],
                }
            )
        if p.path == "/api/structure":
            try:
                smi = canonical(parse_qs(p.query).get("smiles", [""])[0])
                return self.send(svg(smi, 280, 140), mime="image/svg+xml")
            except ValueError as e:
                return self.send({"error": str(e)}, 400)
        static = {
            "/": "research/index.html",
            "/research/app.js": "research/app.js",
            "/research/style.css": "research/style.css",
            "/shared/theme.css": "shared/theme.css",
            "/shared/utils.js": "shared/utils.js",
            "/shared/fonts/SourceCodePro-Regular.woff2": "shared/fonts/SourceCodePro-Regular.woff2",
            "/shared/fonts/SourceCodePro-Semibold.woff2": "shared/fonts/SourceCodePro-Semibold.woff2",
        }
        if p.path not in static:
            return self.send({"error": "Not found."}, 404)
        file = ROOT / static[p.path]
        return self.send(file.read_bytes(), mime=mimetypes.guess_type(file.name)[0] or "text/plain")

    def do_POST(self):
        if not self.trusted_host() or self.headers.get("X-Nexus-Token") != TOKEN:
            return self.send({"error": "Reload this local page and try again."}, 403)
        origin = self.headers.get("Origin")
        if origin and origin not in {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }:
            return self.send({"error": "Local access only."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100000:
                raise ValueError("Request size is invalid.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("Expected a JSON object.")
            if self.path.startswith("/api/research/"):
                manager = workspace(self.server)
                if self.path == "/api/research/output":
                    return self.send(manager.set_output_directory(data.get("directory")))
                if self.path == "/api/research/connection":
                    return self.send(manager.save_connection(data))
                if self.path == "/api/research/connection/delete":
                    manager.delete_connection(data.get("id"))
                    return self.send({"ok": True})
                if self.path == "/api/research/start":
                    return self.send(manager.start(data))
                if self.path == "/api/research/control":
                    return self.send(manager.control(data.get("id"), data.get("action")))
                return self.send({"error": "Not found."}, 404)
            return self.send({"error": "Not found."}, 404)
        except (ValueError, TypeError, KeyError) as e:
            self.send({"error": str(e)}, 400)
        except Exception as e:
            print(type(e).__name__, str(e), file=sys.stderr)
            self.send({"error": "Request failed. Check the local service log."}, 500)

    def log_message(self, format, *args):
        if args and "api/health" not in str(args[0]):
            super().log_message(format, *args)


def main():
    """Serve the checkout on loopback; importing this module does not open a database."""
    port = int(os.environ.get("NEXUS_PORT", "8774"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    signal.signal(signal.SIGTERM, lambda *_: Thread(target=server.shutdown, daemon=True).start())
    if path := os.environ.get("NEXUS_READY_FILE"):
        Path(path).write_text(json.dumps({"port": server.server_port}))
    print(f"Nexus Research ready at http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if hasattr(server, "research"):
            server.research.close()
            if server.research.worker:
                server.research.worker.join(timeout=3)
        server.server_close()


if __name__ == "__main__":
    main()
