"""Second-opinion guardrail audit (Responsible AI: Robustness & Safety pillar).

Compares the custom, tested-in-CI guardrails against Azure AI Content Safety on real
classification output: Prompt Shields as a second opinion on the S0 injection scan, and
Groundedness Detection as a second opinion on the exact-substring evidence verifier. This is an
audit tool, not a runtime guardrail (see decisions.md): it never changes a classification result.

Runs entirely offline except the Content Safety calls themselves, which are SKIPPED, never
silently treated as agreement, when no credentials are configured - `client=None` throughout means
"not run", and every report says exactly how many rows were actually scored by Azure.

Privacy: document text and evidence excerpts are used only to make the second-opinion call itself;
no row in any report carries raw text, only booleans, ids and error kinds.
"""

from __future__ import annotations

from typing import Any

from app.classification.schemas import ClassificationResult
from guardrails.azure_content_safety import ContentSafetyClient, ContentSafetyError


def _custom_injection_flag(result: ClassificationResult) -> bool:
    return any(e.type == "prompt_injection_suspected" for e in result.guardrail_events)


def audit_injection(
    documents_and_results: list[tuple[str, ClassificationResult]],
    client: ContentSafetyClient | None,
) -> dict[str, Any]:
    """`documents_and_results` pairs each document's raw text (used only for the Azure call, never
    persisted) with its already-computed `ClassificationResult`."""
    rows = []
    for content, result in documents_and_results:
        azure: bool | None = None
        error: str | None = None
        if client is not None:
            try:
                azure = client.shield_prompt(documents=[content])["attack_detected"]
            except ContentSafetyError as exc:
                error = exc.kind
        rows.append(
            {
                "request_id": result.request_id,
                "custom_flagged": _custom_injection_flag(result),
                "azure_flagged": azure,
                "azure_error": error,
            }
        )
    scored = [r for r in rows if r["azure_flagged"] is not None]
    agree = sum(1 for r in scored if r["custom_flagged"] == r["azure_flagged"])
    return {
        "n_documents": len(rows),
        "n_scored_by_azure": len(scored),
        "agreement_rate": agree / len(scored) if scored else None,
        "custom_only_flagged": sum(
            1 for r in scored if r["custom_flagged"] and not r["azure_flagged"]
        ),
        "azure_only_flagged": sum(
            1 for r in scored if r["azure_flagged"] and not r["custom_flagged"]
        ),
        "rows": rows,
    }


def audit_groundedness(
    claims: list[tuple[str, str, bool]],  # (excerpt, source_text, custom_verified)
    client: ContentSafetyClient | None,
) -> dict[str, Any]:
    """`claims` pairs one LLM evidence excerpt (already masked/truncated by the producer) and the
    document text it should be grounded in with whether the custom exact-substring check verified
    it. Excerpt and source text are used only for the Azure call, never persisted."""
    rows = []
    for excerpt, source, custom_verified in claims:
        azure_grounded: bool | None = None
        error: str | None = None
        if client is not None:
            try:
                r = client.detect_groundedness(text=excerpt, grounding_sources=[source])
                azure_grounded = not r["ungrounded_detected"]
            except ContentSafetyError as exc:
                error = exc.kind
        rows.append(
            {
                "custom_verified": custom_verified,
                "azure_grounded": azure_grounded,
                "azure_error": error,
            }
        )
    scored = [r for r in rows if r["azure_grounded"] is not None]
    agree = sum(1 for r in scored if r["custom_verified"] == r["azure_grounded"])
    return {
        "n_claims": len(rows),
        "n_scored_by_azure": len(scored),
        "agreement_rate": agree / len(scored) if scored else None,
        "rows": rows,
    }
