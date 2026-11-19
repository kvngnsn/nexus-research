import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus_research import providers
from nexus_research.chemistry import apply_rule, canonical
from nexus_research.reaction_checks import RULES
from nexus_research.research import Research, normalize_findings
from nexus_research.research_store import ResearchStore


class FakeVault:
    def __init__(self):
        self.values = {}

    def set(self, identity, key):
        self.values[identity] = key

    def get(self, identity):
        return self.values[identity]

    def delete(self, identity):
        self.values.pop(identity)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.manager = Research(self.root / "research", FakeVault())
        self.connection = self.manager.save_connection(
            {
                "name": "Synthetic local",
                "kind": "local",
                "model": "fixture",
                "endpoint": "http://127.0.0.1:1234/v1",
            }
        )
        self.count = patch(
            "nexus_research.research.input_count", return_value=(100, "provider_count")
        )
        self.call = patch(
            "nexus_research.research.api_call",
            return_value={
                "text": '{"findings":[]}',
                "usage": {"input": 100, "output": 50, "cached": 80, "reasoning": 10},
                "model": "fixture-v1",
            },
        )
        self.count.start()
        self.mock_call = self.call.start()

    def tearDown(self):
        self.manager.close()
        if self.manager.worker:
            self.manager.worker.join(timeout=3)
        self.count.stop()
        self.call.stop()
        self.temp.cleanup()

    def data(self, **changes):
        return {
            "targets": ["CCOC(C)=O"],
            "connection": self.connection["id"],
            "mode": "timed",
            "max_calls": 2,
            "output_tokens": 64,
            **changes,
        }

    def run_research(self, **changes):
        run = self.manager.start(self.data(**changes))
        self.manager.worker.join(timeout=3)
        self.assertFalse(self.manager.worker.is_alive())
        return self.manager.store.run(run["id"])

    def test_usage_does_not_double_count_cached_or_reasoning_tokens(self):
        run = self.run_research(total_tokens=350)
        self.assertEqual((run["input_tokens"], run["output_tokens"]), (200, 100))
        self.assertEqual((run["cached_tokens"], run["reasoning_tokens"]), (160, 20))
        self.assertEqual(run["calls"], 2)
        self.assertEqual(run["state"], "completed")

    def test_input_and_output_are_reserved_before_generation(self):
        run = self.run_research(total_tokens=128)
        self.assertEqual(run["state"], "limited")
        self.mock_call.assert_not_called()

    def test_dollar_reservation_prevents_generation(self):
        api = self.manager.save_connection(
            {
                "name": "Fake API",
                "kind": "openai",
                "model": "fixture",
                "key": "synthetic-secret-not-a-real-key",
            }
        )
        run = self.run_research(
            connection=api["id"], usd_limit=0.001, input_rate=100, output_rate=100
        )
        self.assertEqual(run["state"], "limited")
        self.mock_call.assert_not_called()

    def test_missing_usage_stops_further_calls(self):
        self.mock_call.return_value = {"text": "A partial note", "usage": {}, "model": "fixture"}
        run = self.run_research()
        self.assertEqual(run["calls"], 1)
        self.assertEqual(run["state"], "failed")
        self.assertTrue(run["usage_uncertain"])
        self.assertEqual(len(run["responses"]), 1)

    def test_secret_not_in_history_or_export(self):
        key = "synthetic-secret-not-a-real-key"
        api = self.manager.save_connection(
            {"name": "Research key alias", "kind": "openai", "model": "fixture", "key": key}
        )
        self.mock_call.return_value = {
            "text": f"Echo {key}",
            "usage": {"input": 10, "output": 5},
            "model": "fixture",
        }
        run = self.run_research(connection=api["id"], mode="single")
        result = self.manager.export(run["id"])
        self.assertNotIn(key, json.dumps(result))
        self.assertNotIn("opening", result)
        for path in list(self.manager.store.root.glob("research.sqlite*")) + list(
            self.manager.output_directory.iterdir()
        ):
            self.assertNotIn(key.encode(), path.read_bytes())

    def test_one_active_run_and_explicit_cancellation(self):
        entered, release = threading.Event(), threading.Event()

        def response(*args):
            entered.set()
            release.wait(2)
            return {
                "text": "Partial visible result",
                "usage": {"input": 10, "output": 5},
                "model": "fixture",
            }

        self.mock_call.side_effect = response
        run = self.manager.start(self.data())
        self.assertTrue(entered.wait(2))
        with self.assertRaises(ValueError):
            self.manager.start(self.data())
        self.manager.control(run["id"], "stop")
        release.set()
        self.manager.worker.join(timeout=3)
        saved = self.manager.store.run(run["id"])
        self.assertEqual(saved["state"], "stopped")
        self.assertEqual(saved["responses"][0]["text"], "Partial visible result")

    def test_resume_retains_original_deadline_and_usage(self):
        run = self.run_research()
        run.update(state="paused", calls=1)
        self.manager.store.save_run(run)
        deadline = run["deadline"]
        self.manager.control(run["id"], "resume")
        self.manager.worker.join(timeout=3)
        resumed = self.manager.store.run(run["id"])
        self.assertEqual(resumed["deadline"], deadline)
        self.assertEqual(resumed["input_tokens"], 300)
        self.assertEqual(resumed["calls"], 2)

    def test_resume_does_not_repeat_completed_single_target_analysis(self):
        run = self.run_research(mode="single")
        run["state"] = "paused"
        self.manager.store.save_run(run)
        self.mock_call.reset_mock()
        self.manager.control(run["id"], "resume")
        self.manager.worker.join(timeout=3)
        self.mock_call.assert_not_called()
        self.assertEqual(self.manager.store.run(run["id"])["state"], "completed")

    def test_rolling_token_window_blocks_a_second_generation(self):
        run = self.manager.start(self.data(tokens_per_minute=256))
        until = time.monotonic() + 2
        while time.monotonic() < until:
            saved = self.manager.store.run(run["id"])
            if saved["state"] == "waiting":
                break
            threading.Event().wait(0.01)
        self.assertEqual(saved["state"], "waiting")
        self.assertEqual(self.mock_call.call_count, 1)
        self.manager.control(run["id"], "stop")
        self.manager.worker.join(timeout=3)
        self.assertEqual(self.manager.store.run(run["id"])["state"], "stopped")

    def test_deadline_is_rechecked_after_input_counting(self):
        clock = [1000.0]

        def count(*_):
            clock[0] += 2
            return 100, "provider_count"

        with (
            patch("nexus_research.research.time.time", side_effect=lambda: clock[0]),
            patch("nexus_research.research.input_count", side_effect=count),
        ):
            run = self.run_research(runtime_seconds=1)
        self.assertEqual(run["state"], "limited")
        self.mock_call.assert_not_called()

    def test_unknown_inflight_usage_and_expired_deadlines_cannot_resume(self):
        run = self.run_research()
        for changes in [{"usage_uncertain": True}, {"deadline": time.time() - 1}]:
            run.update(state="paused", usage_uncertain=False, deadline=time.time() + 10, **{})
            run.update(changes)
            self.manager.store.save_run(run)
            with self.assertRaises(ValueError):
                self.manager.control(run["id"], "resume")

    def test_no_progress_stops_notes_without_calling_them_new_routes(self):
        self.mock_call.return_value["text"] = json.dumps(
            {"findings": [{"title": "An analysis note", "summary": "No route", "steps": []}]}
        )
        run = self.run_research(mode="finite", idle_tokens=128)
        self.assertEqual(run["state"], "no_progress")
        self.assertEqual(run["calls"], 1)
        self.assertFalse(run["findings"][0]["structural_candidate"])

    def test_duplicate_structural_routes_share_an_identity(self):
        target = canonical("CCOC(C)=O")
        rule, parts = next(
            (r, p) for r in RULES if (p := apply_rule(target, r["retro"], r["forward"])[0])
        )
        item = {
            "title": "First wording",
            "steps": [{"product": target, "reactants": list(parts[0])}],
        }
        a = normalize_findings(json.dumps({"findings": [item]}), target, [rule])[0]
        item["title"] = "Different wording"
        item["steps"][0]["reactants"].reverse()
        b = normalize_findings(json.dumps({"findings": [item]}), target, [rule])[0]
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(a["structural_candidate"])
        self.assertIsNone(a["estimated_cost"])

    def test_input_validation_and_cli_dollar_limit(self):
        for values in [
            {"targets": ["invalid"]},
            {"total_tokens": True},
            {"runtime_seconds": float("nan")},
            {"max_calls": 1000},
            {"usd_limit": 1},
        ]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.manager.config(self.data(**values))

    def proposal(self, reactants=None):
        return {
            "title": "Synthetic pathway",
            "steps": [{"product": "CCOC(C)=O", "reactants": reactants or ["CCO", "CC(=O)O"]}],
        }

    def test_objective_is_automatic_and_legacy_task_input_is_ignored(self):
        run = self.run_research(mode="single", task="Unwanted custom instruction")
        self.assertNotIn("task", run["config"])
        prompt = self.mock_call.call_args.args[1]
        self.assertNotIn("Unwanted custom instruction", prompt)
        self.assertIn("Investigate distinct candidate synthesis pathways", prompt)

    def test_local_history_excludes_the_same_pathway_after_restart(self):
        self.mock_call.return_value["text"] = json.dumps({"findings": [self.proposal()]})
        first = self.run_research(mode="single")
        self.assertEqual(len(first["findings"]), 1)
        self.manager = Research(self.root / "research", FakeVault())
        repeated = self.proposal(["OC(=O)C", "OCC"])
        repeated["title"] = "A different title for the same graph"
        self.mock_call.return_value["text"] = json.dumps({"findings": [repeated]})
        second = self.run_research(mode="single")
        self.assertEqual(second["findings"], [])
        self.assertEqual(second["duplicate_pathways"], 1)
        self.assertEqual(second["responses"][0]["duplicates_skipped"], 1)
        prompt = self.mock_call.call_args.args[1]
        self.assertIn('"total": 1', prompt)
        self.assertIn('"reactants": ["CC(=O)O", "CCO"]', prompt)
        self.assertNotIn("Supabase", prompt)
        self.assertEqual(self.manager.snapshot()["pathways"], {"count": 1, "scope": "local_only"})
        self.assertEqual(self.manager.store.run(first["id"])["findings"], first["findings"])

    def test_duplicate_pathway_does_not_reset_no_progress(self):
        self.mock_call.return_value["text"] = json.dumps({"findings": [self.proposal()]})
        self.run_research(mode="single")
        run = self.run_research(mode="finite", idle_tokens=128, max_calls=3)
        self.assertEqual(run["state"], "no_progress")
        self.assertEqual(run["calls"], 1)
        self.assertEqual(run["last_new_tokens"], [0])
        self.assertEqual(run["duplicate_pathways"], 1)

    def test_duplicate_items_in_one_response_count_once_as_discovery(self):
        first = self.proposal()
        second = self.proposal(["CC(=O)O", "CCO"])
        second["summary"] = "Different prose does not change the pathway."
        self.mock_call.return_value["text"] = json.dumps({"findings": [first, second]})
        run = self.run_research(mode="single")
        self.assertEqual(len(run["findings"]), 1)
        self.assertEqual(run["duplicate_pathways"], 1)
        self.assertEqual(self.manager.store.pathway_count(), 1)

    def test_different_precursor_graph_is_a_new_pathway(self):
        self.mock_call.return_value["text"] = json.dumps({"findings": [self.proposal()]})
        self.run_research(mode="single")
        self.mock_call.return_value["text"] = json.dumps(
            {"findings": [self.proposal(["CCO", "CC(=O)Cl"])]}
        )
        run = self.run_research(mode="single")
        self.assertEqual(len(run["findings"]), 1)
        self.assertEqual(run["duplicate_pathways"], 0)
        self.assertEqual(self.manager.store.pathway_count(), 2)

    def test_unverified_but_connected_pathways_are_also_deduplicated(self):
        self.mock_call.return_value["text"] = json.dumps(
            {"findings": [self.proposal(["CC", "CCO"])]}
        )
        first = self.run_research(mode="single")
        self.assertFalse(first["findings"][0]["structural_candidate"])
        second = self.run_research(mode="single")
        self.assertEqual(second["findings"], [])
        self.assertEqual(second["duplicate_pathways"], 1)

    def test_findings_are_written_before_the_next_model_call(self):
        calls = []

        def response(*args):
            if calls:
                files = list(self.manager.output_directory.glob("*.json"))
                saved = json.loads(files[0].read_text())
                self.assertEqual(len(saved["findings"]), 1)
                self.assertEqual(saved["calls"], 1)
            calls.append(1)
            return {
                "text": json.dumps({"findings": [self.proposal()]}),
                "usage": {"input": 100, "output": 50},
                "model": "fixture",
            }

        self.mock_call.side_effect = response
        run = self.run_research(max_calls=2)
        saved = json.loads((self.manager.output_directory / f"nexus-{run['id']}.json").read_text())
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["calls"], 2)
        self.assertNotIn("name", saved["config"]["connection"])
        self.assertNotIn("output_directory", saved)
        self.assertIn(
            "Synthetic pathway",
            (self.manager.output_directory / f"nexus-{run['id']}.md").read_text(),
        )

    def test_output_failure_stops_new_calls_and_retains_received_findings(self):
        from nexus_research.findings import OutputError, write_findings

        def fail_after_response(run):
            if run["calls"]:
                raise OutputError("Synthetic drive disconnected")
            write_findings(run)

        self.mock_call.return_value["text"] = json.dumps({"findings": [self.proposal()]})
        with patch("nexus_research.research.write_findings", side_effect=fail_after_response):
            run = self.run_research(max_calls=3)
        self.assertEqual(run["state"], "output_unavailable")
        self.assertEqual(run["calls"], 1)
        self.assertEqual(len(run["findings"]), 1)
        self.assertIsNone(self.manager.active)
        self.manager.control(run["id"], "resume")
        self.manager.worker.join(timeout=3)
        saved = self.manager.store.run(run["id"])
        self.assertEqual(saved["state"], "completed")
        self.assertEqual(saved["calls"], 3)

    def test_missing_folder_prevents_any_provider_call_and_is_not_recreated(self):
        folder = self.root / "mounted drive"
        folder.mkdir()
        self.manager.set_output_directory(str(folder.resolve()))
        folder.rmdir()
        with self.assertRaises(ValueError):
            self.manager.start(self.data())
        self.mock_call.assert_not_called()
        self.assertFalse(folder.exists())
        self.assertFalse(self.manager.store.runs())

    def test_folder_selection_persists_and_old_runs_keep_their_destination(self):
        first = self.run_research(mode="single")
        folder = self.root / "chosen folder"
        folder.mkdir()
        self.manager.set_output_directory(str(folder.resolve()))
        self.manager = Research(self.root / "research", FakeVault())
        self.assertEqual(self.manager.output_directory, folder.resolve())
        second = self.run_research(mode="single")
        self.assertTrue((folder / f"nexus-{second['id']}.json").is_file())
        self.assertNotEqual(first["output_directory"], second["output_directory"])
        self.assertFalse((folder / f"nexus-{first['id']}.json").exists())

    def test_stop_preserves_partial_cli_output_to_disk(self):
        connection = self.manager.save_connection(
            {"name": "Synthetic CLI", "kind": "codex", "model": "default"}
        )

        def fail(*args):
            from nexus_research.providers import ProviderError

            raise ProviderError(
                "Synthetic interruption",
                partial={"text": "Delivered CLI text", "usage": {}, "model": "fixture"},
            )

        with (
            patch("nexus_research.research.availability", return_value="Installed"),
            patch("nexus_research.research.cli_call", side_effect=fail),
        ):
            run = self.run_research(connection=connection["id"], mode="single")
        self.assertEqual(run["state"], "failed")
        self.assertTrue(run["usage_uncertain"])
        self.assertTrue(run["responses"][0]["partial"])
        self.assertIn(
            "Delivered CLI text",
            (self.manager.output_directory / f"nexus-{run['id']}.md").read_text(),
        )

    def test_no_reporting_or_contract_tables_in_new_store(self):
        with self.manager.store.connect() as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertFalse(tables & {"outbox", "openings"})
        self.assertNotIn("sync", self.manager.snapshot())


