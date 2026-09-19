"""Group-aware split assignment."""

from __future__ import annotations

from collections import defaultdict

import pytest

from evals.classification.dataset.schema import SPLIT_NAMES
from evals.classification.dataset.spec import DEFAULT_SPEC_DIR, FamilySpec, load_spec
from evals.classification.dataset.splits import assign_splits


@pytest.fixture(scope="module")
def dataset_spec():
    return load_spec().dataset


def _fam(fid, tier="T1", level="INTERNAL", cats=(), n=6, group=None):
    return FamilySpec(
        family_id=fid, description="d", tier=tier, format="memo", gold_level=level,
        gold_categories=list(cats), n_docs=n, filenames=["a.txt"], bodies=["b"], group_id=group,
    )  # fmt: skip


def _families():
    fams = []
    levels = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "HIGHLY_CONFIDENTIAL"]
    cats = [None, "PII", "PHI", "SOURCE_CODE", "TRADE_SECRET", "MA_CORP_STRATEGY"]
    for i in range(80):
        cat = cats[i % len(cats)]
        level = (
            "HIGHLY_CONFIDENTIAL"
            if cat in {"PHI", "TRADE_SECRET", "MA_CORP_STRATEGY"}
            else levels[i % 3]
        )
        fams.append(
            _fam(
                f"fam_{i:03d}",
                tier=f"T{1 + i % 5}",
                level=level,
                cats=[cat] if cat else [],
                n=5 + i % 4,
            )
        )
    return fams


def test_assignment_is_deterministic(dataset_spec):
    a = assign_splits(_families(), dataset_spec)
    b = assign_splits(_families(), dataset_spec)
    assert a.group_to_split == b.group_to_split and a.objective == b.objective


def test_every_group_assigned_to_exactly_one_known_split(dataset_spec):
    fams = _families()
    result = assign_splits(fams, dataset_spec)
    assert set(result.group_to_split) == {f.group for f in fams}
    assert set(result.group_to_split.values()) <= set(SPLIT_NAMES)


def test_families_sharing_a_group_stay_together(dataset_spec):
    fams = _families()
    fams[0] = _fam("fam_000", group="shared")
    fams[1] = _fam("fam_001", group="shared")
    fams[2] = _fam("fam_002", group="shared")
    result = assign_splits(fams, dataset_spec)
    assert "shared" in result.group_to_split and "fam_001" not in result.group_to_split


def test_split_sizes_track_targets(dataset_spec):
    fams = _families()
    result = assign_splits(fams, dataset_spec)
    sizes = defaultdict(int)
    for f in fams:
        sizes[result.group_to_split[f.group]] += f.n_docs
    total = sum(sizes.values())
    for split, frac in dataset_spec.split_fractions.as_dict().items():
        assert abs(sizes[split] - frac * total) <= 0.12 * total, (split, dict(sizes))


def test_minimum_positive_pressure_reaches_the_test_split(dataset_spec):
    """Every category present in the pool ends up with positives in the test split."""
    fams = _families()
    result = assign_splits(fams, dataset_spec)
    test_cats = {
        c for f in fams if result.group_to_split[f.group] == "test" for c in f.gold_categories
    }
    assert {"PII", "PHI", "SOURCE_CODE", "TRADE_SECRET", "MA_CORP_STRATEGY"} <= test_cats


def test_spec_dir_constant_points_at_repo_data():
    assert (DEFAULT_SPEC_DIR / "dataset_spec.yaml").exists()
