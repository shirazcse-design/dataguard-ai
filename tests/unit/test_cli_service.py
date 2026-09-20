"""`dataguard-uc4 classify`, `service info` and the schema commands: exit codes and hostile input."""

from __future__ import annotations

import io
import json
import sys

import pytest

from app.classification.cli import main
from app.classification.schemas import ClassificationResult

RECORD = "Employee record\nNational ID: 905-37-6209\nDate of birth: 01/27/1960\n"
COMMON = ["--llm-mode", "off"]


def run(capsys, *argv):
    rc = main(["classify", *COMMON, *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def parsed(out):
    return [
        ClassificationResult.model_validate_json(line) for line in out.splitlines() if line.strip()
    ]


# ---- inputs and exit codes --------------------------------------------------------------------
def test_classify_text_prints_one_valid_result_and_exits_zero(capsys):
    rc, out, err = run(capsys, "--text", RECORD)
    (r,) = parsed(out)
    assert rc == 0 and err == "" and r.status == "ok" and r.level.value == "HIGHLY_CONFIDENTIAL"


def test_classify_a_file_uses_only_the_basename_and_the_content(tmp_path, capsys):
    f = tmp_path / "secret_dir" / "employee_record.txt"
    f.parent.mkdir()
    f.write_text(RECORD)
    rc, out, err = run(capsys, "--file", str(f))
    assert rc == 0 and parsed(out)[0].status == "ok"
    assert "secret_dir" not in out and str(tmp_path) not in out


def test_classify_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(RECORD.encode())})())
    rc, out, _ = run(capsys, "--file", "-")
    assert rc == 0 and parsed(out)[0].level.value == "HIGHLY_CONFIDENTIAL"


def test_classify_a_json_request_from_a_file_and_from_stdin(tmp_path, monkeypatch, capsys):
    req = {
        "request_id": "req-json",
        "document": {"content": RECORD, "filename": "employee_record.txt", "extension": "txt"},
        "options": {"mode": "rules"},
    }
    f = tmp_path / "r.json"
    f.write_text(json.dumps(req))
    rc, out, _ = run(capsys, "--json", str(f))
    assert (
        rc == 0
        and parsed(out)[0].request_id == "req-json"
        and parsed(out)[0].routing.stages_run == ["rules"]
    )
    monkeypatch.setattr(
        sys, "stdin", type("S", (), {"buffer": io.BytesIO(json.dumps(req).encode())})()
    )
    rc, out, _ = run(capsys, "--json", "-")
    assert rc == 0 and parsed(out)[0].request_id == "req-json"


def test_empty_text_is_rejected_with_exit_code_3(capsys):
    rc, out, err = run(capsys, "--text", "   ")
    r = parsed(out)[0]
    assert rc == 3 and r.status == "rejected" and r.level is None and err == ""


def test_undecodable_and_oversize_files_are_rejected_without_reading_them_into_a_result(
    tmp_path, capsys
):
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"secret \xff\xfe text")
    rc, out, _ = run(capsys, "--file", str(bad))
    assert (
        rc == 3
        and parsed(out)[0].warnings == ["input_rejected:undecodable_text"]
        and "secret" not in out
    )
    huge = tmp_path / "huge.txt"
    huge.write_bytes(b"a" * 5_000_100)  # just over the 5 MB hard limit
    rc, out, _ = run(capsys, "--file", str(huge))
    assert rc == 3 and parsed(out)[0].warnings == ["input_rejected:oversize"]


def test_malformed_json_is_a_rejected_result_not_a_traceback(tmp_path, capsys):
    f = tmp_path / "r.json"
    f.write_text('{"request_id": "x", ')
    rc, out, err = run(capsys, "--json", str(f))
    assert (
        rc == 3
        and parsed(out)[0].warnings == ["input_rejected:invalid_json"]
        and "Traceback" not in err
    )
    f.write_bytes(b"\xff\xfe\x00 not utf8")
    rc, out, _ = run(capsys, "--json", str(f))
    assert rc == 3 and parsed(out)[0].status == "rejected"


def test_a_request_object_with_unknown_fields_is_rejected_naming_only_the_field(tmp_path, capsys):
    f = tmp_path / "r.json"
    f.write_text(
        json.dumps(
            {
                "request_id": "x",
                "document": {"content": "SECRET-DOC-TEXT", "filename": "a", "extension": "t"},
                "surprise": 1,
            }
        )
    )
    rc, out, _ = run(capsys, "--json", str(f))
    assert (
        rc == 3
        and "SECRET-DOC-TEXT" not in out
        and parsed(out)[0].warnings == ["invalid_request:surprise"]
    )


