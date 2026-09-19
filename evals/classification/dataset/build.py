"""Build, write, load and verify the UC4 synthetic dataset."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.classification.config_loader import HIGH_RISK_FILE, TAXONOMY_FILE, ConfigBundle
from app.classification.schemas.common import sha256_text

from .generator import generate_dataset
from .integrity import IntegrityReport, check_dataset
from .schema import SPLIT_NAMES, DatasetDocument
from .spec import LoadedSpec, load_spec
from .stats import compute_stats

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "synthetic" / "uc4"


class DatasetIntegrityError(Exception):
    """Raised when dataset files do not match their manifest (tampering / stale data)."""


@dataclass
class BuildResult:
    docs: list[DatasetDocument]
    group_to_split: dict[str, str]
    report: IntegrityReport
    manifest: dict[str, Any]
    files: dict[str, str]  # relative path -> file text (deterministic serialisation)


def injection_snippets(spec: LoadedSpec) -> list[str]:
    """All items of pools whose name starts with `inject_` (the adversarial text fragments)."""
    return [s for name, items in spec.pools.items() if name.startswith("inject_") for s in items]


def serialise_docs(docs: list[DatasetDocument]) -> str:
    lines = [json.dumps(d.model_dump(), ensure_ascii=False, sort_keys=True) for d in docs]
    return "\n".join(lines) + "\n"


def build_dataset(config: ConfigBundle, spec_dir: Path | str | None = None) -> BuildResult:
    spec = load_spec(spec_dir)
    policy = config.policy
    if spec.dataset.taxonomy_version != policy.taxonomy_version:
        raise ValueError(
            f"dataset spec targets taxonomy {spec.dataset.taxonomy_version} but config is "
            f"{policy.taxonomy_version}"
        )
    docs, group_to_split = generate_dataset(spec)
    vocabulary = {item for items in spec.pools.values() for item in items}
    report = check_dataset(docs, spec.dataset, policy, injection_snippets(spec), vocabulary)

    files: dict[str, str] = {}
    file_meta: dict[str, Any] = {}
    for split in SPLIT_NAMES:
        split_docs = [d for d in docs if d.split == split]
        rel = f"docs/{split}.jsonl"
        text = serialise_docs(split_docs)
        files[rel] = text
        file_meta[split] = {
            "path": rel,
            "sha256": sha256_text(text),
            "n_docs": len(split_docs),
        }
    dataset_sha = hashlib.sha256(
        "".join(file_meta[s]["sha256"] for s in SPLIT_NAMES).encode()
    ).hexdigest()

    families = sorted(
        (
            {
                "family_id": f.family_id,
                "group_id": f.group,
                "tier": f.tier,
                "split": group_to_split[f.group],
                "n_docs": f.n_docs,
            }
            for f in spec.families
        ),
        key=lambda x: x["family_id"],
    )
    ann_status: dict[str, int] = {}
    for d in docs:
        ann_status[d.annotation_status] = ann_status.get(d.annotation_status, 0) + 1
    manifest: dict[str, Any] = {
        "dataset_id": spec.dataset.dataset_id,
        "dataset_version": spec.dataset.dataset_version,
        "generator_name": spec.dataset.generator_name,
        "generator_version": spec.dataset.generator_version,
        "seed": spec.dataset.seed,
        "taxonomy_version": policy.taxonomy_version,
        "high_risk_version": policy.high_risk_version,
        # Only the configuration the dataset depends on. The evaluation config is deliberately
        # excluded: changing evaluation settings must not invalidate the dataset.
        "config_file_hashes": {
            k: v for k, v in config.file_hashes.items() if k in (TAXONOMY_FILE, HIGH_RISK_FILE)
        },
        "spec_hash": spec.spec_hash,
        "dataset_sha256": dataset_sha,
        "n_documents": len(docs),
        "annotation_status": ann_status,
        "files": file_meta,
        "stats": compute_stats(docs, policy),
        "families": families,
        "integrity": report.to_dict(),
    }
    files["manifest.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return BuildResult(docs, group_to_split, report, manifest, files)


def write_dataset(result: BuildResult, out_dir: Path | str | None = None) -> Path:
    base = Path(out_dir) if out_dir is not None else DEFAULT_DATA_DIR
    for rel, text in result.files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return base


def load_manifest(data_dir: Path | str | None = None) -> dict[str, Any]:
    base = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    return json.loads((base / "manifest.json").read_text(encoding="utf-8"))


def load_documents(
    data_dir: Path | str | None = None, splits: list[str] | None = None, verify: bool = True
) -> list[DatasetDocument]:
    """Load documents for `splits` (default: all), verifying file hashes against the manifest."""
    base = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    manifest = load_manifest(base)
    docs: list[DatasetDocument] = []
    for split in splits or list(SPLIT_NAMES):
        meta = manifest["files"][split]
        text = (base / meta["path"]).read_text(encoding="utf-8")
        if verify and sha256_text(text) != meta["sha256"]:
            raise DatasetIntegrityError(f"{meta['path']} does not match manifest hash")
        for line in text.splitlines():
            docs.append(DatasetDocument.model_validate_json(line))
        if verify and sum(d.split == split for d in docs) != meta["n_docs"]:
            raise DatasetIntegrityError(f"{meta['path']}: document count differs from manifest")
    return docs
