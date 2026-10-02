"""UC1 core: config + the UC4->UC6 mapping contract, pre-checks (UC4 rules reuse), context and
behaviour bands, the exception register, and the deterministic risk/response harness."""

from __future__ import annotations

import pytest

from app.classification.schemas import Document
from app.dlp.config import LEVELS, load_dlp_configs
from app.dlp.context import ActivityStore, DeterministicBehavior, ExceptionRegister, IdentityStore
from app.dlp.harness import decide
from app.dlp.integration import apply_effects, effective_level, policy_question
from app.dlp.prechecks import classify_destination
from app.dlp.schemas import AgentResult, Behavior, Classification, DLPEvent, Identity, PolicyContext

DLP, MAP, RUBRIC, _ = load_dlp_configs()


def ev(**kw):
    base = dict(case_id="T1", timestamp="2026-09-15T10:00", user_id="u-1003", action="upload",
                destination={"host": "dropbox.com", "account_type": "personal"}, document_ref="uc1:acquisition_targets_2027")  # fmt: skip
    return DLPEvent(**{**base, **kw})


def test_mapping_is_explicit_and_complete():
    assert set(MAP.levels) == set(LEVELS)
    assert MAP.levels["HIGHLY_CONFIDENTIAL"].policy_term == "Restricted"  # the known platform gap
    assert MAP.categories["MA_CORP_STRATEGY"].min_level == "HIGHLY_CONFIDENTIAL"


def test_every_mapped_policy_section_exists_in_the_uc6_corpus():
    from app.policy.config import load_policy_config
    from app.policy.corpus import load_corpus

    cfg, _ = load_policy_config()
    keys = {c.section_key for c in load_corpus(cfg.corpus.dir, cfg.chunking).chunks}
    assert {e.section for e in MAP.policy_effects} <= keys


@pytest.mark.parametrize("host,acct,cls", [
    ("dropbox.com", "personal", "personal_cloud"), ("dropbox.com", "corporate", "approved_corporate"),
    ("onedrive.harbourline.example", "corporate", "approved_corporate"), ("gist.github.com", "personal", "restricted"),
    ("files.unknownshare.example", "external", "unknown_external"), ("gmail.com", "personal", "personal_email"),
])  # fmt: skip
def test_destination_classification(host, acct, cls):
    assert classify_destination(host, acct, DLP) == cls


def test_prechecks_reuse_the_uc4_rules_engine():
    from app.classification.config_loader import load_config
    from app.dlp.prechecks import run_prechecks
    from rules.config import load_rules_config
    from rules.engine import RulesEngine

    b = load_config()
    eng = RulesEngine(load_rules_config(b.policy)[0], b.policy)
    doc = Document(
        content="CONFIDENTIAL\nCustomer SSN 123-45-6789 for account review.",
        filename="a.txt",
        extension="txt",
    )
    pre = run_prechecks(ev(), doc, DLP, eng, ["onedrive.harbourline.example"])
    assert pre.destination_class == "personal_cloud" and pre.destination_known_to_user is False
    # UC4's marking detector fired; its dummy-value check suppressed the well-known fake SSN
    assert pre.pattern_level == "CONFIDENTIAL" and "pattern_level:CONFIDENTIAL" in pre.findings
    assert "6789" not in str(pre.model_dump())  # only ids/strengths kept, never the value


def test_behavior_bands_are_deterministic_and_named():
    b = DeterministicBehavior(DLP.behavior, ActivityStore(), IdentityStore())
    hi = b.assess("u-1003", ev(timestamp="2026-09-15T23:40"), None)
    assert hi.band == "UNUSUAL" and {"after_hours_activity", "new_destination"} <= set(hi.signals)
    lo = b.assess(
        "u-1001",
        ev(
            user_id="u-1001",
            destination={"host": "onedrive.harbourline.example", "account_type": "corporate"},
        ),
        None,
    )
    assert lo.band == "NORMAL" and lo.signals == []


def test_exception_register_matches_only_valid_records():
    r = ExceptionRegister()
    ranks = {k: v.rank for k, v in MAP.levels.items()}
    assert r.check("u-1004", "portal.fernbrook-logistics.example", 2, ranks, "2026-09-15T10:00")[
        "match"
    ]
    assert not r.check(
        "u-1004", "portal.fernbrook-logistics.example", 3, ranks, "2026-09-15T10:00"
    )["match"]  # level too high
    assert not r.check("u-1001", "gmail.com", 1, ranks, "2026-09-15T10:00")["match"]  # expired
    assert not r.check("u-1004", "dropbox.com", 1, ranks, "2026-09-15T10:00")["match"]  # other host


