"""Logging evaluation results to Foundry's Evaluations page (`evals/classification/foundry_evals.py`).

No test contacts Azure: `run_in_foundry` runs against a fake `openai.evals`. The rows are built from
the real prediction/report shapes, and a privacy test checks that no document text can be sent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from app.classification.cli import main
from evals.classification import foundry_evals as fe


def pred(doc_id="d1", gold="PUBLIC", level="PUBLIC", alts=(), gold_hr=False, pred_hr=False, **kw):
    return {
        "doc_id": doc_id, "family_id": "f", "tier": "T1", "gold_level": gold, "pred_level": level,
        "gold_alternative_levels": list(alts), "gold_high_risk": gold_hr, "pred_high_risk": pred_hr,
        "status": "ok", **kw,
    }  # fmt: skip


def rates(rows, criteria):
    return {k: v["passed"] for k, v in fe.local_pass_rates(rows, criteria).items()}


# ---- classifier rows and criteria ------------------------------------------------------------
def test_strict_and_lenient_level_checks():
    rows = fe.classifier_rows(
        [
            pred("exact"),
            pred("alt", gold="INTERNAL", level="CONFIDENTIAL", alts=["CONFIDENTIAL"]),
            pred("wrong", gold="PUBLIC", level="INTERNAL"),
        ]
    )
    assert rates(rows, fe.CLASSIFIER_CRITERIA)["level_exact"] == 1
    assert rates(rows, fe.CLASSIFIER_CRITERIA)["level_acceptable"] == 2


def test_an_undecided_document_is_a_level_miss_never_a_silent_pass():
    (row,) = fe.classifier_rows([pred(level=None, status="review_required")])
    assert row["pred_level"] == fe.NONE
    assert rates([row], fe.CLASSIFIER_CRITERIA) == {
        "level_exact": 0, "level_acceptable": 0, "no_missed_high_risk": 1,
    }  # fmt: skip


def test_only_a_gold_high_risk_document_can_be_a_missed_high_risk():
    rows = fe.classifier_rows(
        [
            pred("missed", gold_hr=True, pred_hr=False),
            pred("caught", gold_hr=True, pred_hr=True),
            pred("over", gold_hr=False, pred_hr=True),  # over-flagging is not a miss
        ]
    )
    assert [r["high_risk_missed"] for r in rows] == ["yes", "no", "no"]
    assert rates(rows, fe.CLASSIFIER_CRITERIA)["no_missed_high_risk"] == 2


# ---- agent rows and criteria -----------------------------------------------------------------
def report(*docs):
    return {"documents": list(docs)}


def ann(doc_id="d1", level="INTERNAL", stopped="completed", tools=("classify_document",)):
    return {
        "doc_id": doc_id, "level": level, "stopped_reason": stopped, "review_requested": False,
        "priority": "low", "tool_calls": [{"tool": t, "ok": True} for t in tools],
    }  # fmt: skip


def test_the_agent_invariant_is_checked_against_the_classifiers_own_decision():
    rows = fe.agent_rows(
        report(ann("same", "INTERNAL"), ann("differs", "PUBLIC")),
        {"same": "INTERNAL", "differs": "CONFIDENTIAL"},
    )
    assert rates(rows, fe.AGENT_CRITERIA)["decision_matches_classifier"] == 1


def test_task_completion_and_classify_called_use_the_real_tool_record():
    rows = fe.agent_rows(
        report(
            ann("ok", tools=("classify_document", "request_human_review")),
            ann("budget", stopped="step_budget_exceeded", tools=("lookup_taxonomy_definition",)),
        ),
        {"ok": "INTERNAL", "budget": "INTERNAL"},
    )
    r = rates(rows, fe.AGENT_CRITERIA)
    assert r["task_completed"] == 1 and r["classify_document_called"] == 1


def test_failed_tool_calls_do_not_count_as_called():
    d = ann(tools=())
    d["tool_calls"] = [{"tool": "classify_document", "ok": False}]
    (row,) = fe.agent_rows(report(d), {"d1": "INTERNAL"})
    assert row["tools_called"] == ""


# ---- privacy ---------------------------------------------------------------------------------
def test_no_document_text_or_rationale_can_reach_the_payload():
    p = pred(content="National ID 905-37-6209", filename="hr.txt", evidence=["905-37-6209"])
    a = ann()
    a["rationale"] = "quotes 905-37-6209"
    payload = [
        fe.eval_payload("c", fe.CLASSIFIER_FIELDS, fe.CLASSIFIER_CRITERIA, fe.classifier_rows([p])),
        fe.eval_payload("a", fe.AGENT_FIELDS, fe.AGENT_CRITERIA, fe.agent_rows(report(a), {})),
    ]
    blob = json.dumps(payload)
    assert "905-37-6209" not in blob and "hr.txt" not in blob
    assert set(payload[0]["rows"][0]) == set(fe.CLASSIFIER_FIELDS)
    assert set(payload[1]["rows"][0]) == set(fe.AGENT_FIELDS)


def test_the_item_schema_declares_every_field_as_a_required_string():
    schema = fe.item_schema(fe.AGENT_FIELDS)
    assert schema["required"] == list(fe.AGENT_FIELDS)
    assert all(v == {"type": "string"} for v in schema["properties"].values())


@pytest.mark.parametrize(
    ("op", "value", "ref", "expected"),
    [("eq", "a", "a", True), ("ne", "a", "b", True), ("like", "x,classify_document", "classify_document", True),
     ("ilike", "ABC", "b", True), ("eq", "a", "A", False)],
)  # fmt: skip
def test_the_local_string_check_matches_the_grader_semantics(op, value, ref, expected):
    c = {"input": "{{item.v}}", "reference": ref, "operation": op}
    assert fe._check(c, {"v": value}) is expected


# ---- running in Foundry (fake client) --------------------------------------------------------
class FakeEvals:
    def __init__(self, statuses):
        self.created, self.run_args, self.statuses = None, None, list(statuses)
        self.runs = NS(create=self._run_create, retrieve=self._retrieve)

    def create(self, **kwargs):
        self.created = kwargs
        return NS(id="eval_1")

    def _run(self):
        status = self.statuses.pop(0)
        results = [
            NS(testing_criteria="level_exact-abc", passed=3, failed=1),
            NS(testing_criteria="no_missed_high_risk", passed=4, failed=0),
        ]
        return NS(id="run_1", status=status, report_url="https://ai.azure.com/r/x",
                  per_testing_criteria_results=results if status == "completed" else None)  # fmt: skip

    def _run_create(self, **kwargs):
        self.run_args = kwargs
        return self._run()

    def _retrieve(self, **kwargs):
        return self._run()


def test_run_in_foundry_sends_inline_rows_polls_and_maps_criteria_back():
    evals = FakeEvals(["queued", "in_progress", "completed"])
    rows = fe.classifier_rows([pred()])
    payload = fe.eval_payload(
        "dataguard-classifier", fe.CLASSIFIER_FIELDS, fe.CLASSIFIER_CRITERIA, rows
    )
    out = fe.run_in_foundry(NS(evals=evals), payload, "run-a", sleep=lambda s: None)

    assert evals.created["name"] == "dataguard-classifier"
    assert evals.created["testing_criteria"] == fe.CLASSIFIER_CRITERIA
    src = evals.run_args["data_source"]
    assert src["type"] == "jsonl" and src["source"]["type"] == "file_content"
    assert src["source"]["content"] == [{"item": r} for r in rows]
    assert out["status"] == "completed" and out["report_url"].startswith("https://")
    assert out["per_criterion"]["level_exact"] == {"passed": 3, "failed": 1}


def test_run_in_foundry_stops_waiting_at_the_timeout():
    evals = FakeEvals(["queued"] * 10)
    payload = fe.eval_payload("x", fe.AGENT_FIELDS, fe.AGENT_CRITERIA, [])
    out = fe.run_in_foundry(
        NS(evals=evals), payload, "r", poll_s=5, timeout_s=10, sleep=lambda s: None
    )
    assert out["status"] == "queued"


# ---- CLI -------------------------------------------------------------------------------------
def write_inputs(tmp_path):
    preds = tmp_path / "p.jsonl"
    preds.write_text("\n".join(json.dumps(pred(f"d{i}")) for i in range(3)), encoding="utf-8")
    rep = tmp_path / "r.json"
    rep.write_text(
        json.dumps(report(*(ann(f"d{i}", "PUBLIC") for i in range(3)))), encoding="utf-8"
    )
    return preds, rep


def test_dry_run_writes_the_exact_payload_and_contacts_nothing(tmp_path, capsys, monkeypatch):
    import app.agent.foundry_service as fs

    monkeypatch.setattr(fs, "project_client", lambda *a, **k: pytest.fail("contacted Foundry"))
    preds, rep = write_inputs(tmp_path)
    out = tmp_path / "payload.json"
    args = ["eval", "foundry-log", "--predictions", str(preds), "--agent-report", str(rep)]
    assert main([*args, "--dry-run", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert [p["name"] for p in payload] == ["dataguard-classifier", "dataguard-agent"]
    local = json.loads(capsys.readouterr().out)["local"]
    assert local["dataguard-agent"]["decision_matches_classifier"]["rate"] == 1.0


def test_an_agent_report_for_documents_not_in_the_predictions_is_refused(tmp_path, capsys):
    preds, _ = write_inputs(tmp_path)
    rep = tmp_path / "other.json"
    rep.write_text(json.dumps(report(ann("unknown"))), encoding="utf-8")
    args = ["eval", "foundry-log", "--predictions", str(preds), "--agent-report", str(rep)]
    assert main([*args, "--dry-run", str(tmp_path / "x.json")]) == 2
    assert "unknown" in capsys.readouterr().err


def test_logging_without_a_project_endpoint_is_a_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATAGUARD_FOUNDRY_PROJECT_ENDPOINT", raising=False)
    preds, _ = write_inputs(tmp_path)
    assert main(["eval", "foundry-log", "--predictions", str(preds)]) == 2
    assert "DATAGUARD_FOUNDRY_PROJECT_ENDPOINT" in capsys.readouterr().err


def test_logging_runs_each_eval_in_foundry_and_prints_both_views(tmp_path, monkeypatch, capsys):
    import app.agent.foundry_service as fs

    monkeypatch.setenv(
        "DATAGUARD_FOUNDRY_PROJECT_ENDPOINT", "https://x.services.ai.azure.com/api/projects/p"
    )
    runs = []

    def fake_run(client, payload, run_name, **kw):
        runs.append(payload["name"])
        return {
            "status": "completed",
            "report_url": "u",
            "per_criterion": {},
            "eval_id": "e",
            "run_id": "r",
        }

    import evals.classification.foundry_evals as fe_mod

    monkeypatch.setattr(fe_mod, "run_in_foundry", fake_run)
    monkeypatch.setattr(fs, "project_client", lambda e, **k: NS(get_openai_client=lambda: NS()))
    preds, rep = write_inputs(tmp_path)
    assert (
        main(["eval", "foundry-log", "--predictions", str(preds), "--agent-report", str(rep)]) == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert runs == ["dataguard-classifier", "dataguard-agent"]
    assert set(out) == {"local", "foundry"}
