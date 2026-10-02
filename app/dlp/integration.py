"""UC1's use of the shared capabilities - no classification or retrieval logic lives here.

* UC4: `classify_document` through UC4's own MCP adapter (`mcp_adapter.adapter`) as caller
  `dlp-investigation-agent`, so UC4's per-caller tier/cost/latency caps apply unchanged.
* UC6: `PolicyCopilot.answer(question, "advanced")`, with the question built ONLY from
  deterministic facts (mapped level term, category phrase, destination phrase). A section's
  effect on the event comes from the versioned mapping (`config/dlp/mapping.v1.yaml`) and only for
  sections UC6 returned as VERIFIED citations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.classification.schemas import Document

from .config import DlpConfig, MappingConfig
from .schemas import Classification, PolicyContext

REPO = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO / "data" / "dlp" / "documents"
EFFECT_ORDER = {"allowed": 1, "requires_approval": 2, "prohibited": 3}


class DocumentError(Exception):
    pass


class DocumentStore:
    """`uc1:<name>` -> data/dlp/documents/<name>.json; `uc4:<doc_id>` -> a UC4 dev-split document
    (its classification is recorded, so it replays)."""

    def __init__(self) -> None:
        self._uc4: dict[str, Any] | None = None

    def get(self, ref: str) -> Document:
        kind, key = ref.split(":", 1)
        if kind == "uc1":
            path = DOCS_DIR / f"{key}.json"
            if not path.exists():
                raise DocumentError("unknown_document")
            d = json.loads(path.read_text(encoding="utf-8"))
            return _doc(d["content"], d["filename"])
        if self._uc4 is None:
            from evals.classification.dataset.build import load_documents

            docs = load_documents(REPO / "data" / "synthetic" / "uc4", splits=["dev"])
            self._uc4 = {x.doc_id: x for x in docs}
        d = self._uc4.get(key)
        if d is None:
            raise DocumentError("unknown_document")
        return _doc(d.content, d.filename)


def _doc(content: str, filename: str) -> Document:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "txt"
    return Document(content=content, filename=filename, extension=ext)


def summarize_classification(r: dict[str, Any]) -> Classification:
    level = r.get("level") or {}
    conf = level.get("confidence") or {}
    review = r.get("review") or {}
    return Classification(
        ok=r.get("status") in ("ok", "degraded"),
        status=r.get("status", "error"),
        level=level.get("value"),
        confidence=conf.get("raw") if isinstance(conf, dict) else None,
        decided_by=level.get("decided_by"),
        categories=[c["id"] for c in r.get("categories", [])],
        high_risk=bool((r.get("high_risk") or {}).get("value")),
        review_required=bool(review.get("required")) or r.get("status") == "review_required",
        review_reasons=list(review.get("reason_codes") or []),
        injection_flagged=any(
            g.get("type") == "prompt_injection_suspected" for g in r.get("guardrail_events", [])
        ),
        stages_run=list((r.get("routing") or {}).get("stages_run") or []),
    )


class Uc4Classifier:
    CALLER = "dlp-investigation-agent"

    def __init__(self, adapter) -> None:
        self.adapter = adapter

    def classify(
        self, document: Document, request_id: str, *, semantic_unavailable: bool
    ) -> Classification:
        args: dict[str, Any] = {
            "request_id": request_id,
            "document": {"content": document.content, "filename": document.filename},
        }
        if semantic_unavailable:
            # SIMULATED fault: the model tier is unavailable (outage / cost cap). UC4's real
            # behaviour then is rules-only, which abstains on semantic documents.
            args["options"] = {"max_llm_tier": "none"}
        return summarize_classification(self.adapter.classify_document(args))


def effective_level(c: Classification, label: str | None, mapping: MappingConfig) -> str | None:
    """The level the policy question is about: the highest of UC4's level, an explicit label, and
    the minimum level of each UC4 category (classification standard). Deterministic."""
    candidates = [c.level, label if label in mapping.levels else None]
    candidates += [mapping.categories[x].min_level for x in c.categories if x in mapping.categories]
    known = [x for x in candidates if x]
    return max(known, key=mapping.rank) if known else None


def policy_question(
    level: str, categories: list[str], dest: str, cfg: DlpConfig, mapping: MappingConfig
) -> str:
    term = mapping.levels[level].policy_term
    phrase = next(
        (mapping.categories[c].phrase for c in categories if c in mapping.categories),
        f"{term.lower()} business documents",
    )
    d = mapping.destination_phrases[dest]
    return cfg.policy_question.format(
        level_term=term, category_phrase=phrase, action_phrase=d.action, destination_phrase=d.phrase
    )


def apply_effects(
    cited_sections: list[str], dest: str, level: str, mapping: MappingConfig
) -> list[dict[str, str]]:
    rank = mapping.rank(level)
    out = []
    for e in mapping.policy_effects:
        if e.section not in cited_sections or dest not in e.destinations:
            continue
        if rank < mapping.rank(e.min_level) or (e.max_level and rank > mapping.rank(e.max_level)):
            continue
        out.append({"section": e.section, "effect": e.effect})
    return out


class PolicyIntelligence:
    def __init__(self, copilot) -> None:
        self.copilot = copilot

    def assess(self, question: str, dest: str, level: str, mapping: MappingConfig) -> PolicyContext:
        a = self.copilot.answer(question, "advanced")
        corpus = self.copilot.corpus
        cited = [c for c in a.claims if c.verified and c.chunk_id]
        sections = list(dict.fromkeys(corpus.by_id[c.chunk_id].section_key for c in cited))
        effects = apply_effects(sections, dest, level, mapping)
        kinds = {e["effect"] for e in effects}
        conflict, reason = False, None
        if a.status == "CONFLICT_REVIEW":
            conflict, reason = True, "uc6_conflict_review"
        elif "prohibited" in kinds and "allowed" in kinds:
            conflict, reason = True, "verified_effects_disagree"
        effect = "unknown"
        if kinds and not conflict:
            effect = max(kinds, key=EFFECT_ORDER.get)
        return PolicyContext(
            question=question,
            status=a.status,
            mode=a.mode,
            citations=list(a.citations),
            cited_sections=sections,
            claims=[{"text": c.text, "citation": c.citation} for c in cited],
            effects=effects,
            effect=effect,
            conflict=conflict,
            conflict_reason=reason,
            review_reasons=list(a.review.reasons),
        )
