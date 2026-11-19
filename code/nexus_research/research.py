"""Bounded local research supervisor with durable checkpoints and honest usage."""

import hashlib
import json
import math
import re
import threading
import time
import uuid

from .chemistry import apply_rule, canonical
from .credentials import Keychain
from .findings import OutputError, check_writable, directory, export_run, write_findings
from .pathways import pathway_record
from .providers import (
    KINDS,
    ProviderError,
    api_call,
    availability,
    cli_call,
    input_count,
    local_endpoint,
)
from .reaction_checks import RULES
from .research_store import ResearchStore


def number(data, key, default, low, high, integer=False):
    value = data.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"Invalid {key}.")
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Enter a number for {key}.") from None
    if not math.isfinite(value) or not low <= value <= high or (integer and value != int(value)):
        raise ValueError(f"{key} must be between {low} and {high}.")
    return int(value) if integer else value


def scrub(text, key=None):
    if key:
        text = text.replace(key, "[credential removed]")
    return re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]{12,})", "[credential removed]", text
    )


def normalize_findings(text, target, rules):
    """Only paired configured reaction checks qualify a new candidate for progress."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        items = json.loads(raw).get("findings", [])
    except (ValueError, AttributeError):
        return []
    if not isinstance(items, list):
        return []
    results = []
    for item in items[:30]:
        if not isinstance(item, dict):
            continue
        steps, checked = [], False
        try:
            for step in item.get("steps", [])[:6]:
                product = canonical(step["product"])
                reactants = sorted(canonical(x) for x in step["reactants"])
                valid = False
                for rule in rules:
                    if not rule.get("enabled", True):
                        continue
                    parts, _ = apply_rule(product, rule["retro"], rule["forward"])
                    if tuple(reactants) in [tuple(sorted(x)) for x in parts]:
                        valid = True
                        break
                steps.append({"product": product, "reactants": reactants, "rule_checked": valid})
            checked = bool(pathway_record(target, steps)) and all(
                step["rule_checked"] for step in steps
            )
        except (ValueError, KeyError, TypeError, RuntimeError):
            steps, checked = [], False
        identity = sorted((step["product"], tuple(step["reactants"])) for step in steps)
        digest = hashlib.sha256(
            json.dumps(
                {"target": target, "route": identity if identity else item}, sort_keys=True
            ).encode()
        ).hexdigest()
        sources = [
            str(x)[:2000]
            for x in item.get("sources", [])[:10]
            if isinstance(x, str) and x.startswith(("https://", "http://"))
        ]
        results.append(
            {
                "id": digest,
                "title": str(item.get("title", "Research note"))[:200],
                "summary": str(item.get("summary", ""))[:6000],
                "steps": steps,
                "sources": sources,
                "structural_candidate": checked,
                "evidence": "Configured graph checks only"
                if checked
                else "Unverified model proposal",
                "estimated_cost": None,
            }
        )
    return results


class Research:
    def __init__(self, root, vault=None, default_output=None):
        self.store = ResearchStore(root)
        saved_output = self.store.get_metadata("output_directory")
        self.output_directory = directory(saved_output or default_output or root / "findings")
        if not saved_output:
            self.output_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.vault = vault if vault is not None else Keychain()
        self.lock = threading.RLock()
        self.active = None
        self.cancel = threading.Event()
        self.action = "stop"
        self.worker = None
        with self.store.connect() as db:
            initialized = db.execute(
                "SELECT value FROM metadata WHERE key='defaults_initialized'"
            ).fetchone()
        if not initialized and not self.store.connections():
            for kind, label in [("codex", "Codex account"), ("claude", "Claude Code account")]:
                self.store.save_connection(
                    {
                        "id": str(uuid.uuid4()),
                        "name": label,
                        "kind": kind,
                        "model": "default",
                        "has_key": False,
                    }
                )
        with self.store.connect() as db:
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('defaults_initialized', '1')")

    def set_output_directory(self, path):
        with self.lock:
            if self.active:
                raise ValueError("Stop the active run before changing the findings folder.")
            destination = directory(path)
            check_writable(destination)
            self.store.set_metadata("output_directory", str(destination))
            self.output_directory = destination
            return {"directory": str(destination)}

    def save_findings(self, run):
        write_findings(run)
        run.pop("output_error", None)

    def connections(self):
        return [dict(x, status=availability(x)) for x in self.store.connections()]

    def save_connection(self, data):
        kind = data.get("kind")
        if kind not in KINDS:
            raise ValueError("Choose a supported connection type.")
        name, model = str(data.get("name", "")).strip(), str(data.get("model", "")).strip()
        if not 1 <= len(name) <= 80 or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", model):
            raise ValueError("Enter a connection name and an exact model ID (or default for CLI).")
        if model == "default" and kind not in {"codex", "claude"}:
            raise ValueError("API connections require an explicit model ID.")
        identity = str(uuid.uuid4())
        connection = {"id": identity, "kind": kind, "name": name, "model": model, "has_key": False}
        if kind == "local":
            connection["endpoint"] = local_endpoint(str(data.get("endpoint", "")))
        if kind in {"openai", "anthropic"}:
            self.vault.set(identity, data.get("key", ""))
            connection["has_key"] = True
        self.store.save_connection(connection)
        return dict(connection, status=availability(connection))

    def delete_connection(self, identity):
        with self.lock:
            if self.active:
                raise ValueError("Stop the active run before removing a connection.")
            connection = self.connection(identity)
            if connection.get("has_key"):
                self.vault.delete(identity)
            self.store.delete_connection(identity)

    def connection(self, identity):
        for item in self.store.connections():
            if item["id"] == identity:
                return item
        raise ValueError("Choose an existing connection.")

    def config(self, data):
        values = data.get("targets")
        if not isinstance(values, list) or not 1 <= len(values) <= 12:
            raise ValueError("Add between one and twelve target structures.")
        targets = list(dict.fromkeys(canonical(x) for x in values))
        connection = self.connection(data.get("connection"))
        if availability(connection) in {"Not installed", "Needs API key"}:
            raise ValueError("Set up the selected connection first.")
        mode = data.get("mode", "single")
        if mode not in {"single", "timed", "finite"}:
            raise ValueError("Choose a supported search mode.")
        config = {
            "targets": targets,
            "connection": connection,
            "mode": mode,
        }
        for key, default, low, high, integer in [
            ("runtime_seconds", 900, 1, 86400, False),
            ("total_tokens", 20000, 128, 10000000, True),
            ("tokens_per_minute", 10000, 128, 1000000, True),
            ("output_tokens", 2000, 64, 32768, True),
            ("max_calls", 1, 1, 200, True),
            ("idle_seconds", 300, 1, 86400, False),
            ("idle_tokens", 5000, 128, 10000000, True),
            ("usd_limit", 0, 0, 10000, False),
            ("input_rate", 0, 0, 1000, False),
            ("output_rate", 0, 0, 1000, False),
        ]:
            config[key] = number(data, key, default, low, high, integer)
        if config["usd_limit"] and connection["kind"] in {"codex", "claude", "local"}:
            raise ValueError(
                "A dollar cap is unavailable for CLI or local connections. Use token and time stops."
            )
        if config["usd_limit"] and (not config["input_rate"] or not config["output_rate"]):
            raise ValueError(
                "Enter current input/output USD per million tokens to reserve estimated spending."
            )
        return config

    def start(self, data):
        config = self.config(data)
        check_writable(self.output_directory)
        now = time.time()
        run = {
            "id": str(uuid.uuid4()),
            "config": config,
            "output_directory": str(self.output_directory),
            "state": "running",
            "detail": "Starting",
            "created": now,
            "deadline": now + config["runtime_seconds"],
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "calls": 0,
            "duplicate_pathways": 0,
            "spend_estimate": 0 if config["input_rate"] and config["output_rate"] else None,
            "productive_seconds": 0,
            "last_new_seconds": [0] * len(config["targets"]),
            "target_seconds": [0] * len(config["targets"]),
            "last_new_tokens": [0] * len(config["targets"]),
            "target_tokens": [0] * len(config["targets"]),
            "findings": [],
            "responses": [],
            "rate_window": [],
            "inflight": False,
            "usage_uncertain": False,
        }
        self._launch(run, "run.started")
        return self.store.run(run["id"])

    def _launch(self, run, event):
        with self.lock:
            if self.active:
                raise ValueError("One run can be active at a time. Pause or stop it first.")
            self.active = run["id"]
            self.action = "stop"
            self.cancel = threading.Event()
            try:
                self.store.save_run(run, event)
                self.save_findings(run)
            except Exception as exc:
                self.active = None
                run.update(state="output_unavailable", detail=str(exc))
                self.store.save_run(run, "run.output_unavailable")
                raise
            self.worker = threading.Thread(target=self._work, args=(run,), daemon=True)
            self.worker.start()

    def control(self, identity, action):
        with self.lock:
            run = self.store.run(identity)
            if action == "resume":
                if run["state"] not in {
                    "paused",
                    "interrupted",
                    "output_unavailable",
                    "database_disconnected",
                }:
                    raise ValueError(
                        "Only paused, interrupted or storage-stopped runs can be resumed."
                    )
                if run.get("usage_uncertain"):
                    raise ValueError(
                        "Unreported in-flight usage prevents a safe budget resume. Export and review this run."
                    )
                if time.time() >= run["deadline"]:
                    raise ValueError(
                        "The original deadline has passed; this run cannot be resumed."
                    )
                self.connection(run["config"]["connection"]["id"])
                run.setdefault("output_directory", str(self.output_directory))
                check_writable(directory(run["output_directory"]))
                run.update(state="running", detail="Resuming within the original budget")
                self._launch(run, "run.resumed")
            elif action in {"pause", "stop"} and self.active == identity:
                run.update(
                    state="pausing" if action == "pause" else "stopping",
                    detail="Waiting for any in-flight call; provider charges may continue.",
                )
                self.store.save_run(run)
                self.action = action
                self.cancel.set()
            else:
                raise ValueError("This run is not active.")
            return run

    def close(self):
        if self.active:
            self.action = "pause"
            self.cancel.set()

    def prompt(self, run, target):
        prior = [x["title"] for x in run["findings"]][-20:]
        known = self.store.pathway_context(target, offset=run["calls"] * 12)
        return (
            "You are a preliminary chemistry research assistant. Use no tools or local files. "
            "Do not supply harmful or unlawful synthesis assistance. Respect provider safety policies. "
            "Treat all routes, yields and prices as unverified hypotheses. Never fabricate sources or quotes. "
            "Distinguish a literature claim, an assumption and an observed supplier price. "
            "Investigate distinct candidate synthesis pathways, resource requirements and cost uncertainties. "
            "Separate evidence from assumptions and identify missing information. "
            "Return JSON with findings: [{title, summary, sources: [URL], "
            "steps: [{reactants: [SMILES], product: SMILES}]}]. Use 1–6 connected steps per pathway "
            "and an empty steps array for analysis notes. "
            "A molecule string is not evidence that a reaction works. Include uncertainties in summary.\n"
            f"Target SMILES: {target}\n"
            "Avoid pathways already discovered on this computer. Rewording, reordering steps or reactants, "
            "or equivalent SMILES do not make a different pathway. Investigate different precursor/step graphs. "
            "The excerpt below is bounded; every result is checked against the complete local index. "
            "Return an empty findings list if you cannot support another distinct proposal.\n"
            f"Known local pathways for this target: {json.dumps(known)}\n"
            f"Previously recorded titles in this run (do not repeat): {json.dumps(prior)}"
        )

    def _work(self, run):
        config = run["config"]
        connection = config["connection"]
        key = None
        counts = {}
        try:
            if connection.get("has_key"):
                key = self.vault.get(connection["id"])
            workdir = self.store.root / "work" / run["id"]
            workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
            while not self.cancel.is_set():
                now = time.time()
                if now >= run["deadline"]:
                    run.update(state="limited", detail="Original runtime deadline reached")
                    break
                if config["mode"] == "single" and run["calls"] >= len(config["targets"]):
                    run.update(state="completed", detail="One analysis per target completed")
                    break
                if run["calls"] >= config["max_calls"]:
                    run.update(
                        state="completed",
                        detail="Configured call limit reached; search is not exhaustive",
                    )
                    break
                if config["mode"] == "finite":
                    eligible = [
                        i
                        for i in range(len(config["targets"]))
                        if run["target_seconds"][i] - run["last_new_seconds"][i]
                        < config["idle_seconds"]
                        and run["target_tokens"][i] - run["last_new_tokens"][i]
                        < config["idle_tokens"]
                    ]
                    if not eligible:
                        run.update(
                            state="no_progress",
                            detail="No-progress threshold reached; not exhaustive coverage",
                        )
                        break
                else:
                    eligible = list(range(len(config["targets"])))
                index = eligible[run["calls"] % len(eligible)]
                target = config["targets"][index]
                check_writable(directory(run["output_directory"]))
                prompt = self.prompt(run, target)
                remaining = config["total_tokens"] - run["input_tokens"] - run["output_tokens"]
                if remaining < 64:
                    run.update(state="limited", detail="Reported token budget reached")
                    break
                output = min(config["output_tokens"], remaining)
                timeout = min(120, max(1, run["deadline"] - time.time()))
                if connection["kind"] not in {"codex", "claude"}:
                    if prompt not in counts:
                        counts[prompt] = input_count(connection, prompt, key, timeout, self.cancel)
                    counted, basis = counts[prompt]
                    output = min(output, remaining - counted, config["tokens_per_minute"] - counted)
                    if output < 64:
                        run.update(
                            state="limited",
                            detail="Remaining token budget cannot reserve another call",
                        )
                        break
                    reservation = counted + output
                    estimated = (
                        counted * config["input_rate"] + output * config["output_rate"]
                    ) / 1000000
                    if (
                        config["usd_limit"]
                        and (run["spend_estimate"] or 0) + estimated > config["usd_limit"]
                    ):
                        run.update(
                            state="limited",
                            detail="Estimated dollar budget cannot reserve another call",
                        )
                        break
                else:
                    reservation, basis = 0, "reported_after_call"
                if self.cancel.is_set():
                    break
                timeout = min(120, run["deadline"] - time.time())
                if timeout <= 0:
                    run.update(
                        state="limited",
                        detail="Original runtime deadline reached before generation",
                    )
                    break
                run["rate_window"] = [x for x in run["rate_window"] if now - x[0] < 60]
                if (
                    sum(x[1] for x in run["rate_window"]) + reservation
                    > config["tokens_per_minute"]
                ):
                    run.update(
                        state="waiting", detail="Waiting for the rolling token-per-minute window"
                    )
                    self.store.save_run(run)
                    self.cancel.wait(0.5)
                    continue
                run.update(
                    state="running",
                    detail=f"Researching target {index + 1} of {len(config['targets'])}",
                    inflight=False,
                    reservation=reservation,
                    reservation_basis=basis,
                )
                self.store.save_run(run)
                self.store.event(
                    "model.requested",
                    {
                        "prompt": prompt,
                        "connection": connection,
                        "output_limit": output,
                        "reservation": reservation,
                    },
                    run["id"],
                )
                if self.cancel.is_set():
                    break
                run["inflight"] = True
                self.store.save_run(run)
                started = time.monotonic()
                if connection["kind"] in {"codex", "claude"}:
                    response = cli_call(connection, prompt, self.cancel, timeout, workdir)
                else:
                    response = api_call(connection, prompt, output, key, timeout, self.cancel)
                productive = time.monotonic() - started
                run["productive_seconds"] += productive
                run["target_seconds"][index] += productive
                response["text"] = scrub(response["text"], key)
                used = response["usage"]
                run["calls"] += 1
                run["inflight"] = False
                if any(
                    not isinstance(used.get(k), int) or used[k] < 0 for k in ("input", "output")
                ):
                    run["usage_uncertain"] = True
                else:
                    for field, source in [
                        ("input_tokens", "input"),
                        ("output_tokens", "output"),
                        ("cached_tokens", "cached"),
                        ("reasoning_tokens", "reasoning"),
                    ]:
                        run[field] += used.get(source) or 0
                    total = used["input"] + used["output"]
                    run["target_tokens"][index] += total
                    run["rate_window"].append([time.time(), total])
                    if run["spend_estimate"] is not None:
                        run["spend_estimate"] += (
                            used["input"] * config["input_rate"]
                            + used["output"] * config["output_rate"]
                        ) / 1000000
                run["responses"].append(dict(response, target=target, created=time.time()))
                new = normalize_findings(response["text"], target, RULES)
                for finding in new:
                    finding["target"] = target
                    finding["call"] = run["calls"]
                self.store.complete_response(run, new, index)
                self.save_findings(run)
                if run["usage_uncertain"]:
                    run.update(
                        state="failed",
                        detail="Provider omitted usage; new calls stopped to protect the budget",
                    )
                    break
                if config["mode"] == "single" and run["calls"] >= len(config["targets"]):
                    run.update(state="completed", detail="One analysis per target completed")
                    break
                # CLI counts arrive after completion; enforce subsequent calls, never claim a hard in-flight cap.
                if sum(x[1] for x in run["rate_window"]) >= config["tokens_per_minute"]:
                    run.update(
                        state="waiting", detail="Reported usage reached the rolling minute limit"
                    )
                    self.store.save_run(run)
                    while not self.cancel.wait(0.5) and time.time() < run["deadline"]:
                        if time.time() - run["rate_window"][0][0] >= 60:
                            break
            if self.cancel.is_set():
                run.update(
                    state="paused" if self.action == "pause" else "stopped",
                    detail="Partial research saved locally",
                )
        except Exception as exc:
            if run.get("inflight"):
                run["usage_uncertain"] = True
            if isinstance(exc, ProviderError) and exc.partial and exc.partial.get("text"):
                response = dict(
                    exc.partial,
                    text=scrub(exc.partial["text"], key),
                    partial=True,
                    target=target,
                    created=time.time(),
                )
                run["calls"] += 1
                run["responses"].append(response)
                findings = normalize_findings(response["text"], target, RULES)
                for finding in findings:
                    finding.update(target=target, call=run["calls"])
                self.store.complete_response(run, findings, index, event="model.partial")
            state = (
                "output_unavailable"
                if isinstance(exc, OutputError)
                else "paused"
                if self.cancel.is_set() and self.action == "pause"
                else "stopped"
                if self.cancel.is_set()
                else "failed"
            )
            run.update(
                state=state,
                detail=scrub(str(exc), key)
                if isinstance(exc, (ValueError, ProviderError))
                else "Research failed. Partial records are saved.",
            )
        finally:
            with self.lock:
                if self.cancel.is_set() and run["state"] not in {"failed", "paused", "stopped"}:
                    run.update(
                        state="paused" if self.action == "pause" else "stopped",
                        detail="Partial research saved locally",
                    )
                run["inflight"] = False
                self.store.save_run(run, "run.finished")
                try:
                    self.save_findings(run)
                except OutputError as exc:
                    run.update(state="output_unavailable", detail=str(exc), output_error=str(exc))
                    self.store.save_run(run, "run.output_unavailable")
                self.active = None

    def snapshot(self):
        runs = [
            {k: v for k, v in x.items() if k not in {"responses", "findings", "rate_window"}}
            | {"finding_count": len(x["findings"])}
            for x in self.store.runs()
        ]
        return {
            "connections": self.connections(),
            "runs": runs,
            "output": {"directory": str(self.output_directory), "formats": ["json", "md"]},
            "pathways": {"count": self.store.pathway_count(), "scope": "local_only"},
            "storage": str(self.store.root),
            "active": self.active,
        }

    def export(self, identity):
        return export_run(self.store.run(identity))
