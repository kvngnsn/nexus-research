import copy
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nexus_research.pathways import pathway_record
from nexus_research.research_store import ResearchStore

TARGET = "CCOC(C)=O"
STEPS = [{"product": TARGET, "reactants": ["CCO", "CC(=O)O"]}]


def finding():
    return {
        "id": pathway_record(TARGET, STEPS)["id"],
        "target": TARGET,
        "steps": copy.deepcopy(STEPS),
        "structural_candidate": True,
        "call": 1,
    }


def run_fixture(identity):
    return {
        "id": identity,
        "state": "running",
        "config": {"targets": [TARGET]},
        "calls": 1,
        "findings": [],
        "responses": [{"text": "Synthetic response"}],
        "last_new_seconds": [0],
        "target_seconds": [5],
        "last_new_tokens": [0],
        "target_tokens": [150],
    }


class PathwayTests(unittest.TestCase):
    def test_step_order_and_equivalent_smiles_share_an_identity(self):
        first = STEPS + [{"product": "CCO", "reactants": ["CC=O"]}]
        second = [
            {"product": "OCC", "reactants": ["O=CC"]},
            {"product": "CC(=O)OCC", "reactants": ["OC(=O)C", "OCC"]},
        ]
        self.assertEqual(
            pathway_record(TARGET, first)["id"], pathway_record("CC(=O)OCC", second)["id"]
        )

    def test_notes_detached_steps_cycles_and_empty_reactants_are_not_pathways(self):
        for steps in [
            [],
            STEPS + [{"product": "CCC", "reactants": ["CC"]}],
            STEPS + [{"product": "CCO", "reactants": [TARGET]}],
            [{"product": TARGET, "reactants": []}],
        ]:
            with self.subTest(steps=steps):
                self.assertIsNone(pathway_record(TARGET, steps))

    def test_stereochemistry_and_reactant_multiplicity_are_preserved(self):
        left = [{"product": "C[C@H](O)CC", "reactants": ["CCC(C)=O"]}]
        right = [{"product": "C[C@@H](O)CC", "reactants": ["CCC(C)=O"]}]
        self.assertNotEqual(
            pathway_record(left[0]["product"], left)["id"],
            pathway_record(right[0]["product"], right)["id"],
        )
        repeated = [{"product": TARGET, "reactants": ["CCO", "CCO", "CC(=O)O"]}]
        self.assertNotEqual(
            pathway_record(TARGET, STEPS)["id"], pathway_record(TARGET, repeated)["id"]
        )

    def test_migration_indexes_all_history_without_rewriting_old_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "research.sqlite") as db:
                db.executescript(
                    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                    "INSERT INTO metadata VALUES ('schema_version', '1');"
                    "CREATE TABLE runs (id TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL, payload TEXT NOT NULL);"
                )
                old = run_fixture("oldest")
                old.update(state="completed", findings=[finding()])
                original = json.dumps(old)
                db.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?)", ("oldest", "completed", 0, original)
                )
                for index in range(101):
                    recent = {"id": f"recent-{index}", "state": "completed", "findings": []}
                    db.execute(
                        "INSERT INTO runs VALUES (?, ?, ?, ?)",
                        (recent["id"], "completed", index + 1, json.dumps(recent)),
                    )
            store = ResearchStore(root)
            self.assertEqual(len(store.runs()), 100)
            self.assertEqual(store.pathway_count(TARGET), 1)
            with store.connect() as db:
                self.assertEqual(
                    db.execute("SELECT payload FROM runs WHERE id='oldest'").fetchone()[0], original
                )
                self.assertEqual(
                    db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[
                        0
                    ],
                    "4",
                )
            self.assertEqual(ResearchStore(root).pathway_count(), 1)

    def test_pathway_claim_and_response_rollback_together(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory))
            run = run_fixture("atomic")
            store.save_run(run)
            before = copy.deepcopy(run)
            with store.connect() as db:
                db.executescript(
                    "CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'fixture'); END;"
                )
            with self.assertRaises(sqlite3.IntegrityError):
                store.complete_response(run, [finding()], 0)
            self.assertEqual(run, before)
            self.assertEqual(store.pathway_count(), 0)
            self.assertEqual(store.run(run["id"])["findings"], [])

    def test_concurrent_connections_only_claim_one_new_pathway(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stores = [ResearchStore(root), ResearchStore(root)]
            runs = [run_fixture("first"), run_fixture("second")]
            for store, run in zip(stores, runs):
                store.save_run(run)
            barrier = threading.Barrier(2)

            def complete(index):
                barrier.wait(timeout=3)
                stores[index].complete_response(runs[index], [finding()], 0)

            with ThreadPoolExecutor(2) as pool:
                list(pool.map(complete, [0, 1]))
            self.assertEqual(sum(len(run["findings"]) for run in runs), 1)
            self.assertEqual(sum(run["duplicate_pathways"] for run in runs), 1)
            self.assertEqual(stores[0].pathway_count(), 1)

    def test_full_index_catches_a_duplicate_outside_the_prompt_excerpt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory))
            run = run_fixture("many")
            store.save_run(run)
            candidates = []
            for index in range(15):
                item = finding()
                item["steps"] = [{"product": TARGET, "reactants": ["CCO", "C" * (index + 2)]}]
                item["id"] = pathway_record(TARGET, item["steps"])["id"]
                item["structural_candidate"] = False
                candidates.append(item)
            store.complete_response(run, candidates, 0)
            context = store.pathway_context(TARGET)
            self.assertEqual(context["total"], 15)
            self.assertEqual(context["shown"], 12)
            self.assertLessEqual(len(json.dumps(context["pathways"])), 6000)
            shown = {item["id"] for item in context["pathways"]}
            omitted = next(item for item in candidates if item["id"] not in shown)
            repeated = run_fixture("outside-excerpt")
            store.save_run(repeated)
            store.complete_response(repeated, [omitted], 0)
            self.assertEqual(repeated["findings"], [])
            self.assertEqual(repeated["duplicate_pathways"], 1)
            self.assertNotEqual(context["pathways"], store.pathway_context(TARGET, 12)["pathways"])