def test_an_unsupported_schema_version_is_rejected_with_exit_3(tmp_path, capsys):
    f = tmp_path / "r.json"
    f.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "request_id": "x",
                "document": {"content": "a", "filename": "a", "extension": "t"},
            }
        )
    )
    rc, out, _ = run(capsys, "--json", str(f))
    assert rc == 3 and parsed(out)[0].warnings == ["unsupported_schema_version:2.0"]


def test_jsonl_gives_one_result_per_line_and_the_worst_exit_code(tmp_path, capsys):
    good = {
        "request_id": "a",
        "document": {"content": RECORD, "filename": "employee_record.txt", "extension": "txt"},
    }
    f = tmp_path / "in.jsonl"
    f.write_text(
        json.dumps(good) + "\n\nnot json at all\n" + json.dumps({**good, "request_id": "c"}) + "\n"
    )
    rc, out, _ = run(capsys, "--jsonl", str(f))
    results = parsed(out)
    assert [r.request_id for r in results] == ["a", "line-2", "c"] and [
        r.status for r in results
    ] == ["ok", "rejected", "ok"]
    assert rc == 3
    assert all("\n" not in line for line in out.splitlines())  # compact, one JSON object per line


def test_jsonl_over_the_batch_limit_is_a_usage_error_before_any_work(tmp_path, capsys):
    f = tmp_path / "in.jsonl"
    f.write_text("{}\n" * 101)
    rc, out, err = run(capsys, "--jsonl", str(f))
    assert rc == 2 and out == "" and "max_batch_size" in err


def test_all_ok_jsonl_exits_zero(tmp_path, capsys):
    good = {
        "request_id": "a",
        "document": {"content": RECORD, "filename": "employee_record.txt", "extension": "txt"},
    }
    f = tmp_path / "in.jsonl"
    f.write_text(json.dumps(good) + "\n")
    assert run(capsys, "--jsonl", str(f))[0] == 0


# ---- usage and startup errors ------------------------------------------------------------------
@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--text", "a", "--file", "x"],
        ["--text", "a", "--json", "x"],
        ["--jsonl", "x", "--json", "y"],
    ],
)
def test_exactly_one_input_is_required(capsys, argv):
    rc, out, err = run(capsys, *argv)
    assert rc == 2 and out == "" and "exactly one" in err


def test_a_missing_input_file_is_a_usage_error_without_a_traceback(capsys):
    rc, out, err = run(capsys, "--file", "/no/such/file.txt")
    assert rc == 2 and out == "" and "Traceback" not in err and "cannot read input" in err


def test_a_broken_config_or_missing_credentials_fail_at_startup_with_exit_2(
    config_copy, monkeypatch, capsys
):
    (config_copy / "routing/routing.v1.yaml").write_text("not: [valid")
    rc, out, err = run(capsys, "--text", "hello", "--config-dir", str(config_copy))
    assert rc == 2 and out == "" and "SERVICE START FAILED" in err
    for var in (
        "DATAGUARD_LLM_DEPLOYMENT_MID",
        "DATAGUARD_LLM_DEPLOYMENT_LARGE",
        "DATAGUARD_LLM_DEPLOYMENT_SMALL",
    ):
        monkeypatch.delenv(var, raising=False)
    rc = main(["classify", "--text", "hello", "--llm-mode", "foundry"])
    err = capsys.readouterr().err
    assert rc == 2 and "SERVICE START FAILED" in err and "DATAGUARD_LLM_DEPLOYMENT" in err


def test_an_unknown_variant_is_a_startup_failure_not_a_traceback(capsys):
    rc = main(["classify", "--text", "a", "--llm-mode", "off", "--variant", "nope"])
    out = capsys.readouterr()
    assert rc == 2 and out.out == "" and "SERVICE START FAILED" in out.err and "nope" in out.err


# ---- options -----------------------------------------------------------------------------------
def test_the_option_flags_reach_the_router(capsys):
    rc, out, _ = run(capsys, "--text", RECORD, "--mode", "rules", "--no-evidence")
    r = parsed(out)[0]
    assert rc == 0 and r.routing.stages_run == ["rules"] and r.evidence == []
    rc, out, _ = run(
        capsys, "--text", "Notes from the supplier call about pricing.", "--max-llm-tier", "none"
    )
    assert parsed(out)[0].status == "review_required" and parsed(out)[0].level is None
    rc, out, _ = run(capsys, "--text", RECORD, "--max-latency-ms", "5", "--max-cost-usd", "0")
    assert rc == 0


