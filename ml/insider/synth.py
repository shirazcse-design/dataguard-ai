"""Deterministic synthetic population and daily activity for UC2 (`dataguard-insider data build`).

* 60 name-free users (`u-2001`...) in 7 role families. Users carry role, department, privilege and
  normal working hours only: no names, demographics, region, employment status or HR data.
* 90 days of daily activity. Days 1-60 are the HISTORICAL period and contain normal behaviour only.
  Days 61-90 are the HOLDOUT: normal behaviour plus hard legitimate patterns plus injected anomalies.
* Scenario labels are written to `eval_labels.csv`, a file the model never reads. Training is
  unsupervised; labels exist only to evaluate the detector afterwards.

Everything is seeded: the same config produces byte-identical files (tested).
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "insider"
FEATURES = ("files_downloaded", "sensitive_files_accessed", "after_hours_activity",
            "external_upload_volume_mb", "new_repository_access", "failed_access_attempts",
            "distinct_repositories")  # fmt: skip


@dataclass(frozen=True)
class Family:
    role: str
    department: str
    downloads: float  # mean files/day on a weekday
    sensitive_frac: float
    after_hours: float  # mean after-hours events/day
    upload_prob: float  # chance of any external upload on a day
    upload_mu: float  # log-mean of an upload's MB
    new_repo: float
    failed: float
    repos: float  # mean distinct repositories/day
    repo_prefix: str
    known_repos: int


FAMILIES: dict[str, Family] = {
    "engineering": Family("Software Engineer", "Engineering", 25, .18, .4, .03, 1.5, .10, .25, 4, "eng", 8),
    "finance": Family("Financial Analyst", "Finance", 15, .25, .3, .05, 1.5, .03, .15, 3, "fin", 5),
    "sales": Family("Account Executive", "Sales", 12, .12, .4, .18, 2.0, .03, .20, 3, "sales", 5),
    "research": Family("Research Scientist", "R&D", 20, .20, .5, .05, 2.0, .06, .20, 4, "rnd", 7),
    "product": Family("Product Manager", "Product", 15, .20, .2, .04, 1.5, .03, .15, 3, "prod", 6),
    "it_operations": Family("Systems Administrator", "IT Operations", 30, .10, 1.5, .05, 2.0, .08, .40, 6, "ops", 12),
    "people_operations": Family("People Partner", "People Operations", 10, .30, .2, .02, 1.0, .02, .10, 2, "people", 4),
}  # fmt: skip

# Holdout scenarios, all on weekdays (day 1 = Monday 2026-06-01). `kind`: "anomaly" (injected; evaluation label positive) or "legitimate" (hard
# legitimate pattern; evaluation label negative). `who` = (family, index within the family).
# `set` overrides a feature with an absolute value; `mul` multiplies the generated value; `add` adds.
SCENARIOS: list[dict[str, Any]] = [
    {"id": "S1-flagship", "kind": "anomaly", "who": ("product", 0), "days": [86],
     "set": {"files_downloaded": 262, "sensitive_files_accessed": 78, "after_hours_activity": 34,
             "external_upload_volume_mb": 2400.0, "new_repository_access": 3,
             "failed_access_attempts": 4, "distinct_repositories": 9}},
    {"id": "S1-precursor", "kind": "anomaly", "who": ("product", 0), "days": [85],
     "set": {"new_repository_access": 1, "after_hours_activity": 3, "files_downloaded": 31}},
    {"id": "S2-slow-drip", "kind": "anomaly", "who": ("finance", 1), "days": [71, 72, 73, 74, 75, 78, 79, 80, 81, 82],
     "mul": {"files_downloaded": 2.0, "sensitive_files_accessed": 3.0},
     "add": {"external_upload_volume_mb": 90.0, "after_hours_activity": 1}},
    {"id": "S3-source-code-grab", "kind": "anomaly", "who": ("engineering", 2), "days": [74],
     "mul": {"files_downloaded": 6.0, "sensitive_files_accessed": 5.0, "distinct_repositories": 2.0},
     "add": {"new_repository_access": 2}},
    {"id": "S4-repo-exploration", "kind": "anomaly", "who": ("research", 1), "days": [81],
     "set": {"new_repository_access": 6, "failed_access_attempts": 8, "distinct_repositories": 12}},
    {"id": "S5-credential-probing", "kind": "anomaly", "who": ("sales", 1), "days": [73],
     "set": {"failed_access_attempts": 25}, "add": {"after_hours_activity": 6}},
    {"id": "S6-after-hours-bulk", "kind": "anomaly", "who": ("research", 2), "days": [87],
     "mul": {"files_downloaded": 4.0}, "set": {"after_hours_activity": 20, "external_upload_volume_mb": 350.0}},
    {"id": "S7-subtle", "kind": "anomaly", "who": ("engineering", 4), "days": [66],
     "add": {"new_repository_access": 1, "external_upload_volume_mb": 25.0}, "mul": {"sensitive_files_accessed": 2.0}},
    {"id": "S8-upload-spike", "kind": "anomaly", "who": ("sales", 2), "days": [78],
     "mul": {"files_downloaded": 3.0, "sensitive_files_accessed": 4.0}, "set": {"external_upload_volume_mb": 800.0}},
    {"id": "H1-month-end-close", "kind": "legitimate", "who": ("finance", "*"), "days": [88, 89],
     "mul": {"files_downloaded": 2.5, "sensitive_files_accessed": 2.0}, "add": {"after_hours_activity": 5}},
    {"id": "H2-release-week", "kind": "legitimate", "who": ("engineering", "0-5"), "days": [64, 65, 66, 67, 68],
     "mul": {"files_downloaded": 1.6}, "add": {"after_hours_activity": 3}},
    {"id": "H3-approved-migration", "kind": "legitimate", "who": ("it_operations", 1), "days": [80],
     "set": {"files_downloaded": 400, "sensitive_files_accessed": 120, "distinct_repositories": 15,
             "after_hours_activity": 6}},
    {"id": "H4-travel-after-hours", "kind": "legitimate", "who": ("sales", 3), "days": [71, 72, 73],
     "add": {"after_hours_activity": 8}},
    {"id": "H5-project-onboarding", "kind": "legitimate", "who": ("engineering", 7), "days": [71],
     "add": {"new_repository_access": 4, "distinct_repositories": 4}},
]  # fmt: skip


# What happened on scenario days that the daily counts cannot say: where an upload went and which
# repositories were new. Descriptive data for the log service, NOT labels (no "anomaly" field).
EVENT_DETAILS: dict[str, dict[str, Any]] = {
    "u-2043@85": {"new_repositories": ["src-core-platform"]},
    "u-2043@86": {
        "upload": {"host": "dropbox.com", "account_type": "personal"},
        "new_repositories": ["src-core-platform", "cust-data-exports", "fin-billing-ledger"],
    },
    "u-2016@*": {"upload": {"host": "drive.google.com", "account_type": "personal"}},
    "u-2003@74": {"new_repositories": ["eng-core-auth", "eng-payments-secrets"]},
    "u-2036@81": {
        "new_repositories": [
            "rnd-genomics-raw",
            "rnd-patent-drafts",
            "rnd-vendor-models",
            "rnd-lab-notebooks",
            "rnd-sensor-firmware",
            "rnd-archive-2024",
        ]
    },
    "u-2037@87": {"upload": {"host": "wetransfer.com", "account_type": "personal"}},
    "u-2005@66": {
        "upload": {"host": "gist.github.com", "account_type": "personal"},
        "new_repositories": ["eng-infra-terraform"],
    },
    "u-2027@78": {"upload": {"host": "drive.google.com", "account_type": "personal"}},
    "u-2008@71": {"new_repositories": ["eng-repo-09", "eng-repo-10", "eng-repo-11", "eng-repo-12"]},
    "sales@*": {
        "upload": {"host": "portal.fernbrook-logistics.example", "account_type": "external"}
    },
    "*": {"upload": {"host": "portal.fernbrook-logistics.example", "account_type": "external"}},
}


def event_details(user: dict[str, Any], day: int) -> dict[str, Any]:
    """Upload destination and new-repository names for a user-day (most specific entry wins)."""
    out: dict[str, Any] = {}
    for key in (
        "*",
        f"{user['role_family']}@*",
        f"{user['user_id']}@*",
        f"{user['user_id']}@{day}",
    ):
        out.update(EVENT_DETAILS.get(key, {}))
    return out


def load_config() -> dict[str, Any]:
    import yaml

    return yaml.safe_load((REPO / "config" / "insider" / "insider.v1.yaml").read_text("utf-8"))


def _users(cfg: dict[str, Any], rng: np.random.Generator) -> list[dict[str, Any]]:
    users, n = [], 2001
    for fam, count in cfg["dataset"]["users_per_family"].items():
        f = FAMILIES[fam]
        for i in range(count):
            privileged = fam == "it_operations" or (
                fam in ("engineering", "finance") and i == count - 1
            )
            known = [f"{f.repo_prefix}-repo-{k:02d}" for k in range(1, f.known_repos + 1)]
            users.append({
                "user_id": f"u-{n}", "role_family": fam, "role": f.role, "department": f.department,
                "privilege_level": "privileged" if privileged else "standard",
                "work_hours": [int(rng.choice([8, 9, 10])), 0], "known_repositories": known,
                "family_index": i,
            })  # fmt: skip
            users[-1]["work_hours"][1] = users[-1]["work_hours"][0] + 9
            n += 1
    return users


def _day(f: Family, u: dict, mult: float, sens: float, weekend: bool, rng) -> dict[str, float]:
    scale = 0.1 if weekend else 1.0
    files = int(rng.poisson(f.downloads * mult * scale))
    up = (
        round(float(rng.lognormal(f.upload_mu, 0.7)), 1)
        if rng.random() < f.upload_prob * scale
        else 0.0
    )
    new_repo = int(rng.poisson(f.new_repo * scale))
    distinct = (
        0 if files == 0 else max(1, min(len(u["known_repositories"]), int(rng.poisson(f.repos))))
    )
    return {
        "files_downloaded": files,
        "sensitive_files_accessed": int(rng.binomial(files, min(1.0, sens))) if files else 0,
        "after_hours_activity": int(rng.poisson(f.after_hours * (0.5 if weekend else 1.0))),
        "external_upload_volume_mb": up,
        "new_repository_access": new_repo,
        "failed_access_attempts": int(rng.poisson(f.failed * scale)),
        "distinct_repositories": distinct + new_repo,
    }


def _targets(users: list[dict], who: tuple) -> list[dict]:
    fam, idx = who
    pool = [u for u in users if u["role_family"] == fam]
    if idx == "*":
        return pool
    if isinstance(idx, str) and "-" in idx:
        lo, hi = (int(x) for x in idx.split("-"))
        return pool[lo : hi + 1]
    return [pool[idx]]


def build(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return {users, activity rows, label rows, permissions}. Pure: no files written."""
    cfg = cfg or load_config()
    ds = cfg["dataset"]
    rng = np.random.default_rng(ds["seed"])
    users = _users(cfg, rng)
    start = date.fromisoformat(ds["start_date"])
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    for u in users:
        f = FAMILIES[u["role_family"]]
        # The flagship user is pinned at the family mean so the demo baseline reads cleanly.
        flagship = u["role_family"] == "product" and u["family_index"] == 0
        mult = 1.0 if flagship else float(rng.lognormal(0.0, 0.3))
        sens = f.sensitive_frac * (1.0 if flagship else float(rng.uniform(0.7, 1.3)))
        for d in range(1, ds["days"] + 1):
            day = start + timedelta(days=d - 1)
            values = _day(f, u, mult, sens, day.weekday() >= 5, rng)
            rows[(u["user_id"], d)] = {"user_id": u["user_id"], "day": d, "date": day.isoformat(),
                                       "period": "train" if d <= ds["train_days"] else "holdout",
                                       **values}  # fmt: skip
    labels: dict[tuple[str, int], dict[str, Any]] = {}
    for sc in SCENARIOS:
        assert all(d > ds["train_days"] for d in sc["days"]), "scenarios live in the holdout only"
        for u in _targets(users, sc["who"]):
            for d in sc["days"]:
                r = rows[(u["user_id"], d)]
                for k, v in sc.get("mul", {}).items():
                    r[k] = (
                        round(r[k] * v, 1)
                        if k == "external_upload_volume_mb"
                        else int(round(r[k] * v))
                    )
                for k, v in sc.get("add", {}).items():
                    r[k] = round(r[k] + v, 1) if k == "external_upload_volume_mb" else int(r[k] + v)
                for k, v in sc.get("set", {}).items():
                    r[k] = v
                r["sensitive_files_accessed"] = min(
                    r["sensitive_files_accessed"], r["files_downloaded"]
                )
                prev = labels.get((u["user_id"], d))
                if prev is None or sc["kind"] == "anomaly":
                    labels[(u["user_id"], d)] = {"user_id": u["user_id"], "day": d, "date": r["date"],
                                                 "scenario": sc["id"], "kind": sc["kind"]}  # fmt: skip
    permissions = {
        u["user_id"]: {
            "repositories": [{"name": n, "access": "read-write" if u["role_family"] == "engineering" else "read"}
                             for n in u["known_repositories"]],
            "expected_data_classes": _expected(u["role_family"]),
        }
        for u in users
    }  # fmt: skip
    return {"users": users, "activity": [rows[k] for k in sorted(rows, key=lambda k: (k[1], k[0]))],
            "labels": sorted(labels.values(), key=lambda r: (r["day"], r["user_id"])),
            "permissions": permissions}  # fmt: skip


