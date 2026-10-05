"""UC2 docs: every relative link resolves, the required documents exist, and code/config comments
point only at UC2 documents that exist."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path("docs/uc2")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


@pytest.mark.parametrize(
    "doc", sorted(DOCS.glob("*.md")) + sorted((DOCS / "results").glob("*.md")), ids=lambda p: p.name
)
def test_relative_links_resolve(doc):
    for target in LINK.findall(doc.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        assert (doc.parent / target).resolve().exists(), f"{doc.name} links to missing {target}"


def test_required_uc2_documents_exist():
    for name in (
        "README",
        "product-brief",
        "architecture",
        "ml-design",
        "agent-and-tools",
        "risk-and-response",
        "evaluation",
        "responsible-ai",
        "learning-loop",
        "lessons-learned",
        "interview-demo",
        "foundry-agents-setup",
        "foundry-guardrails-setup",
        "foundry-observability-setup",
        "foundry-evals-setup",
    ):
        assert (DOCS / f"{name}.md").exists(), name


def test_code_and_config_reference_only_existing_uc2_docs():
    ref = re.compile(r"docs/uc2/([A-Za-z0-9_-]+\.md)")
    for root in ("app", "evals", "config", "ml", "prompts", "mcp_adapter", "observability"):
        for f in Path(root).rglob("*"):
            if f.suffix not in (".py", ".yaml", ".md", ".js") or not f.is_file():
                continue
            for name in ref.findall(f.read_text(encoding="utf-8", errors="ignore")):
                assert (DOCS / name).exists(), f"{f} references missing docs/uc2/{name}"
