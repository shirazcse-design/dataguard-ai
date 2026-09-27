"""Curated demo documents: REAL synthetic dev-split documents, never invented text.

They are dev-split documents because the recorded LLM responses (`data/llm_cache/`) cover them, so
REPLAY mode reproduces the frozen hybrid's real answers without a network call. The expected
outcomes below were observed by running the service (replay) and are pinned by a unit test, so an
example that stops behaving as described fails CI instead of surprising an interview.

`llm_tiers: "off"` sets the request option `max_llm_tier: "none"` - a real service option, not a
UI trick - to show the fail-safe path: without an LLM verdict the router escalates to review
instead of defaulting to a low level.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents


@dataclass(frozen=True)
class Example:
    key: str
    doc_id: str
    title: str
    story: str
    llm_tiers: str  # "default" | "off"
    expect_status: str
    expect_level: str | None


EXAMPLES: tuple[Example, ...] = (
    Example(
        "public", "uc4-2154faf7ae", "Public careers FAQ",
        "Published web content: no sensitive data, so PUBLIC.", "default", "ok", "PUBLIC",
    ),
    Example(
        "internal", "uc4-53b989fc54", "Team meeting notes",
        "Ordinary internal notes: INTERNAL, no data categories.", "default", "ok", "INTERNAL",
    ),
    Example(
        "confidential", "uc4-288d0c258b", "Manager's private notes (PII)",
        "CONFIDENTIAL, but PII makes it high-risk: high-risk comes from policy, not the level.",
        "default", "ok", "CONFIDENTIAL",
    ),
    Example(
        "healthcare", "uc4-19c5bd1307", "Prescription record (healthcare)",
        "PHI detected by rules and confirmed by the LLM: HIGHLY_CONFIDENTIAL and high-risk.",
        "default", "ok", "HIGHLY_CONFIDENTIAL",
    ),
    Example(
        "review", "uc4-1807887255", "Customer case-study draft, LLM unavailable",
        "An ambiguous draft with the LLM tiers turned off for this request: rules alone cannot "
        "decide, so it escalates to human review with no label - never a default of PUBLIC.",
        "off", "review_required", None,
    ),
)  # fmt: skip

BY_KEY = {e.key: e for e in EXAMPLES}


@lru_cache(maxsize=1)
def _dev_docs() -> dict[str, Any]:
    return {d.doc_id: d for d in load_documents(DEFAULT_DATA_DIR, splits=["dev"])}


def document(example: Example) -> Any:
    return _dev_docs()[example.doc_id]


def listing() -> list[dict[str, Any]]:
    out = []
    for e in EXAMPLES:
        d = document(e)
        out.append(
            {
                "key": e.key,
                "title": e.title,
                "story": e.story,
                "llm_tiers": e.llm_tiers,
                "doc_id": e.doc_id,
                "family_id": d.family_id,
                "tier": d.tier,
                "gold_level": d.gold_level,
                "gold_categories": list(d.gold_categories),
                "filename": d.filename,
                "content": d.content,
                "expect": {"status": e.expect_status, "level": e.expect_level},
            }
        )
    return out
