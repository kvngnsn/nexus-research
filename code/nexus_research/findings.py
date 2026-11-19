"""Human-readable findings on the user's disk; no remote storage or reporting."""

import copy
import json
import os
import re
import tempfile
from pathlib import Path


class OutputError(ValueError):
    pass


def directory(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise OutputError("Choose a findings folder.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise OutputError("Use an absolute folder path.")
    return path.resolve()


def check_writable(path):
    """Do not recreate a missing drive or silently fall back to another destination."""
    try:
        if not path.is_dir():
            raise OSError("Folder unavailable")
        with tempfile.TemporaryFile(dir=path) as file:
            file.write(b"Nexus write check")
            file.flush()
            os.fsync(file.fileno())
    except OSError as exc:
        raise OutputError(
            "Findings folder is unavailable or not writable. Choose an existing folder."
        ) from exc


def export_run(run):
    result = copy.deepcopy(run)
    for field in (
        "opening",
        "database_device_id",
        "output_directory",
        "output_error",
        "rate_window",
    ):
        result.pop(field, None)
    connection = result.get("config", {}).get("connection", {})
    for field in ("id", "name", "has_key", "endpoint"):
        connection.pop(field, None)
    result["export_version"] = 2
    result["commercial_readiness"] = False
    return result


def markdown(run):
    lines = [
        "# Nexus Research",
        "",
        f"Run: {run['id']}",
        f"State: {run['state']}",
        str(run.get("detail", "")),
        "",
        "Research hypotheses only. Sources, synthesis feasibility and costs require independent validation.",
        "",
    ]
    for finding in run.get("findings", []):
        lines += [
            "## " + finding.get("title", "Finding"),
            "",
            finding.get("summary", ""),
            "",
            f"Status: {finding.get('evidence', 'Unverified proposal')}. Cost unknown.",
            "",
        ]
        lines += [
            f"- {' + '.join(step['reactants'])} → {step['product']}"
            for step in finding.get("steps", [])
        ]
        lines += ["", *finding.get("sources", []), ""]
    lines += ["## Visible model responses", ""]
    lines += [response.get("text", "") + "\n" for response in run.get("responses", [])]
    return "\n".join(lines)


def write_findings(run):
    path = directory(run["output_directory"])
    identity = run["id"]
    if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", identity):
        raise OutputError("Invalid run identifier.")
    result = export_run(run)
    documents = {
        f"nexus-{identity}.json": json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
        + "\n",
        f"nexus-{identity}.md": markdown(result),
    }
    # Each replacement is atomic. JSON is the authoritative snapshot; SQLite retains recovery data.
    for name, text in documents.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path, delete=False
            ) as file:
                temporary = Path(file.name)
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path / name)
        except OSError as exc:
            raise OutputError(
                "Could not save findings to the chosen folder. Research stopped; recovery data remains in local history."
            ) from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
