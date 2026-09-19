# Phase 0 - Foundations

## What was built

| Area | Location |
|---|---|
| Packaging (`pyproject.toml`, Python >= 3.11, `dataguard-uc4` CLI) | repo root |
| Taxonomy (two-axis) with level floors | `config/taxonomy/taxonomy.v1.yaml` |
| High-risk definition (configurable) | `config/taxonomy/high_risk.v1.yaml` |
| Evaluation settings | `config/eval/eval.v1.yaml` |
| Config schemas | `app/classification/schemas/config_models.py` |
| Confidence schema (the contract) | `app/classification/schemas/confidence.py` |
| Evidence schema | `app/classification/schemas/evidence.py` |
| Request / result schemas | `app/classification/schemas/request.py`, `result.py` |
| Config loader (strict, cross-checked) | `app/classification/config_loader.py` |
| Level-floor + derived high-risk logic | `app/classification/policy.py` |
| `Classifier` interface | `app/classification/interfaces.py` |
| CI skeleton | `.github/workflows/ci.yml` |
| Tests | `tests/unit/` |

Directories for later phases (`rules/`, `app/llm/`, `guardrails/`, `observability/`, `prompts/`)
exist as documented placeholders and contain no logic.

## Key design points

* **Taxonomy is configuration, not code.** Level and category ids are strings validated against the
  loaded taxonomy, so the taxonomy can evolve without code changes (approved: definitions live in
  version-controlled config, no RAG).
* **Level floors are configurable synthetic-policy defaults**, not universal security requirements
  (approved). The taxonomy file carries a notice saying so, and a test asserts the notice exists.
* **`high_risk` is derived, never predicted or hard-coded.** `TaxonomyPolicy.derive_high_risk` reads
  `high_risk.v1.yaml`. A test swaps in a different definition and shows the derivation changes with
  no code change.
* **The confidence contract is enforced by validators.** A verbalized (LLM) bucket can never be
  marked calibrated; a `calibrated_probability` requires a `calibration_ref`; `est_reliability`
  requires the reference that measured it.
* **Evidence provenance must match evidence type** (observed vs. inferred), encoding the PRD's
  grounding contract (section 8.4).
* **Results are values, not exceptions.** `ClassificationResult` always validates with a `status`;
  `ok`/`degraded` require a label and derived high-risk, and `review_required` requires a review
  block with reason codes.
* **Fail-fast configuration.** Duplicate YAML keys, unknown fields, unknown ids, floor references to
  missing levels, and taxonomy/high-risk version mismatches all raise `ConfigError`.

## How to run

```bash
pytest tests/unit
dataguard-uc4 config validate
```
