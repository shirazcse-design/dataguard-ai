"""Hard protection for the locked test split.

The test split is report-only: it must never be used to tune rules, thresholds, prompts or models.
Every official entry point (document loading, `evaluate()`, the CLI) therefore refuses to touch it
unless the caller supplies an explicit authorisation, and every authorised access is recorded in the
run manifest and in an append-only access log.

Limits, stated plainly: this stops *accidental* use through the project's own entry points. It
cannot stop someone who reads `data/synthetic/uc4/docs/test.jsonl` directly; that remains a matter
of process and code review (the access log and manifests make it visible).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

LOCKED_SPLIT = "test"
DEVELOPMENT_SPLITS: tuple[str, ...] = ("train", "calibration", "dev")
ALLOW_FLAG = "--allow-locked-test"

BANNER = (
    "=" * 72 + "\nLOCKED TEST SPLIT AUTHORISED\n"
    "This run reads the locked test split. Results are REPORT-ONLY: never use them to tune\n"
    "rules, thresholds, prompts or models. The access is recorded in the run manifest and in the\n"
    "locked-test access log.\n" + "=" * 72
)


class LockedTestSplitError(PermissionError):
    """Raised when the locked test split is requested without explicit authorisation."""


@dataclass(frozen=True)
class LockedTestAuthorization:
    """Explicit, recorded permission to read the locked test split."""

    mechanism: str = ALLOW_FLAG
    authorized_at: str = ""
    purpose: str = "report-only evaluation; no tuning"

    @classmethod
    def now(cls, mechanism: str = ALLOW_FLAG) -> LockedTestAuthorization:
        return cls(mechanism=mechanism, authorized_at=datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def requires_authorization(splits) -> bool:
    return LOCKED_SPLIT in set(splits)


def check_access(splits, authorization: LockedTestAuthorization | None) -> None:
    """Raise unless the locked split is absent from `splits` or access is authorised."""
    if requires_authorization(splits) and authorization is None:
        raise LockedTestSplitError(
            f"the '{LOCKED_SPLIT}' split is locked (report-only). Pass {ALLOW_FLAG} on the CLI, "
            "or a LockedTestAuthorization in code, to read it deliberately."
        )
