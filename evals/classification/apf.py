"""Agent Performance Framework and HHH (PRD sections 14-15), SCOPED for UC4.

UC4 is a read/compute classification service with no tools and no autonomous actions (decisions
A6, A19), so the PRD's tool-selection, agent-loop and destructive-action sub-measures do not apply
here and are dropped rather than force-fitted (see `docs/uc4/responsible-ai.md`). Every remaining
sub-score is computed from numbers the harness and the observability summary already produce
honestly; nothing here is a new measurement, only a documented, versioned way of combining them.
Every score also carries the raw inputs it was computed from, so a reader can check the arithmetic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from app.classification.config_loader import ConfigError, default_config_dir, read_yaml
from app.classification.schemas.common import SEMVER_RE, StrictModel

APF_FILE = "eval/apf.v1.yaml"


class APFWeights(StrictModel):
    effectiveness: float = Field(ge=0, le=1)
    efficiency: float = Field(ge=0, le=1)
    reliability: float = Field(ge=0, le=1)
    trustworthiness: float = Field(ge=0, le=1)


class APFConfig(StrictModel):
    apf_config_version: str
    weights: APFWeights
    efficiency_latency_budget_s: float = Field(gt=0)

    def _semver(self) -> None:
        if not SEMVER_RE.match(self.apf_config_version):
            raise ValueError("apf_config_version must be a semantic version")


def load_apf_config(config_dir: Path | str | None = None) -> tuple[APFConfig, str]:
    base = Path(config_dir) if config_dir is not None else default_config_dir()
    path = base / APF_FILE
    data, digest = read_yaml(path)
    try:
        cfg = APFConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"{path}: schema validation failed:\n{exc}") from exc
    cfg._semver()
    total = sum(cfg.weights.model_dump().values())
    if abs(total - 1.0) > 1e-6:
        raise ConfigError(f"{path}: weights must sum to 1.0, got {total}")
    return cfg, digest


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _rate(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else None


# ---------------------------------------------------------------------------------------------
# HHH (section 14), scoped: no tool-use / destructive-action questions, since UC4 has neither.
# ---------------------------------------------------------------------------------------------
def compute_hhh(
    metrics: dict[str, Any],
    obs_summary: dict[str, Any] | None = None,
    *,
    content_safety_pass_rate: float | None = None,
) -> dict[str, Any]:
    """`metrics` is `build_metrics(...)['headline']` (the harness's own output). `obs_summary` is
    `observability.summarize(spans)`, optional. `content_safety_pass_rate` is an external judge
    signal (Azure AI Content Safety on LLM rationale text); None if that check was not run.
    """
    coverage = metrics["metrics"]["coverage"]
    helpful = _rate(coverage["n_with_prediction"], coverage["n_docs"])

    honest_inputs: dict[str, Any] = {}
    honest = None
    if obs_summary is not None:
        fail_rate = obs_summary.get("evidence_verification_failure_rate")
        honest_inputs["evidence_verification_failure_rate"] = fail_rate
        honest = None if fail_rate is None else _clamp01(1 - fail_rate)

    high_risk = metrics["metrics"]["high_risk"]
    harmless_inputs = {"high_risk_recall": high_risk["recall"]}
    # Harmless, scoped to UC4's actual risk: the harm this service can cause is under-classifying a
    # sensitive document as safe, not an autonomous destructive action (it takes none). High-risk
    # recall - a document that IS high-risk being correctly flagged - is that safety property.
    harmless = high_risk["recall"]
    if content_safety_pass_rate is not None:
        harmless_inputs["content_safety_pass_rate"] = content_safety_pass_rate
        harmless = _mean([harmless, content_safety_pass_rate])

    return {
        "scope_note": (
            "HHH scoped for a non-agentic classifier: tool-use and destructive-action questions "
            "from the PRD's agent rubric do not apply (no tools, no autonomous actions) and were "
            "dropped rather than force-fitted."
        ),
        "helpful": {
            "score": helpful,
            "formula": "documents with a usable prediction / headline documents",
            "inputs": {
                "n_docs": coverage["n_docs"],
                "n_with_prediction": coverage["n_with_prediction"],
            },
        },
        "honest": {
            "score": honest,
            "formula": "1 - evidence_verification_failure_rate (None if no trace file supplied)",
            "inputs": honest_inputs,
        },
        "harmless": {
            "score": harmless,
            "formula": "high_risk_recall, optionally averaged with an external content-safety "
            "pass rate on LLM rationale text (None if not run)",
            "inputs": harmless_inputs,
        },
    }


# ---------------------------------------------------------------------------------------------
# APF (section 15), scoped
# ---------------------------------------------------------------------------------------------
def compute_apf(
    metrics: dict[str, Any],
    obs_summary: dict[str, Any] | None,
    cfg: APFConfig,
    *,
    hhh: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`metrics` and `obs_summary` as in `compute_hhh`. `hhh` is `compute_hhh(...)`'s own output,
    reused here for Trustworthiness's Honest component rather than recomputed."""
    hm = metrics["metrics"]

    effectiveness_inputs = {
        "level_macro_f1": hm["level"]["macro"]["f1"],
        "category_macro_f1": hm["categories"]["macro"]["f1"],
        "high_risk_recall": hm["high_risk"]["recall"],
    }
    effectiveness = _mean(list(effectiveness_inputs.values()))

    efficiency_inputs: dict[str, Any] = {"latency_budget_s": cfg.efficiency_latency_budget_s}
    efficiency = None
    if obs_summary is not None:
        stage = obs_summary.get("stage_latency", {})
        p95_ms = max((s.get("p95_ms") or 0) for s in stage.values()) if stage else None
        efficiency_inputs["worst_stage_p95_ms"] = p95_ms
        if p95_ms is not None:
            efficiency = _clamp01(1 - (p95_ms / 1000.0) / cfg.efficiency_latency_budget_s)

    reliability_inputs: dict[str, Any] = {}
    reliability = None
    if obs_summary is not None:
        review_rate = obs_summary.get("review_rate") or 0.0
        schema_failure_rate = obs_summary.get("schema_failure_rate") or 0.0
        n_traces = obs_summary.get("n_traces") or 0
        failed_spans = (obs_summary.get("fallbacks") or {}).get("failed_stage_spans", 0)
        stage_failure_rate = _rate(failed_spans, n_traces) or 0.0
        reliability_inputs = {
            "review_rate": review_rate,
            "schema_failure_rate": schema_failure_rate,
            "failed_stage_rate": stage_failure_rate,
        }
        reliability = _clamp01(1 - _mean([review_rate, schema_failure_rate, stage_failure_rate]))

    honest_score = (hhh or {}).get("honest", {}).get("score")
    trustworthiness_inputs = {"evidence_verification_rate (Honest)": honest_score}
    trustworthiness = honest_score

    dims = {
        "effectiveness": (effectiveness, effectiveness_inputs),
        "efficiency": (efficiency, efficiency_inputs),
        "reliability": (reliability, reliability_inputs),
        "trustworthiness": (trustworthiness, trustworthiness_inputs),
    }
    weights = cfg.weights.model_dump()
    present = {k: v for k, (v, _) in dims.items() if v is not None}
    if present:
        used_weight = sum(weights[k] for k in present)
        composite = sum(weights[k] * v for k, v in present.items()) / used_weight
    else:
        composite = None

    return {
        "apf_config_version": cfg.apf_config_version,
        "scope_note": (
            "APF scoped for a non-agentic classifier: no tool-selection or agent-loop sub-measures "
            "(there is no tool use or multi-step loop). Weights are the PRD's own default "
            "composite (section 15), a product-risk choice, not measured."
        ),
        "weights": weights,
        "composite": composite,
        "composite_note": None
        if len(present) == len(dims)
        else f"computed from {sorted(present)} only; missing dimensions were excluded and the "
        "remaining weights renormalised (not treated as zero)",
        "dimensions": {
            k: {"score": v, "weight": weights[k], "inputs": inputs}
            for k, (v, inputs) in dims.items()
        },
    }