def test_policy_question_and_effects_are_deterministic():
    c = Classification(
        ok=True, status="ok", level="HIGHLY_CONFIDENTIAL", categories=["MA_CORP_STRATEGY"]
    )
    lvl = effective_level(c, None, MAP)
    q = policy_question(lvl, c.categories, "personal_cloud", DLP, MAP)
    assert (
        q
        == "Can Restricted information, such as merger and acquisition plans, be uploaded to a personal cloud-storage account?"
    )
    eff = apply_effects(["POL-DLP@2.1#4.2", "POL-AUP@4.0#5.3"], "personal_cloud", lvl, MAP)
    assert {e["effect"] for e in eff} == {"prohibited"}
    internal = apply_effects(
        ["POL-DLP@2.1#4.2", "POL-AUP@4.0#5.3"], "personal_cloud", "INTERNAL", MAP
    )
    assert {e["effect"] for e in internal} == {
        "prohibited",
        "allowed",
    }  # the AUP/DLP conflict, deterministically


# -- harness ---------------------------------------------------------------------------------
CLS_HC = Classification(
    ok=True,
    status="ok",
    level="HIGHLY_CONFIDENTIAL",
    categories=["MA_CORP_STRATEGY"],
    high_risk=True,
)
IDENT = Identity(user_id="u-1003", role="r", department="d", employment_type="employee", employment_status="active",
                 manager="m", privilege_level="standard", region="EMEA", business_unit="b")  # fmt: skip
NORMAL = Behavior(band="NORMAL", signals=[], features={}, provider="t")
PROHIB = PolicyContext(
    question="q", status="ANSWERED", effect="prohibited", cited_sections=["POL-DLP@2.1#4.2"]
)


def run(**kw):
    base = dict(rubric=RUBRIC, mapping=MAP, destination="personal_cloud", level="HIGHLY_CONFIDENTIAL",
                classification=CLS_HC, identity=IDENT, behavior=NORMAL, policy=PROHIB, agent=None,
                stage_failures=[], injection=False)  # fmt: skip
    return decide(**{**base, **kw})


def test_flagship_shape_escalates_with_a_simulated_action_needing_approval():
    d = run()
    assert d.outcome == "ESCALATE" and d.human_review_required and "simulated" in d.simulated_action
    assert any(r.startswith("policy_effect:prohibited") for r in d.reason_codes)


def test_exposure_gating_keeps_sensitive_data_in_corporate_systems_allowed():
    d = run(
        destination="approved_corporate",
        policy=PolicyContext(question="q", status="ANSWERED", effect="allowed"),
    )
    assert (
        d.outcome == "ALLOW"
        and "gated:no_destination_exposure" in d.reason_codes
        and d.simulated_action is None
    )


@pytest.mark.parametrize("kw,trigger", [
    (dict(classification=Classification(ok=False, status="review_required"), level=None), "classification_uncertain"),
    (dict(policy=PolicyContext(question="q", status="CONFLICT_REVIEW", conflict=True)), "policy_conflict"),
    (dict(policy=PolicyContext(question="q", status="UNAVAILABLE")), "policy_insufficient_high_impact"),
    (dict(stage_failures=["identity"]), "stage_failure"),
    (dict(injection=True), "injection_with_sensitive_data"),
    (dict(agent=AgentResult(proposed_outcome="HUMAN_REVIEW", stopped_reason="final_answer")), "agent_requested_review"),
    (dict(agent=AgentResult(stopped_reason="tool_failure")), "agent_failure_high_impact"),
])  # fmt: skip
def test_human_review_triggers(kw, trigger):
    d = run(**kw)
    assert d.outcome == "HUMAN_REVIEW" and f"review:{trigger}" in d.reason_codes


def test_agent_can_raise_to_review_but_never_lower():
    warn_case = dict(level="INTERNAL", classification=Classification(ok=True, status="ok", level="INTERNAL"),
                     destination="partner_external", policy=PolicyContext(question="q", status="ANSWERED", effect="requires_approval"))  # fmt: skip
    base = run(**warn_case)
    up = run(
        **warn_case, agent=AgentResult(proposed_outcome="ESCALATE", stopped_reason="final_answer")
    )
    assert up.outcome == "HUMAN_REVIEW" and "review:agent_disagreement_up" in up.reason_codes
    down = run(agent=AgentResult(proposed_outcome="ALLOW", stopped_reason="final_answer"))
    assert down.outcome == "ESCALATE" and any(
        r.startswith("agent_disagreement_down_ignored") for r in down.reason_codes
    )
    assert base.outcome in ("WARN", "ESCALATE")


def test_exception_discount_only_from_a_verified_record_and_never_below_the_floor():
    exc = {"exception_id": "DLPX-1"}
    d = run(
        agent=AgentResult(
            proposed_outcome="ESCALATE", stopped_reason="final_answer", verified_exception=exc
        )
    )
    assert any(r.startswith("verified_exception:DLPX-1:-20") for r in d.reason_codes)
    assert (
        d.band == "ESCALATE"
    )  # still high: the floor for a prohibited Restricted file with an exception is WARN, score stays high
    plain = run(agent=AgentResult(proposed_outcome="ESCALATE", stopped_reason="final_answer"))
    assert not any("verified_exception" in r for r in plain.reason_codes)
