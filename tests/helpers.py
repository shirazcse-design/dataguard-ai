"""Shared builders for tests."""

from __future__ import annotations

from app.classification.schemas.common import sha256_text
from evals.classification.dataset.schema import DatasetDocument
from evals.classification.records import PredictionRecord

LEVELS = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]
CATS = [
    "PII", "PHI", "FINANCIAL_PCI", "SOURCE_CODE",
    "CREDENTIALS_SECRETS", "INTELLECTUAL_PROPERTY", "TRADE_SECRET", "MA_CORP_STRATEGY",
]  # fmt: skip


def mkdoc(doc_id="d1", *, level="INTERNAL", cats=(), group=None, split="test", tier="T1", **kw):
    content = kw.pop(
        "content", f"Synthetic test document {doc_id} with enough characters to be valid."
    )
    base = dict(
        doc_id=doc_id, split=split, tier=tier, family_id=group or doc_id, group_id=group or doc_id,
        generator="g", format="memo", filename=f"{doc_id}.txt", extension="txt", content=content,
        gold_level=level, gold_categories=list(cats), taxonomy_version="1.0.0",
        content_hash=sha256_text(content),
    )  # fmt: skip
    base.update(kw)
    return DatasetDocument(**base)


def mkrec(
    gold_level, pred_level, gold_cats=(), pred_cats=(), *, group="g", doc_id=None, hr=None, **kw
):
    """A PredictionRecord with derived high-risk taken from `hr=(gold, pred)` when given."""
    doc_id = (
        doc_id
        or f"r{abs(hash((gold_level, pred_level, tuple(gold_cats), tuple(pred_cats), group))) % 10**8}"
    )
    gold_hr, pred_hr = hr if hr is not None else (False, False)
    base = dict(
        doc_id=doc_id, group_id=group, family_id=group, split="test", tier="T1", format="memo",
        generator="g", ambiguity_flag=False, gold_level=gold_level, gold_categories=sorted(gold_cats),
        gold_high_risk=gold_hr, status="ok" if pred_level else "error", has_prediction=pred_level is not None,
        pred_level=pred_level, pred_categories=sorted(pred_cats), pred_high_risk=pred_hr, latency_ms=1.0,
    )  # fmt: skip
    base.update(kw)
    return PredictionRecord(**base)


def only_explicit_locked_runs(data_dir) -> None:
    """The locked-test access log may hold entries, but only from explicitly authorised runs
    (`--allow-locked-test`): the review tooling has no path that writes one."""
    import json
    from pathlib import Path

    for line in Path(data_dir, "locked_test_access.jsonl").read_text().splitlines():
        entry = json.loads(line)
        assert entry["authorization"]["mechanism"] == "--allow-locked-test"
        assert entry["run_id"].startswith(("hybrid-test-", "rules-test-", "ml-test-", "llm-test-"))
