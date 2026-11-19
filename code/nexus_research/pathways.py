"""Canonical identities for connected pathway graphs, independent of model wording."""

import hashlib
import json

from .chemistry import canonical


def pathway_record(target, steps):
    """Return an exact graph identity, or None for notes, detached steps or cycles.

    Conditions, titles, evidence and reaction-rule coverage do not change identity.
    Reactant multiplicity and specified stereochemistry do change identity.
    """
    try:
        target = canonical(target)
        if not isinstance(steps, list) or not 1 <= len(steps) <= 6:
            return None
        normalized = []
        for step in steps:
            reactants = step["reactants"]
            if not isinstance(reactants, list) or not 1 <= len(reactants) <= 16:
                return None
            normalized.append(
                {
                    "product": canonical(step["product"]),
                    "reactants": sorted(canonical(x) for x in reactants),
                }
            )
        graph = {step["product"]: step["reactants"] for step in normalized}
        if len(graph) != len(normalized) or target not in graph:
            return None
        visited = set()

        def visit(node, ancestors):
            if node in ancestors:
                return False
            if node in visited:
                return True
            visited.add(node)
            return all(visit(child, ancestors | {node}) for child in graph.get(node, []))

        if not visit(target, set()) or not set(graph).issubset(visited):
            return None
        identity = sorted((step["product"], tuple(step["reactants"])) for step in normalized)
        digest = hashlib.sha256(
            json.dumps({"target": target, "route": identity}, sort_keys=True).encode()
        ).hexdigest()
        return {
            "id": digest,
            "target": target,
            "steps": sorted(normalized, key=lambda step: (step["product"], step["reactants"])),
        }
    except (ValueError, KeyError, TypeError, RuntimeError):
        return None
