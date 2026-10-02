# UC1 risk, response and human-in-the-loop

The harness (`app/dlp/harness.py`, rubric `config/dlp/risk.v1.yaml` **1.1.0**) owns the outcome.
Every point and rule is a reason code on the decision, in the trace and on the dashboard. Changing
any number is a new `rubric_version`.

## 1. Points

| Factor | Points |
|---|---|
| Level | PUBLIC 0 · INTERNAL 10 · CONFIDENTIAL 25 · HIGHLY_CONFIDENTIAL 40 |
| High-risk category (UC4) | +10 |
| Destination | approved corporate 0 · partner 15 · unknown external 20 · personal email / personal cloud / generative AI 30 · restricted (paste sites, public repos) 40 |
| Policy effect (verified UC6 citations) | allowed 0 · requires approval 10 · prohibited 25 · unknown 5 |
| Behaviour | NORMAL 0 · ELEVATED 10 · UNUSUAL 20 |
| Identity | contractor +5 · privileged +5 · notice period +15 |
| Verified exception (tool result only) | −20 |

**Exposure gating (1.1.0).** Level, category, behaviour and identity points count only when the
destination carries exposure. A sensitive file moving inside an approved corporate system is not a
DLP exposure. This was found in the first offline dry run, which used no model results: v1.0.0
escalated D03 (a Confidential file to the company's managed file transfer) at score 60. The fix is
recorded in the rubric's changelog. The golden set was not changed.

## 2. Bands, floors, triggers (in this order)

1. **Band:** score ≥ 60 → ESCALATE; ≥ 30 → WARN; otherwise ALLOW.
2. **Floors.** A verified prohibition can never score below these:
   * prohibited and level ≥ CONFIDENTIAL → at least ESCALATE (a verified exception can lower it to
     WARN, never to ALLOW);
   * prohibited and level ≥ INTERNAL → at least WARN;
   * restricted destination and level ≥ INTERNAL → ESCALATE (no exception discount).
3. **Human-review triggers.** Any of these makes the outcome HUMAN_REVIEW, with a
   `review:<trigger>` reason code:

| Trigger | When |
|---|---|
| `classification_uncertain` | UC4 `review_required`, no level, or rejected/error |
| `policy_conflict` | UC6 `CONFLICT_REVIEW`, or verified effects that disagree |
| `policy_insufficient_high_impact` | UC6 has no evidence, level ≥ CONFIDENTIAL, destination not approved |
| `stage_failure` | identity, behaviour or policy failed |
| `agent_disagreement_up` | the agent proposed a **higher** severity than the rubric |
| `agent_requested_review` | the agent called `request_human_review` |
| `injection_with_sensitive_data` | instruction-like text in the document or justification, and level ≥ CONFIDENTIAL |
| `agent_failure_high_impact` | the agent stopped without a usable answer and the band is WARN or higher |

**The agent can raise, never lower.**
* A lower proposal is ignored and logged as `agent_disagreement_down_ignored:<outcome>`.
* A higher proposal is never applied directly. It sends the case to a person.

## 3. Human-in-the-loop and simulated actions

* **ESCALATE always requires human approval** (`escalate_requires_human_approval: true`).
* WARN, ESCALATE and HUMAN_REVIEW on a non-corporate destination carry a **SIMULATED** action
  proposal from the rubric:
  * personal cloud: "Block the upload (simulated - requires analyst approval)";
  * personal email: "Hold the email (simulated …)";
  * and so on, one per destination class.
* No code path blocks, deletes, quarantines or sends anything. The dashboard's approval queue
  records an analyst's choice (approve simulated action / dismiss as false positive / request more
  information) as demo-only state:
  * `executed: false`, `simulated: true`, `not_gold_adjudication: true`;
  * never written to an evaluation set.

## 4. Worked example: the flagship (D11)

| Factor | Value | Points |
|---|---|---|
| Level | HIGHLY_CONFIDENTIAL | +40 |
| High-risk category | MA_CORP_STRATEGY | +10 |
| Destination | personal_cloud | +30 |
| Policy effect | prohibited (verified citations: AUP §5.3, CLS §3.2; CLS §2.4 defines Restricted) | +25 |
| Behaviour | UNUSUAL (after hours, frequent external uploads, unusual download volume, high sensitive-file access, new destination) | +20 |
| Identity | privileged | +5 |
| **Score** | | **130 → ESCALATE** |

The floor (prohibited + ≥ CONFIDENTIAL) would have forced ESCALATE anyway. `check_dlp_exception`
returned no record, so there is no discount. The agent proposed ESCALATE (agreement). The outcome
is ESCALATE plus a simulated "block the upload" awaiting an analyst.

## 5. Known rubric gaps (measured, not tuned)

* **D02, a Public file to personal cloud, gets WARN.** Destination points (30) apply even when the
  data is Public. The policy mapping says Public data is allowed anywhere, but `allowed` adds 0
  rather than cancelling exposure. Fixing it would be rubric 1.2.0 and would be tuning on the
  evaluation set, so it is documented, not changed.
* **`agent_disagreement_up` costs review workload** (D08, D10: the rubric says WARN, the agent says
  ESCALATE, and the case goes to HUMAN_REVIEW). This is the approved trade-off: a model's stricter
  view is never discarded, and never applied without a person.
