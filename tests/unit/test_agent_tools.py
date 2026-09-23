"""The Batch Triage Agent's fixed tool registry: classify_document, lookup_taxonomy_definition,
request_human_review. Stateless per call; only three tools exist."""

from __future__ import annotations

import pytest

from app.agent.tools import TOOL_NAMES, ToolRegistry, tool_schemas
from app.classification.config_loader import load_config
from app.classification.service import ClassificationService


@pytest.fixture(scope="module")
def registry():
    bundle = load_config()
    return ToolRegistry(ClassificationService(llm_mode="off"), bundle)


def test_tool_schemas_name_exactly_the_three_allowed_tools():
    names = {s["function"]["name"] for s in tool_schemas()}
    assert (
        names
        == set(TOOL_NAMES)
        == {
            "classify_document",
            "lookup_taxonomy_definition",
            "request_human_review",
        }
    )


def test_an_unknown_tool_name_is_rejected_not_silently_ignored(registry):
    result = registry.call("delete_everything", {})
    assert result.ok is False and result.error_kind == "unknown_tool"


# ---- classify_document ---------------------------------------------------------------------
def test_classify_document_returns_a_real_classification_result(registry):
    result = registry.call(
        "classify_document",
        {"content": "Employee record\nNational ID: 905-37-6209\n", "filename": "hr.txt"},
    )
    assert result.ok is True
    assert result.data["level"]["value"] == "HIGHLY_CONFIDENTIAL"
    assert "PII" in [c["id"] for c in result.data["categories"]]
    assert result.data["high_risk"]["value"] is True


def test_classify_document_rejects_missing_or_empty_content(registry):
    assert registry.call("classify_document", {}).error_kind == "invalid_arguments"
    assert registry.call("classify_document", {"content": ""}).error_kind == "invalid_arguments"
    assert registry.call("classify_document", {"content": 5}).error_kind == "invalid_arguments"


def test_classify_document_defaults_the_filename(registry):
    result = registry.call("classify_document", {"content": "A public press release."})
    assert result.ok is True


# ---- lookup_taxonomy_definition ------------------------------------------------------------
def test_lookup_taxonomy_definition_finds_a_level(registry):
    result = registry.call("lookup_taxonomy_definition", {"id": "HIGHLY_CONFIDENTIAL"})
    assert result.ok is True
    assert result.data["id"] == "HIGHLY_CONFIDENTIAL"
    assert result.data["description"]


def test_lookup_taxonomy_definition_finds_a_category(registry):
    result = registry.call("lookup_taxonomy_definition", {"id": "PHI"})
    assert result.ok is True and result.data["id"] == "PHI"


def test_lookup_taxonomy_definition_rejects_an_unknown_id(registry):
    result = registry.call("lookup_taxonomy_definition", {"id": "NOT_A_REAL_ID"})
    assert result.ok is False and result.error_kind == "unknown_id"


def test_lookup_taxonomy_definition_rejects_missing_id(registry):
    assert registry.call("lookup_taxonomy_definition", {}).error_kind == "invalid_arguments"


# ---- request_human_review -------------------------------------------------------------------
def test_request_human_review_returns_a_recommendation_never_an_action(registry):
    result = registry.call("request_human_review", {"reason": "ambiguous document"})
    assert result.ok is True
    assert result.data == {"requested": True, "reason": "ambiguous document"}


def test_request_human_review_rejects_missing_reason(registry):
    assert registry.call("request_human_review", {}).error_kind == "invalid_arguments"


def test_request_human_review_truncates_a_long_reason(registry):
    result = registry.call("request_human_review", {"reason": "x" * 1000})
    assert len(result.data["reason"]) == 200