class StoreTests(unittest.TestCase):
    def test_run_and_event_are_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory))
            with store.connect() as db:
                db.executescript(
                    "CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'fixture'); END;"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                store.save_run({"id": "fixture", "state": "running"}, "run.started")
            with store.connect() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM events").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM runs").fetchone()[0], 0)

    def test_crash_recovery_marks_inflight_usage_unknown_without_auto_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = ResearchStore(root)
            store.save_run({"id": "fixture", "state": "running", "inflight": True})
            recovered = ResearchStore(root).run("fixture")
            self.assertEqual(recovered["state"], "interrupted")
            self.assertTrue(recovered["usage_uncertain"])


class ProviderTests(unittest.TestCase):
    def test_local_url_rejects_external_hosts_and_credentials(self):
        for url in [
            "https://api.example.com/v1",
            "http://user:key@localhost/v1",
            "http://localhost/v1?key=secret",
            "file:///v1",
        ]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                providers.local_endpoint(url)

    def test_native_launch_finds_user_local_cli_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            executable = home / ".local/bin/claude"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o700)
            with (
                patch.object(providers.Path, "home", return_value=home),
                patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}),
            ):
                self.assertEqual(providers.cli_path("claude"), str(executable))

    def test_cli_restrictions_and_prompt_not_in_argv(self):
        with patch.object(providers, "cli_path", return_value="/fixture/cli"):
            codex = providers.cli_command({"kind": "codex", "model": "default"})
            claude = providers.cli_command({"kind": "claude", "model": "default"})
        self.assertIn("features.shell_tool=false", codex)
        self.assertIn("read-only", codex)
        self.assertIn("--restricted", claude)
        self.assertEqual(claude[claude.index("--tools") + 1], "")
        self.assertIn("mcp__*", claude)

    def test_cli_hidden_reasoning_is_ignored(self):
        value = providers.parse_cli_line(
            "codex",
            {
                "type": "item.completed",
                "item": {"type": "reasoning", "text": "fixture hidden reasoning"},
            },
        )
        self.assertEqual(value, {})

    def test_openai_request_limits_output_and_discards_hidden_items(self):
        data = {
            "output": [
                {"type": "reasoning", "content": [{"text": "hidden fixture"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "visible"}]},
            ],
            "usage": {"input_tokens": 20, "output_tokens": 10},
            "model": "fixture-version",
        }
        with patch.object(providers, "request_json", return_value=data) as call:
            result = providers.api_call(
                {"kind": "openai", "model": "fixture"}, "prompt", 64, "fake-key", 10
            )
        self.assertEqual(result["text"], "visible")
        self.assertFalse(call.call_args.args[1]["store"])
        self.assertEqual(call.call_args.args[1]["max_output_tokens"], 64)

    def test_cli_stream_capture_ignores_reasoning_and_accounts_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "fixture.py"
            events = [
                {"type": "item.completed", "item": {"type": "reasoning", "text": "hidden fixture"}},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Visible fixture"},
                },
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
            ]
            script.write_text(
                "import sys\nsys.stdin.read()\n"
                + "\n".join(f"print({json.dumps(json.dumps(x))}, flush=True)" for x in events)
            )
            with patch.object(providers, "cli_command", return_value=[sys.executable, str(script)]):
                result = providers.cli_call(
                    {"kind": "codex"}, "synthetic prompt", threading.Event(), 2, directory
                )
            self.assertEqual(result["text"], "Visible fixture")
            self.assertEqual(result["usage"]["output"], 5)
