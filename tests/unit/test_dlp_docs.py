"""UC1 docs: every relative link resolves, and the documented result files exist."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path("docs/uc1")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


@pytest.mark.parametrize(
    "doc", sorted(DOCS.glob("*.md")) + sorted((DOCS / "results").glob("*.md")), ids=lambda p: p.name
)
def test_relative_links_resolve(doc):
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        assert (doc.parent / target).resolve().exists(), f"{doc.name} links to missing {target}"


def test_required_uc1_documents_exist():
    for name in (
        "README",
        "product-brief",
        "architecture",
        "agent-and-tools",
        "risk-and-response",
        "evaluation",
        "responsible-ai",
        "lessons-learned",
        "interview-demo",
        "foundry-agent-setup",
        "foundry-guardrails-setup",
        "foundry-observability-setup",
        "foundry-evals-setup",
    ):
        assert (DOCS / f"{name}.md").exists(), name
