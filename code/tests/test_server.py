import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from nexus_research import server
from nexus_research.research import Research
from tests.fakes import MemoryVault


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "research"
        self.paths = patch.object(server, "RESEARCH_ROOT", self.root)
        self.paths.start()
        self.factory = patch.object(
            server,
            "Research",
            side_effect=lambda root, **kwargs: Research(root, MemoryVault()),
        )
        self.factory.start()
        self.logs = patch.object(server.Handler, "log_message")
        self.logs.start()
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.http.daemon_threads = True
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.headers = {"X-Nexus-Token": server.TOKEN}

    def tearDown(self):
        if hasattr(self.http, "research"):
            self.http.research.close()
            if self.http.research.worker:
                self.http.research.worker.join(timeout=3)
        self.http.shutdown()
        self.http.server_close()
        self.thread.join(timeout=3)
        self.paths.stop()
        self.factory.stop()
        self.logs.stop()
        self.temp.cleanup()

    def call(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.http.server_port, timeout=5)
        try:
            connection.request(
                "GET" if body is None else "POST",
                path,
                body=None if body is None else json.dumps(body),
                headers={"Content-Type": "application/json", **(headers or {})},
            )
            response = connection.getresponse()
            data = response.read()
            return response.status, data
        finally:
            connection.close()

    def test_root_is_research_only_and_does_not_open_a_database(self):
        status, page = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Nexus Research</title>", page)
        self.assertNotIn(b"<nav", page)
        self.assertNotIn(b"Route explorer", page)
        self.assertNotIn(b"Synthesis Economics", page)
        self.assertNotIn(b"Research task", page)
        self.assertNotIn(b'id="task"', page)
        self.assertFalse(self.root.exists())
        status, data = self.call("/api/health")
        self.assertEqual(json.loads(data)["subapps"], ["research"])

    def test_removed_pages_and_catalog_apis_are_unavailable(self):
        for path in ["/economics/", "/routes/", "/api/catalog", "/data/research.sqlite"]:
            with self.subTest(path=path):
                self.assertEqual(self.call(path)[0], 404)
        for path in ["/api/search", "/api/economics", "/api/material", "/api/rule"]:
            with self.subTest(path=path):
                self.assertEqual(self.call(path, {}, self.headers)[0], 404)
        self.assertFalse(self.root.exists())

    def test_host_and_origin_boundary(self):
        self.assertEqual(self.call("/api/workspace", headers={"Host": "external.test"})[0], 403)
        headers = dict(self.headers, Origin="https://external.test")
        self.assertEqual(
            self.call("/api/research/output", {"directory": str(self.root)}, headers)[0], 403
        )
        self.assertFalse(self.root.exists())

    def test_session_token_is_required_without_any_contract_gate(self):
        self.assertEqual(self.call("/api/research/status")[0], 403)
        status, data = self.call("/api/research/status", headers=self.headers)
        self.assertEqual(status, 200)
        self.assertIn("output", json.loads(data))
        self.assertEqual(self.call("/api/opening", {}, self.headers)[0], 404)
        self.assertEqual(set(json.loads(self.call("/api/workspace")[1])), {"token"})

    def test_fonts_and_arbitrary_smiles_preview_are_local(self):
        status, font = self.call("/shared/fonts/SourceCodePro-Regular.woff2")
        self.assertEqual((status, font[:4]), (200, b"wOF2"))
        status, drawing = self.call("/api/structure?smiles=CC(C)OC(=O)c1ccccc1")
        self.assertEqual(status, 200)
        self.assertIn(b"<svg", drawing)
        self.assertEqual(self.call("/api/structure?smiles=invalid")[0], 400)
        self.assertFalse(self.root.exists())

    def test_reporting_endpoints_are_removed(self):
        for action in ("connect", "disconnect", "check", "sync"):
            self.assertEqual(
                self.call(f"/api/research/database/{action}", {}, self.headers)[0], 404
            )

    def test_output_selection_checks_path_and_persists(self):
        folder = Path(self.temp.name) / "chosen findings"
        folder.mkdir()
        status, body = self.call("/api/research/output", {"directory": str(folder)}, self.headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["directory"], str(folder.resolve()))
        for path in ("", "relative/path", str(folder / "missing"), None):
            self.assertEqual(
                self.call("/api/research/output", {"directory": path}, self.headers)[0], 400
            )
        self.assertEqual(self.http.research.output_directory, folder.resolve())

    def test_research_saves_to_disk_without_a_reporting_service(self):
        status, data = self.call(
            "/api/research/connection",
            {
                "name": "Synthetic local",
                "kind": "local",
                "model": "fixture",
                "endpoint": "http://127.0.0.1:1234/v1",
            },
            self.headers,
        )
        self.assertEqual(status, 200)
        identity = json.loads(data)["id"]
        response = {
            "text": json.dumps(
                {
                    "findings": [
                        {
                            "title": "Synthetic proposal",
                            "steps": [{"product": "CCOC(C)=O", "reactants": ["CC(=O)O", "CCO"]}],
                        }
                    ]
                }
            ),
            "usage": {"input": 100, "output": 50},
            "model": "fixture",
        }
        with (
            patch("nexus_research.research.input_count", return_value=(100, "fixture")),
            patch("nexus_research.research.api_call", return_value=response),
        ):
            status, data = self.call(
                "/api/research/start",
                {
                    "targets": ["CCOC(C)=O"],
                    "connection": identity,
                    "mode": "single",
                    "output_tokens": 64,
                },
                self.headers,
            )
            self.assertEqual(status, 200)
            run_id = json.loads(data)["id"]
            self.http.research.worker.join(timeout=3)
        status, data = self.call(f"/api/research/export?id={run_id}", headers=self.headers)
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["calls"], 1)
        self.assertTrue(result["findings"][0]["structural_candidate"])
        self.assertNotIn("opening", result)
        self.assertIsNone(result["findings"][0]["estimated_cost"])
        self.assertFalse(list(Path(self.temp.name).rglob("catalog.sqlite")))
        status, data = self.call("/api/research/status", headers=self.headers)
        folder = Path(json.loads(data)["output"]["directory"])
        self.assertEqual(json.loads((folder / f"nexus-{run_id}.json").read_text()), result)
        self.assertTrue((folder / f"nexus-{run_id}.md").is_file())
        self.assertEqual(json.loads(data)["pathways"], {"count": 1, "scope": "local_only"})
