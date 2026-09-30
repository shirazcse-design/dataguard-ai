"""UC6 Foundry Evaluations export: row shapes, and the guide's expected pass counts must equal a
fresh REPLAY of the committed recordings (so the manual guide cannot drift from the data)."""

from __future__ import annotations

import re
from pathlib import Path

from app.policy.service import build_copilot
from evals.policy.foundry_export import build_rows
from evals.policy.golden import load_golden

ITEMS, _ = load_golden()


def test_rows_and_the_guides_expected_counts_match_replay():
    answers, outcomes = build_rows(build_copilot("replay"), ITEMS, ["naive", "advanced", "agentic"])
    assert len(outcomes) == 108
    assert {"query", "response", "context", "ground_truth"} <= set(answers[0])
    assert all(r["response"] and r["context"] for r in answers)
    assert all(r["mode"] == "replay" for r in outcomes)
    doc = Path("docs/uc6/foundry-evals-setup.md").read_text(encoding="utf-8")
    for grader in (
        "status_ok",
        "no_forbidden_phrase",
        "no_unverified_shown",
        "no_fabricated_citation",
    ):
        row = re.search(rf"^\| {grader} \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|$", doc, re.M)
        assert row, grader
        got = [
            sum(o[grader] == "pass" for o in outcomes if o["level"] == lv)
            for lv in ("naive", "advanced", "agentic")
        ]
        assert [int(x) for x in row.groups()] == [*got, sum(got)], grader
    counts = {lv: sum(r["level"] == lv for r in answers) for lv in ("naive", "advanced", "agentic")}
    assert (
        f"naive {counts['naive']}, advanced {counts['advanced']}, agentic {counts['agentic']}"
        in doc
    )


def test_withheld_evidence_never_enters_the_exported_context():
    from app.policy.config import load_policy_config
    from app.policy.corpus import load_corpus
    from app.policy.embeddings import HashingEmbedder

    cfg, _ = load_policy_config()
    corpus = load_corpus(cfg.corpus.dir, cfg.chunking)
    # Advanced WITHOUT the draft filter, so the poisoned draft is retrieved and must be withheld.
    lv = cfg.levels["advanced"].model_copy(update={"metadata_filter": False})
    open_cfg = cfg.model_copy(update={"levels": {**cfg.levels, "advanced": lv}})
    cp = build_copilot("offline", embedder=HashingEmbedder(), config=(open_cfg, corpus))
    x02 = [i for i in ITEMS if i.id == "X02"]
    answer = cp.answer(x02[0].question, "advanced")
    assert any(e.flagged_injection for e in answer.evidence)  # the draft really was retrieved
    answers, _ = build_rows(cp, x02, ["advanced"])
    assert answers and all("ignore previous instructions" not in r["context"] for r in answers)
