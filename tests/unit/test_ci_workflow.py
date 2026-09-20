"""The CI workflow must be valid YAML with well-formed jobs (an invalid workflow runs NO jobs at all)."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def load():
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_workflow_is_valid_yaml_with_the_expected_top_level_keys():
    wf = load()
    assert (
        wf["name"] == "CI" and "jobs" in wf and wf[True if True in wf else "on"]
    )  # PyYAML reads `on` as True


def test_every_job_has_a_string_name_a_runner_and_steps():
    for key, job in load()["jobs"].items():
        assert isinstance(job["name"], str) and job["name"].strip(), key
        assert "runs-on" in job and job["steps"], key
        for step in job["steps"]:
            assert "uses" in step or "run" in step, (key, step)


def test_job_names_are_unique():
    names = [j["name"] for j in load()["jobs"].values()]
    assert len(names) == len(set(names))


def test_the_phase_jobs_are_present():
    keys = set(load()["jobs"])
    assert {
        "lint-and-test",
        "rules-baseline",
        "ml-baseline",
        "llm-status",
        "hybrid-report",
        "observability",
        "service",
    } <= keys


def test_no_secret_or_credential_is_written_into_the_workflow():
    text = WORKFLOW.read_text().lower()
    for needle in ("api_key=", "api-key:", "secret:", "password", "bearer "):
        assert needle not in text, needle
