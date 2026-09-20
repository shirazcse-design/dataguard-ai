"""The frozen v1 schemas, the compatibility check, and the golden examples."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from app.classification.cli import main
from app.classification.schema_freeze import (
    MODELS,
    SCHEMA_DIR,
    check_frozen,
    diff_signatures,
    digest,
    export_schema,
    frozen_filename,
    signature,
    write_frozen,
)
from app.classification.schemas import ClassificationRequest, ClassificationResult
from app.classification.schemas.common import SCHEMA_VERSION
from evals.classification.schema_examples import write_examples

EXAMPLES = SCHEMA_DIR / "examples"
STATUSES = {"ok", "degraded", "review_required", "rejected", "error"}


def load(name):
    return json.loads((SCHEMA_DIR / frozen_filename(name)).read_text())


# ---- the frozen files ------------------------------------------------------------------------
def test_frozen_schemas_exist_are_valid_json_schemas_and_carry_the_version():
    for name in MODELS:
        schema = load(name)
        Draft202012Validator.check_schema(schema)
        assert schema["x-schema-version"] == SCHEMA_VERSION and schema["$id"].endswith(
            frozen_filename(name)
        )


def test_the_models_have_not_drifted_from_the_frozen_schemas():
    report = check_frozen()
    for name, r in report.items():
        assert not r["missing"], name
        assert r["breaking"] == [] and r["additive"] == [], (name, r)
        assert r["frozen_digest"] == r["current_digest"]


def test_the_schema_check_command_passes_and_fails_on_drift(tmp_path, capsys):
    assert main(["schema", "check"]) == 0
    out = capsys.readouterr().out
    assert out.count("unchanged") == 2
    drifted = tmp_path / "schema"
    write_frozen(drifted)
    p = drifted / frozen_filename("classification-result")
    d = json.loads(p.read_text())
    d["properties"].pop(
        "warnings"
    )  # the "frozen" contract had a field the models no longer... reverse: remove from frozen
    p.write_text(json.dumps(d))
    assert main(["schema", "check", "--dir", str(drifted)]) == 1
    assert (
        "additive" in capsys.readouterr().out
    )  # the models gained a field relative to that frozen file
    empty = tmp_path / "none"
    assert main(["schema", "check", "--dir", str(empty)]) == 1


def test_export_is_idempotent_and_reproduces_the_committed_files(tmp_path):
    written = write_frozen(tmp_path)
    for path in written:
        assert json.loads(path.read_text()) == json.loads((SCHEMA_DIR / path.name).read_text())


# ---- the compatibility classifier ------------------------------------------------------------
def base_sig():
    return signature(export_schema("classification-result"))


def mutate(fn):
    schema = export_schema("classification-result")
    fn(schema)
    return signature(schema)


@pytest.mark.parametrize("label, fn, kind", [
    ("remove a property", lambda s: s["properties"].pop("warnings"), "breaking"),
    ("change a type", lambda s: s["properties"]["request_id"].update(type="integer"), "breaking"),
    ("add a REQUIRED property", lambda s: (s["properties"].update(newfield={"type": "string"}), s["required"].append("newfield")), "breaking"),
    ("make an optional property required", lambda s: s["required"].append("warnings"), "breaking"),
    ("add an OPTIONAL property", lambda s: s["properties"].update(newfield={"type": "string"}), "additive"),
    ("relax a required property", lambda s: s["required"].remove("request_id"), "additive"),
])  # fmt: skip
def test_the_diff_classifies_each_kind_of_change(label, fn, kind):
    frozen = base_sig()
    current = mutate(fn)
    breaking, additive = diff_signatures(frozen, current)
    if kind == "breaking":
        assert breaking, label
    elif kind == "additive":
        assert additive and not breaking, label
    assert diff_signatures(frozen, frozen) == ([], [])


def test_cosmetic_schema_changes_do_not_count_as_drift():
    def cosmetic(s):
        s["title"] = "Renamed"
        s["description"] = "changed prose"
        for prop in s["properties"].values():
            prop["title"] = "X"
            prop["description"] = "y"

    assert diff_signatures(base_sig(), mutate(cosmetic)) == ([], [])


def test_removing_or_adding_a_whole_model_is_detected():
    frozen = base_sig()
    without = {k: v for k, v in frozen.items() if k != "Evidence"}
    assert any("model removed" in b for b in diff_signatures(frozen, without)[0])
    assert any("new model" in a for a in diff_signatures(without, frozen)[1])


def test_narrowing_an_enum_or_a_constraint_is_breaking_and_widening_an_enum_is_too():
    frozen = base_sig()
    status = export_schema("classification-result")["properties"]["status"]
    assert "enum" in status  # Literal types are exported as inline enums

    def with_status(values):
        schema = export_schema("classification-result")
        schema["properties"]["status"]["enum"] = values
        return signature(schema)

    assert diff_signatures(frozen, with_status(status["enum"][:-1]))[0]  # a value removed
    assert diff_signatures(frozen, with_status([*status["enum"], "maybe"]))[0]  # a value added

    def with_min_items():
        schema = export_schema("classification-result")
        schema["properties"]["categories"]["minItems"] = 1
        return signature(schema)

    assert diff_signatures(frozen, with_min_items())[0]  # a new constraint on an existing field


def test_the_digest_changes_when_the_contract_changes():
    a = export_schema("classification-result")
    b = export_schema("classification-result")
    b["properties"]["extra"] = {"type": "string"}
    assert digest(a) == digest(export_schema("classification-result")) and digest(a) != digest(b)


# ---- golden examples --------------------------------------------------------------------------
def example_files():
    return sorted(EXAMPLES.glob("*.json"))


def test_there_is_a_golden_example_for_every_status():
    seen = {json.loads(p.read_text())["result"]["status"] for p in example_files()}
    assert seen == STATUSES


@pytest.mark.parametrize("path", example_files(), ids=lambda p: p.stem)
def test_every_example_validates_against_the_frozen_json_schemas(path):
    body = json.loads(path.read_text())
    Draft202012Validator(load("classification-request")).validate(body["request"])
    Draft202012Validator(load("classification-result")).validate(body["result"])
    assert body["result"]["schema_version"] == body["request"]["schema_version"] == SCHEMA_VERSION


@pytest.mark.parametrize("path", example_files(), ids=lambda p: p.stem)
def test_every_example_parses_with_the_current_models(path):
    body = json.loads(path.read_text())
    ClassificationRequest.model_validate(body["request"])
    result = ClassificationResult.model_validate(body["result"])
    assert result.status == body["result"]["status"]
    # a round trip through the model reproduces the document exactly
    assert json.loads(result.model_dump_json()) == body["result"]


def test_the_examples_carry_no_sensitive_level_for_rejected_and_error_results():
    for p in example_files():
        r = json.loads(p.read_text())["result"]
        if r["status"] in ("rejected", "error"):
            assert r["level"] is None and r["categories"] == [] and r["high_risk"] is None


def test_the_examples_are_in_sync_with_the_code_that_generates_them(tmp_path):
    for path in write_examples(tmp_path):
        assert path.read_text() == (EXAMPLES / path.name).read_text(), path.name


def test_a_schema_invalid_result_is_rejected_by_the_frozen_schema():
    ok = json.loads((EXAMPLES / "ok-rules.json").read_text())["result"]
    validator = Draft202012Validator(load("classification-result"))
    bad = copy.deepcopy(ok)
    bad["surprise"] = 1
    assert list(validator.iter_errors(bad))  # additional properties are refused
    bad = copy.deepcopy(ok)
    bad["status"] = "maybe"
    assert list(validator.iter_errors(bad))
    bad = copy.deepcopy(ok)
    del bad["request_id"]
    assert list(validator.iter_errors(bad))


def test_the_schema_directory_only_contains_the_expected_files():
    assert {p.name for p in SCHEMA_DIR.iterdir() if p.is_file()} == {
        "classification-request.v1.json", "classification-result.v1.json", "CHANGELOG.md"
    }  # fmt: skip
    assert isinstance(SCHEMA_DIR, Path)


def test_a_changed_reference_or_additional_properties_policy_is_breaking():
    frozen = base_sig()
    schema = export_schema("classification-result")
    refs = [k for k, v in schema["properties"].items() if "$ref" in v]
    assert refs
    other = next(
        n for n in schema["$defs"] if n != schema["properties"][refs[0]]["$ref"].rsplit("/", 1)[-1]
    )
    schema["properties"][refs[0]]["$ref"] = f"#/$defs/{other}"
    assert diff_signatures(frozen, signature(schema))[0]  # the field now points at another model
    schema2 = export_schema("classification-result")
    schema2["additionalProperties"] = True
    assert any("additionalProperties" in b for b in diff_signatures(frozen, signature(schema2))[0])