def test_pretty_output_is_multiline_valid_json(capsys):
    rc, out, _ = run(capsys, "--text", RECORD, "--pretty")
    assert rc == 0 and out.count("\n") > 20 and json.loads(out)["status"] == "ok"


def test_request_id_and_caller_are_used_and_the_caller_is_pseudonymised_in_traces(tmp_path, capsys):
    trace = tmp_path / "spans.jsonl"
    rc, out, _ = run(
        capsys,
        "--text",
        RECORD,
        "--request-id",
        "req-42",
        "--caller-id",
        "alice-analyst",
        "--purpose",
        "audit",
        "--trace-out",
        str(trace),
    )
    assert rc == 0 and parsed(out)[0].request_id == "req-42"
    text = trace.read_text()
    assert "alice-analyst" not in text and "req-42" in text and "National ID" not in text


def test_stdout_is_only_json_and_stderr_is_empty_on_success(capsys):
    rc, out, err = run(capsys, "--text", RECORD)
    assert err == "" and json.loads(out)["schema_version"] == "1.0"


# ---- service info and schema commands ---------------------------------------------------------
def test_service_info_and_self_check(capsys):
    assert main(["service", "info", *COMMON, "--check"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["schema_version"] == "1.0" and info["self_check"] == {
        "ok": True,
        "status": "review_required",
    }
    assert info["stages"] == ["rules"] and info["llm_mode"] == "off"


def test_schema_export_and_examples_write_files(tmp_path, capsys):
    assert main(["schema", "export", "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "classification-result.v1.json").exists()
    assert main(["schema", "examples", "--out-dir", str(tmp_path / "ex")]) == 0
    assert len(list((tmp_path / "ex").glob("*.json"))) == 6
    capsys.readouterr()


def test_exit_codes_for_review_and_error_results_are_asserted_explicitly(capsys):
    from unittest import mock

    from app.classification.hybrid import HybridClassifier

    rc, out, _ = run(
        capsys, "--text", "Notes from the supplier call about pricing.", "--max-llm-tier", "none"
    )
    assert parsed(out)[0].status == "review_required" and rc == 0  # a review is a flag, not a block
    with mock.patch.object(
        HybridClassifier, "classify", side_effect=RuntimeError("SECRET-DOC-TEXT")
    ):
        rc, out, err = run(capsys, "--text", RECORD)
    r = parsed(out)[0]
    assert (
        rc == 4 and r.status == "error" and r.level is None and "SECRET-DOC-TEXT" not in out + err
    )


def test_an_oversize_file_is_rejected_before_it_is_hashed_or_decoded(tmp_path, capsys):
    huge = tmp_path / "huge.txt"
    huge.write_bytes(b"a" * 5_000_100)
    rc, out, _ = run(capsys, "--file", str(huge))
    assert rc == 3 and parsed(out)[0].content_hash == "0" * 64  # never read into a request


def test_input_is_read_with_a_cap_of_the_limit_plus_one_byte(tmp_path, monkeypatch):
    from app.classification.cli import _read_capped

    f = tmp_path / "big.bin"
    f.write_bytes(b"x" * 1000)
    assert len(_read_capped(str(f), 10)) == 11
    monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": io.BytesIO(b"y" * 1000)})())
    assert len(_read_capped("-", 10)) == 11


def test_mode_and_tier_options_change_which_stages_run_when_an_llm_stage_exists(tmp_path, capsys):
    from evals.classification.dataset.build import DEFAULT_DATA_DIR, load_documents

    docs = sorted(load_documents(DEFAULT_DATA_DIR, splits=["dev"]), key=lambda d: d.doc_id)
    doc = next(d for d in docs if d.tier == "T2")
    f = (
        tmp_path / doc.filename
    )  # the recorded LLM responses are keyed by input including the filename
    f.write_text(doc.content)
    base = ["classify", "--llm-mode", "replay", "--file", str(f)]

    def stages(*extra):
        rc = main([*base, *extra])
        return rc, ClassificationResult.model_validate_json(
            capsys.readouterr().out.strip()
        ).routing.stages_run

    assert stages() == (0, ["rules", "llm:mid"])
    assert stages("--mode", "rules")[1] == ["rules"]
    assert stages("--mode", "llm")[1] == ["llm:mid"]
    assert stages("--max-llm-tier", "none")[1] == ["rules"]
