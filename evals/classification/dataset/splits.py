"""Group-aware split assignment.

All documents of a scenario family (group) go to ONE split, so a classifier trained on `train`
never sees the template family of a `test` document. Assignment is a seeded, deterministic search
that balances split sizes, tier mix, level mix and per-category positives, and penalises label
counts below the minimum-positives thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .rng import DetRandom
from .schema import SPLIT_NAMES
from .spec import DatasetSpec, FamilySpec

_W_SIZE, _W_OTHER, _W_HINGE = 4.0, 1.0, 20.0


@dataclass(frozen=True)
class SplitAssignment:
    group_to_split: dict[str, str]
    objective: float
    restart: int


def _features(fam: FamilySpec) -> dict[str, int]:
    feats = {
        "size": fam.n_docs,
        f"tier:{fam.tier}": fam.n_docs,
        f"level:{fam.gold_level}": fam.n_docs,
    }
    for cat in fam.gold_categories:
        feats[f"cat:{cat}"] = fam.n_docs
    return feats


def _group_matrix(families: list[FamilySpec]) -> tuple[list[str], list[str], np.ndarray]:
    groups: dict[str, dict[str, int]] = {}
    for fam in families:
        agg = groups.setdefault(fam.group, {})
        for k, v in _features(fam).items():
            agg[k] = agg.get(k, 0) + v
    group_ids = sorted(groups)
    feat_names = sorted({k for g in groups.values() for k in g})
    mat = np.zeros((len(group_ids), len(feat_names)), dtype=float)
    for gi, gid in enumerate(group_ids):
        for k, v in groups[gid].items():
            mat[gi, feat_names.index(k)] = v
    return group_ids, feat_names, mat


def assign_splits(families: list[FamilySpec], spec: DatasetSpec) -> SplitAssignment:
    group_ids, feat_names, mat = _group_matrix(families)
    n_groups, n_splits = len(group_ids), len(SPLIT_NAMES)
    fractions = np.array([spec.split_fractions.as_dict()[s] for s in SPLIT_NAMES])
    totals = mat.sum(axis=0)
    targets = fractions[:, None] * totals[None, :]  # (splits, features)
    weights = np.array([_W_SIZE if f == "size" else _W_OTHER for f in feat_names])
    label_cols = [i for i, f in enumerate(feat_names) if f.startswith(("level:", "cat:"))]
    mins = np.array([spec.min_positives_per_label[s] for s in SPLIT_NAMES], dtype=float)

    def objective(assign: np.ndarray) -> float:
        one_hot = np.zeros((n_groups, n_splits))
        one_hot[np.arange(n_groups), assign] = 1.0
        values = one_hot.T @ mat  # (splits, features)
        loss = float((weights[None, :] * ((values - targets) / (targets + 5.0)) ** 2).sum())
        for s in range(n_splits):
            for c in label_cols:
                # A label can only be required up to what half of its total supports.
                required = min(mins[s], totals[c] * 0.5)
                if values[s, c] < required:
                    loss += _W_HINGE * ((required - values[s, c]) / max(required, 1.0)) ** 2
        return loss

    best: SplitAssignment | None = None
    sizes = mat[:, feat_names.index("size")]
    for restart in range(spec.split_search.restarts):
        rng = DetRandom(spec.seed, "splits", restart)
        order = list(range(n_groups))
        rng.shuffle(order)
        assign = np.zeros(n_groups, dtype=int)
        filled = np.zeros(n_splits)
        size_targets = fractions * sizes.sum()
        for gi in order:
            deficit = size_targets - filled
            s = int(np.argmax(deficit))
            assign[gi] = s
            filled[s] += sizes[gi]
        current = objective(assign)
        for _ in range(spec.split_search.iterations):
            if rng.chance(0.5):
                gi, new = rng.randint(0, n_groups - 1), rng.randint(0, n_splits - 1)
                if assign[gi] == new:
                    continue
                old = assign[gi]
                assign[gi] = new
                cand = objective(assign)
                if cand < current:
                    current = cand
                else:
                    assign[gi] = old
            else:
                a, b = rng.randint(0, n_groups - 1), rng.randint(0, n_groups - 1)
                if assign[a] == assign[b]:
                    continue
                assign[a], assign[b] = assign[b], assign[a]
                cand = objective(assign)
                if cand < current:
                    current = cand
                else:
                    assign[a], assign[b] = assign[b], assign[a]
        if best is None or current < best.objective:
            mapping = {group_ids[i]: SPLIT_NAMES[int(assign[i])] for i in range(n_groups)}
            best = SplitAssignment(mapping, current, restart)
    assert best is not None
    return best
