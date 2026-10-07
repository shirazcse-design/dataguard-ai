"""Which POL-ACC requirements apply to a request (config/access/requirements.v1.yaml). The policy
TEXT comes from UC6 at run time; this module only selects sections and their limits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .graph import AccessGraph

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config" / "access" / "requirements.v1.yaml"


def load_requirements(path: Path = CONFIG) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def applicable(
    graph: AccessGraph, eid: str, sensitivity: str | None, cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The requirements for this entitlement: always + its resource class + UC4's sensitivity.
    `mapped` is False when the resource class has no policy mapping (POLICY_EVIDENCE_MISSING)."""
    cfg = cfg or load_requirements()
    rclass = graph.resource(graph.resource_of(eid))["resource_class"]
    by_class = cfg["by_resource_class"].get(rclass)
    reqs: dict[str, dict[str, Any]] = {}
    for r in [*cfg["always"], *(by_class or []), *cfg["by_sensitivity"].get(sensitivity or "", [])]:
        reqs.setdefault(r["req_id"], r)
    limits = [r["max_hours"] for r in reqs.values() if "max_hours" in r]
    return {"requirements_version": cfg["requirements_version"], "policy_id": cfg["policy_id"],
            "resource_class": rclass, "mapped": by_class is not None, "requirements": list(reqs.values()),
            "max_hours": min(limits) if limits else None}  # fmt: skip
