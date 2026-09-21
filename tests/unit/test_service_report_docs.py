"""The generated service report, and the documentation it depends on."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.classification.cli import main

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs" / "uc4"


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    out = tmp_path_factory.mktemp("svc") / "service.md"
    assert main(["service", "report", "--out", str(out)]) == 0
    return out.read_text()


def test_report_states_provenance_and_that_the_locked_split_was_not_read(report):
    assert (
        "second independent review pending" in report
        and "The locked test split was not read" in report
    )
    assert "recommendations" in report and "service-plan.md" in report


def test_report_has_every_section(report):
    for h in (
        "## Frozen schema",
        "## Golden examples",
        "## CLI exit codes",
        "## Version block",
        "## MCP freeze criteria",
        "## Caveats",
    ):
        assert h in report, h


def test_every_exit_code_scenario_passes_and_covers_all_four_codes(report):
    section = report[report.index("## CLI exit codes") : report.index("## Version block")]
    assert "**FAIL**" not in section and section.count("| PASS |") == 9
    for code in ("| 0 |", "| 2 |", "| 3 |", "| 4 |"):
        assert code in section


def test_every_example_validates_both_ways_and_no_schema_drift(report):
    section = report[report.index("## Golden examples") : report.index("## CLI exit codes")]
    assert "**NO**" not in section and section.count("| yes | yes | yes |") == 6
    assert (
        "**" not in report[report.index("## Frozen schema") : report.index("## Golden examples")]
    )  # no drift


def test_the_freeze_criteria_are_computed_and_the_gates_criterion_is_reported_honestly(report):
    crit = report[report.index("## MCP freeze criteria") :]
    assert "| The result schema is versioned | MET |" in crit
    assert "| The eval gates have passed (strict metric, dev, as first stated) |" in crit
    assert "point / lower bound" in crit
    assert "| Approval to build MCP | GIVEN |" in crit and "decision A29" in crit
    # the computed strict/dev row must agree with the numbers it prints: any FAIL means NOT MET
    row = next(
        x for x in crit.splitlines() if x.startswith("| The eval gates have passed (strict metric")
    )
    assert ("**NOT MET**" in row) == ("FAIL" in row.split("|")[3])


def test_the_adopted_gate_row_is_stated_as_a_decision_and_its_figures_match_the_rescore_doc(report):
    crit = report[report.index("## MCP freeze criteria") :]
    row = next(x for x in crit.splitlines() if "adopted gate: lenient level, decision A33" in x)
    assert "| MET |" in row and "not used for the gate" in row  # dev is disclosed, not hidden
    doc = (DOCS / "results" / "validation-rescore.md").read_text()
    for figure in ("0.884 [0.758, 0.980]", "0.859 [0.685, 1.000]", "1.000 [1.000, 1.000]", "0.997"):
        assert figure in row and figure in doc, figure
    assert "release-ready for the v0.1 scope" in crit and "strict level gate still fails" in crit


# ---- documentation integrity -------------------------------------------------------------------
LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(?:#[^)]*)?\)")


@pytest.mark.parametrize("doc", sorted(DOCS.glob("*.md")), ids=lambda p: p.name)
def test_relative_links_in_the_uc4_docs_resolve(doc):
    for target in LINK.findall(doc.read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        assert (doc.parent / target).resolve().exists(), f"{doc.name} links to missing {target}"


def test_the_mcp_contract_states_release_readiness_as_a_decision_with_its_limits():
    text = (DOCS / "mcp-contract.md").read_text()
    assert "release-ready for the v0.1 scope" in text and "decision A33" in text
    assert "Release-ready does not mean production-hardened" in text
    for limit in ("asserted, not verified", "placeholder", "Rate limited", "10 s limit"):
        assert limit in text, limit
    assert "strict** level gate still fails" in text  # the unmet strict result stays visible
    assert "Approval to build MCP | Given" in text
    assert (ROOT / "mcp_adapter" / "adapter.py").exists()
    # a top-level `mcp` package would shadow the MCP SDK on import
    assert not (ROOT / "mcp").exists()


def test_the_service_docs_document_every_exit_code():
    text = (DOCS / "service-api.md").read_text()
    for code in ("| 0 |", "| 3 |", "| 4 |", "| 2 |"):
        assert code in text
    assert "a review is a flag, not a block" in text.lower()


def test_the_result_schema_doc_covers_every_status_and_field():
    text = (DOCS / "result-schema.md").read_text()
    for status in ("`ok`", "`degraded`", "`review_required`", "`rejected`", "`error`"):
        assert status in text
    for field in (
        "`level`",
        "`categories`",
        "`high_risk`",
        "`review`",
        "`evidence`",
        "`routing`",
        "`versions`",
        "`telemetry`",
        "`guardrail_events`",
        "`warnings`",
    ):
        assert field in text
