"""Local research history, durable checkpoints and local events."""

import copy
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager

from .pathways import pathway_record


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class ResearchStore:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "research.sqlite"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO metadata VALUES ('schema_version', '1');
                CREATE TABLE IF NOT EXISTS connections (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, run_id TEXT, created REAL NOT NULL,
                    type TEXT NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pathways (
                    id TEXT PRIMARY KEY, target TEXT NOT NULL, steps TEXT NOT NULL,
                    first_run_id TEXT NOT NULL, first_call INTEGER NOT NULL,
                    created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS pathways_target_created
                    ON pathways(target, created DESC, id);
            """)
            # One additive migration scans every saved run, not just the history's last 100.
            db.execute("BEGIN IMMEDIATE")
            version = db.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()[0]
            if version == "1":
                for identity, updated, payload in db.execute(
                    "SELECT id, updated, payload FROM runs ORDER BY updated, id"
                ):
                    run = json.loads(payload)
                    targets = run.get("config", {}).get("targets", [])
                    for finding in run.get("findings", []):
                        target = finding.get("target")
                        if target is None and len(targets) == 1:
                            target = targets[0]
                        record = pathway_record(target, finding.get("steps"))
                        if record:
                            self._insert_pathway(
                                db, record, identity, finding.get("call", 0), updated
                            )
                db.execute("UPDATE metadata SET value='2' WHERE key='schema_version'")
                version = "2"
            # Keep legacy tables as inert local records; never upload or delete prior research.
            db.execute("UPDATE metadata SET value='4' WHERE key='schema_version'")
        self.path.chmod(0o600)
        # A closed application never silently restarts a provider request.
        for run in self.runs():
            if run["state"] in {"running", "stopping", "waiting", "pausing"}:
                run.update(state="interrupted", detail="App closed. Review usage before resuming.")
                if run.get("inflight"):
                    run["usage_uncertain"] = True
                self.save_run(run, "run.interrupted")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                db.execute("PRAGMA foreign_keys=ON")
                yield db
        finally:
            db.close()

    def connections(self):
        with self.connect() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT config FROM connections")]

    def save_connection(self, item):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO connections VALUES (?, ?)", (item["id"], encode(item))
            )

    def delete_connection(self, identity):
        with self.connect() as db:
            db.execute("DELETE FROM connections WHERE id=?", (identity,))

    def event(self, kind, payload, run_id=None, db=None):
        body = encode(payload)
        identity = str(uuid.uuid4())

        def insert(connection):
            connection.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                (
                    identity,
                    run_id,
                    time.time(),
                    kind,
                    body,
                    hashlib.sha256(body.encode()).hexdigest(),
                ),
            )

        if db is None:
            with self.connect() as connection:
                insert(connection)
        else:
            insert(db)
        return identity

    @staticmethod
    def _insert_pathway(db, record, run_id, call, created):
        return (
            db.execute(
                "INSERT OR IGNORE INTO pathways VALUES (?, ?, ?, ?, ?, ?)",
                (record["id"], record["target"], encode(record["steps"]), run_id, call, created),
            ).rowcount
            == 1
        )

    def pathway_count(self, target=None):
        with self.connect() as db:
            if target is None:
                return db.execute("SELECT count(*) FROM pathways").fetchone()[0]
            return db.execute("SELECT count(*) FROM pathways WHERE target=?", (target,)).fetchone()[
                0
            ]

    def pathway_context(self, target, offset=0):
        """A bounded, rotating prompt excerpt; every result is checked against the full index."""
        with self.connect() as db:
            count = db.execute(
                "SELECT count(*) FROM pathways WHERE target=?", (target,)
            ).fetchone()[0]
            rows = db.execute(
                "SELECT id, steps FROM pathways WHERE target=? "
                "ORDER BY created DESC, id LIMIT 12 OFFSET ?",
                (target, offset % max(1, count)),
            )
            excerpt, size = [], 0
            for identity, steps in rows:
                record = {"id": identity, "steps": json.loads(steps)}
                length = len(encode(record))
                if size + length > 6000:
                    continue
                excerpt.append(record)
                size += length
        return {"total": count, "shown": len(excerpt), "pathways": excerpt}

    def complete_response(self, run, candidates, target_index, event="model.completed"):
        """Claim new graphs and checkpoint the response and local event in one transaction."""
        original = run
        run = copy.deepcopy(run)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = {finding["id"] for finding in run["findings"]}
            duplicates = 0
            for finding in candidates:
                record = pathway_record(finding["target"], finding["steps"])
                if record:
                    if not self._insert_pathway(db, record, run["id"], run["calls"], time.time()):
                        duplicates += 1
                        continue
                    self.event(
                        "pathway.discovered",
                        {"schema_version": 1, "run_id": run["id"], "finding": finding},
                        run["id"],
                        db=db,
                    )
                if finding["id"] in existing:
                    continue
                run["findings"].append(finding)
                existing.add(finding["id"])
                if finding["structural_candidate"]:
                    run["last_new_seconds"][target_index] = run["target_seconds"][target_index]
                    run["last_new_tokens"][target_index] = run["target_tokens"][target_index]
            run["duplicate_pathways"] = run.get("duplicate_pathways", 0) + duplicates
            run["responses"][-1]["duplicates_skipped"] = duplicates
            self._save_run(db, run, event)
        original.update(run)

    def save_run(self, run, event=None):
        with self.connect() as db:
            self._save_run(db, run, event)

    def _save_run(self, db, run, event=None):
        run["updated"] = time.time()
        db.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?)",
            (
                run["id"],
                run["state"],
                run["updated"],
                encode(run),
            ),
        )
        if event:
            payload = {
                "schema_version": 1,
                "run_id": run["id"],
                "state": run["state"],
                "detail": run.get("detail"),
                "deadline": run.get("deadline"),
                "calls": run.get("calls", 0),
                "duplicate_pathways": run.get("duplicate_pathways", 0),
                "usage_uncertain": run.get("usage_uncertain", False),
                "usage": {
                    key: run.get(key)
                    for key in (
                        "input_tokens",
                        "output_tokens",
                        "cached_tokens",
                        "reasoning_tokens",
                        "spend_estimate",
                    )
                },
            }
            if event == "run.started":
                payload.update(config=run.get("config"))
            elif event in {"model.completed", "model.partial"}:
                payload["response"] = run["responses"][-1]
                payload["findings"] = [x for x in run["findings"] if x.get("call") == run["calls"]]
            self.event(event, payload, run["id"], db=db)

    def runs(self):
        with self.connect() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM runs ORDER BY updated DESC LIMIT 100")
            ]

    def run(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM runs WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("Run not found.")
        return json.loads(row[0])

    def set_metadata(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, value))

    def get_metadata(self, key):
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
