"""Verify the frozen app using isolated files and a synthetic localhost model."""

import json
import os
import plistlib
import sqlite3
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist/Nexus Research.app"


class FixtureProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == "/v1/chat/completions"
        self.rfile.read(int(self.headers["Content-Length"]))
        self.server.calls += 1
        body = json.dumps(
            {
                "model": "synthetic-fixture",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "findings": [
                                        {
                                            "title": "Synthetic proposal",
                                            "summary": "Isolated verification only.",
                                            "steps": [
                                                {
                                                    "product": "CCOC(C)=O",
                                                    "reactants": ["CCO", "CC(=O)O"],
                                                }
                                            ],
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    if not APP.exists():
        raise SystemExit("Build the native app with make app first.")
    info = plistlib.loads((APP / "Contents/Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "org.nexusresearch.desktop"
    assert "NexusQADataDirectory" not in info
    resources = APP / "Contents/Resources"
    assert (resources / info["CFBundleIconFile"]).read_bytes().startswith(b"icns")
    assert (resources / "LICENSE").read_text().startswith("MIT License")
    for pattern in ("*.sqlite", "supabase.ini", "database.json", "terms.md", "issue_database.py"):
        assert not any(APP.rglob(pattern)), f"Private or obsolete file bundled: {pattern}"
    assert any(APP.rglob("cacert.pem"))
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(APP)], check=True)
    provider = ThreadingHTTPServer(("127.0.0.1", 0), FixtureProvider)
    provider.daemon_threads = True
    provider.calls = 0
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-public-qa-") as directory:
            root = Path(directory)
            ready = root / "ready.json"
            chosen = root / "chosen drive"
            chosen.mkdir()
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith(("PYTHON", "NEXUS_", "ATLAS_"))
            }
            env.update(
                PATH="/usr/bin:/bin",
                NEXUS_PORT="0",
                NEXUS_DATA_DIR=str(root / "data"),
                NEXUS_RESEARCH_DIR=str(root / "research"),
                NEXUS_OUTPUT_DIR=str(root / "default findings"),
                NEXUS_READY_FILE=str(ready),
            )
            with (root / "engine.log").open("w") as log:
                process = subprocess.Popen(
                    [str(resources / "Engine/NexusEngine")],
                    cwd=root,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                try:
                    deadline = time.monotonic() + 30
                    while not ready.exists():
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError((root / "engine.log").read_text())
                        time.sleep(0.05)
                    base = f"http://127.0.0.1:{json.loads(ready.read_text())['port']}"

                    def request(path, body=None, headers=None):
                        return urlopen(
                            Request(
                                base + path,
                                data=None if body is None else json.dumps(body).encode(),
                                headers={"Content-Type": "application/json", **(headers or {})},
                            ),
                            timeout=20,
                        )

                    def call(path, body=None, headers=None):
                        with request(path, body, headers) as response:
                            return json.loads(response.read())

                    health = call("/api/health")
                    assert health["version"] == info["CFBundleShortVersionString"]
                    headers = {"X-Nexus-Token": call("/api/workspace")["token"]}
                    with request("/") as response:
                        page = response.read()
                    assert b'id="outputDirectory"' in page
                    assert b'id="opening"' not in page and b'id="databaseStatus"' not in page
                    for path, body in [
                        ("/api/opening", {}),
                        ("/api/catalog", None),
                        ("/api/search", {}),
                    ] + [
                        (f"/api/research/database/{action}", {})
                        for action in ("connect", "disconnect", "check", "sync")
                    ]:
                        try:
                            request(path, body, headers).close()
                            raise AssertionError(f"Removed route available: {path}")
                        except HTTPError as exc:
                            assert exc.code == 404
                    status = call("/api/research/status", headers=headers)
                    assert not status["runs"] and "sync" not in status
                    assert provider.calls == 0
                    call("/api/research/output", {"directory": str(chosen)}, headers)
                    connection = call(
                        "/api/research/connection",
                        {
                            "name": "Synthetic local",
                            "kind": "local",
                            "model": "synthetic-fixture",
                            "endpoint": f"http://127.0.0.1:{provider.server_port}/v1",
                        },
                        headers,
                    )
                    run = call(
                        "/api/research/start",
                        {
                            "targets": ["CCOC(C)=O"],
                            "connection": connection["id"],
                            "mode": "single",
                            "output_tokens": 64,
                        },
                        headers,
                    )
                    deadline = time.monotonic() + 15
                    while call("/api/research/status", headers=headers)["active"]:
                        assert time.monotonic() < deadline, "Synthetic run timed out"
                        time.sleep(0.05)
                    result = call(f"/api/research/export?id={run['id']}", headers=headers)
                    assert result["state"] == "completed", result.get("detail")
                    assert len(result["findings"]) == 1 and provider.calls == 1
                    assert json.loads((chosen / f"nexus-{run['id']}.json").read_text()) == result
                    assert "Synthetic proposal" in (chosen / f"nexus-{run['id']}.md").read_text()
                    assert (
                        "output_directory" not in result
                        and "name" not in result["config"]["connection"]
                    )
                    with sqlite3.connect(root / "research/research.sqlite") as db:
                        tables = {
                            row[0]
                            for row in db.execute(
                                "SELECT name FROM sqlite_master WHERE type='table'"
                            )
                        }
                        assert not tables & {"outbox", "openings"}
                        assert (
                            db.execute(
                                "SELECT value FROM metadata WHERE key='schema_version'"
                            ).fetchone()[0]
                            == "4"
                        )
                    with request("/shared/fonts/SourceCodePro-Regular.woff2") as response:
                        assert response.read(4) == b"wOF2"
                    print(
                        json.dumps(
                            {
                                "local_research": True,
                                "chosen_folder_json_and_markdown": True,
                                "reporting_and_contract_routes_removed": True,
                                "private_config_absent": True,
                                "signature_verified": True,
                                "paid_calls": 0,
                                "synthetic_local_calls": provider.calls,
                            }
                        )
                    )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
