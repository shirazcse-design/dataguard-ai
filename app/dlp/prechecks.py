"""Deterministic DLP pre-checks: explicit, cheap, auditable conditions that need no AI.

* destination class from the catalogue (host + account type), and whether the user has used it;
* an existing sensitivity label carried by the event;
* explicit sensitive patterns, via UC4's OWN rules engine (`rules.engine.RulesEngine`) - reused,
  not re-implemented. Only category ids and strengths come back; masked excerpts are not kept.
"""

from __future__ import annotations

from app.classification.schemas import Document

from .config import DlpConfig
from .schemas import DLPEvent, Prechecks


def classify_destination(host: str, account_type: str, cfg: DlpConfig) -> str:
    if account_type == "corporate" and host in cfg.corporate_tenants:
        return "approved_corporate"
    for name, cls in cfg.destinations.items():
        if host in cls.hosts:
            return name
    return "unknown_external"


def run_prechecks(
    event: DLPEvent,
    document: Document,
    cfg: DlpConfig,
    rules_engine,
    known_destinations: list[str] | None,
) -> Prechecks:
    dest = classify_destination(event.destination.host, event.destination.account_type, cfg)
    rr = rules_engine.analyze(document)
    findings = [f"destination:{dest}"]
    if event.existing_label:
        findings.append(f"explicit_label:{event.existing_label}")
    for cat, strength in sorted(rr.categories.items()):
        findings.append(f"pattern:{cat}:{strength}")
    if rr.level:
        findings.append(f"pattern_level:{rr.level}")
    known = None if known_destinations is None else event.destination.host in known_destinations
    return Prechecks(
        destination_class=dest,
        destination_known_to_user=known,
        existing_label=event.existing_label,
        pattern_categories=dict(rr.categories),
        pattern_level=rr.level,
        findings=findings,
    )
