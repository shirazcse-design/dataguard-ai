"""Meta-test: every row of the failure matrix has tests, and no test is orphaned."""

from __future__ import annotations

import re
from pathlib import Path

from evals.classification.failure_matrix import ROWS

HERE = Path(__file__).parent


def _test_names() -> list[str]:
    names: list[str] = []
    for p in sorted(HERE.glob("test_*.py")):
        names += re.findall(r"^def (test_row_[A-Za-z0-9]+_\w+)\(", p.read_text(), flags=re.M)
    return names


def test_every_failure_row_has_at_least_one_test():
    have = {re.match(r"test_row_([A-Za-z0-9]+)_", n).group(1) for n in _test_names()}
    assert set(ROWS) - have == set(), f"failure rows without a test: {sorted(set(ROWS) - have)}"


def test_no_test_refers_to_a_row_that_is_not_in_the_matrix():
    have = {re.match(r"test_row_([A-Za-z0-9]+)_", n).group(1) for n in _test_names()}
    assert have - set(ROWS) == set()


def test_the_matrix_mirrors_the_architecture_failure_table():
    plan = (
        HERE.parents[1] / "docs/UC4_Technical_Architecture_and_Implementation_Plan.txt"
    ).read_text()
    section = plan[
        plan.index("## 19. Failure-handling strategy") : plan.index("## 20. Step-by-step")
    ]
    rows = [
        r
        for r in re.findall(r"^\| ([^|]+) \|", section, flags=re.M)
        if r.strip() not in ("Failure", "---")
    ]
    assert len(rows) == 10 and len([k for k in ROWS if k.startswith("F")]) == len(rows)
