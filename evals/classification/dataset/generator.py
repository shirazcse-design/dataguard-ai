"""Deterministic synthetic-document generator.

Every document is a pure function of (dataset seed, family id, index), so generation is
reproducible and independent of iteration order.
"""

from __future__ import annotations

import hashlib

from app.classification.schemas import ExistingLabel
from app.classification.schemas.common import sha256_text

from .rng import DetRandom
from .schema import DatasetDocument, GoldEvidenceSpan
from .spec import FamilySpec, LoadedSpec
from .splits import assign_splits
from .template import TemplateError, render_many


def _doc_id(family_id: str, index: int) -> str:
    # Opaque on purpose: ids never encode family, tier or label.
    return "uc4-" + hashlib.sha256(f"{family_id}:{index}".encode()).hexdigest()[:10]


def _extension(filename: str) -> str:
    return filename.rsplit(".", 1)[1].lower() if "." in filename else ""


def generate_family_docs(
    fam: FamilySpec, spec: LoadedSpec, split: str, taxonomy_version: str
) -> list[DatasetDocument]:
    pools = {**spec.pools, **fam.pools}
    docs: list[DatasetDocument] = []
    for i in range(fam.n_docs):
        rng = DetRandom(spec.dataset.seed, "doc", fam.family_id, i)
        # Cycle through body variants so every variant is used; filenames are random.
        templates: dict[str, str] = {
            "filename": rng.choice(fam.filenames),
            "body": fam.bodies[i % len(fam.bodies)],
        }
        for key, tpl in fam.metadata.items():
            templates[f"meta:{key}"] = tpl
        for j, lab in enumerate(fam.existing_labels):
            templates[f"label:{j}"] = lab.value
        try:
            rendered = render_many(templates, rng, pools)
        except TemplateError as exc:
            raise TemplateError(f"family {fam.family_id} doc {i}: {exc}") from exc

        filename = rendered["filename"].text
        if rendered["filename"].spans:
            raise TemplateError(f"family {fam.family_id}: evidence markers not allowed in filename")
        body = rendered["body"]
        labels = [
            ExistingLabel(scheme=lab.scheme, value=rendered[f"label:{j}"].text)
            for j, lab in enumerate(fam.existing_labels)
            if rng.chance(lab.p)
        ]
        docs.append(
            DatasetDocument(
                doc_id=_doc_id(fam.family_id, i),
                split=split,
                tier=fam.tier,
                family_id=fam.family_id,
                group_id=fam.group,
                generator=spec.dataset.generator_name,
                format=fam.format,
                filename=filename,
                extension=_extension(filename),
                content=body.text,
                existing_labels=labels,
                metadata={k: rendered[f"meta:{k}"].text for k in fam.metadata},
                gold_level=fam.gold_level,
                gold_categories=list(fam.gold_categories),
                gold_evidence_spans=[
                    GoldEvidenceSpan(
                        label=s.label, char_start=s.char_start, char_end=s.char_end, text=s.text
                    )
                    for s in body.spans
                ],
                ambiguity_flag=fam.ambiguity,
                acceptable_alternative_levels=list(fam.acceptable_alternative_levels),
                decoy_for=list(fam.decoy_for),
                adversarial_type=fam.adversarial_type,
                annotation_notes=fam.notes.strip(),
                taxonomy_version=taxonomy_version,
                content_hash=sha256_text(body.text),
            )
        )
    return docs


def generate_dataset(spec: LoadedSpec) -> tuple[list[DatasetDocument], dict[str, str]]:
    """Generate all documents. Returns (documents, group_id -> split)."""
    assignment = assign_splits(spec.families, spec.dataset)
    docs: list[DatasetDocument] = []
    for fam in spec.families:
        split = assignment.group_to_split[fam.group]
        docs.extend(generate_family_docs(fam, spec, split, spec.dataset.taxonomy_version))
    docs.sort(key=lambda d: (d.split, d.doc_id))
    return docs, assignment.group_to_split