def _expected(family: str) -> list[str]:
    return {
        "engineering": ["SOURCE_CODE", "INTERNAL"], "finance": ["FINANCIAL_PCI", "CONFIDENTIAL"],
        "sales": ["CUSTOMER_CONTACTS", "INTERNAL"], "research": ["INTELLECTUAL_PROPERTY", "CONFIDENTIAL"],
        "product": ["INTERNAL", "CONFIDENTIAL"], "it_operations": ["CREDENTIALS_SECRETS", "SOURCE_CODE", "INTERNAL"],
        "people_operations": ["PII", "CONFIDENTIAL"],
    }[family]  # fmt: skip


def _csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write(out: Path = DATA, cfg: dict[str, Any] | None = None) -> dict[str, int]:
    built = build(cfg)
    out.mkdir(parents=True, exist_ok=True)
    users = [{k: v for k, v in u.items() if k != "family_index"} for u in built["users"]]
    (out / "users.json").write_text(json.dumps(users, indent=1) + "\n", encoding="utf-8")
    (out / "permissions.json").write_text(
        json.dumps(built["permissions"], indent=1) + "\n", "utf-8"
    )
    (out / "activity.csv").write_text(_csv(built["activity"]), encoding="utf-8")
    (out / "eval_labels.csv").write_text(_csv(built["labels"]), encoding="utf-8")
    return {
        "users": len(users),
        "activity_rows": len(built["activity"]),
        "label_rows": len(built["labels"]),
    }


def load_activity(path: Path = DATA / "activity.csv") -> list[dict[str, Any]]:
    out = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row: dict[str, Any] = {"user_id": r["user_id"], "day": int(r["day"]), "date": r["date"],
                                   "period": r["period"]}  # fmt: skip
            for k in FEATURES:
                row[k] = float(r[k])
            out.append(row)
    return out


def load_users(path: Path = DATA / "users.json") -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))
