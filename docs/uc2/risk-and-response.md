# UC2 risk and response

The outcome is decided by `app/insider/harness.py::decide()` from `config/insider/risk.v1.yaml`
(**rubric 1.2.0**). The risk agent recommends; the harness decides; an analyst acts. Every point,
floor, ceiling and trigger is a reason code on the case and in the trace.

## 1. Outcomes

| Outcome | Meaning | Analyst |
|---|---|---|
| MONITOR | Nothing needs attention; normal monitoring continues | No |
| INVESTIGATE | An analyst should look at this case | Yes |
| ESCALATE | A senior analyst should review promptly | Yes |
| HUMAN_REVIEW | The system could not reach a reliable assessment (missing, conflicting or failed evidence) | Yes |

No outcome triggers an action. There is no tool to disable an account, revoke access, notify a
manager or open an HR case.

## 2. Points

| Factor | Points |
|---|---|
| Anomaly band (Isolation Forest) | NORMAL 0 · ELEVATED 15 · HIGH_ANOMALY 35 |
| Contributing signals | 5 each, at most 3 |
| UC4 data level (max over case files) **†** | Internal 5 · Confidential 15 · Highly Confidential 25 |
| UC4 high-risk category **†** | 10 |
| External transfer on the case day | personal / unknown / restricted 15 · partner 5 · approved corporate 0 |
| UC6 policy effect | prohibited 15 · requires approval 5 |
| Privileged access (access context, not HR), when the band is not NORMAL | 5 |
| **Verified** approval (register match for this user and date) | −20 |

**†** **Role-context gating (1.2.0):** data points count only when a file's categories fall outside
the role's expected data classes, or when the day includes an external transfer to a non-corporate
destination. Without identity context they always count (the conservative default).

Bands: **≥ 60 ESCALATE**, **≥ 30 INVESTIGATE**, otherwise MONITOR.

## 3. Floors, ceiling and the agent

* **Floors:** HIGH_ANOMALY → at least INVESTIGATE. HIGH_ANOMALY + Highly Confidential + external
  transfer → ESCALATE.
* **Approval ceiling (1.1.0):** a verified approval caps the scored outcome at INVESTIGATE (an
  analyst confirms the scope), unless the day includes a transfer to a personal or unknown
  destination.
* **The agent's recommendation:**

  | Recommendation vs the rubric | Effect | Reason code |
  |---|---|---|
  | Higher | Adopted | `agent_raised` |
  | One level lower | Ignored | `agent_lower_ignored` |
  | Two or more lower | HUMAN_REVIEW | `review:agent_disagreement` |
  | HUMAN_REVIEW | HUMAN_REVIEW | `review:agent_disagreement` |

  The agent can never silently lower a deterministic high-risk outcome.

## 4. Human-review triggers

| Trigger | When |
|---|---|
| `required_capability_failed` | Identity, logs, UC4 or the behaviour series failed, including SIMULATED faults |
| `required_evidence_missing` | ELEVATED+: no log timeline or unclassified files; HIGH: no policy result |
| `data_uncertain` | UC4 could not classify a case file |
| `policy_conflict` | UC6 reported a conflict material at this data level, or verified effects disagree |
| `policy_insufficient_material` | HIGH_ANOMALY, no policy evidence and no verified prohibition |
| `specialist_conflict` | The investigator **cited** conflicting evidence (uncited conflicts are logged only) |
| `agent_disagreement` / `agent_failure` | See section 3; or a required agent produced no valid result |
| `unsupported_conclusion` | Guilt, intent or employment wording was produced and withheld |

**Level-aware UC6 conflicts:** a UC6 conflict between policies that both prohibit the action at
this level is not material. It is logged as `uc6_conflict_not_material_at_level` and doesn't block
the case. This was found in live run 1, where it sent the flagship to review instead of ESCALATE.

## 5. Worked example: the flagship (I13)

| Factor | Value | Points |
|---|---|---|
| Anomaly band | HIGH_ANOMALY (0.7996) | +35 |
| Contributing signals | 3 | +15 |
| Data level | HIGHLY_CONFIDENTIAL (outside the product role, and an external transfer) | +25 |
| High-risk category | credentials, financial, source code | +10 |
| External transfer | personal cloud, 2,400 MB | +15 |
| Policy effect | prohibited (POL-AUP §5.3) | +15 |
| **Score** | band ESCALATE, floor ESCALATE | **115** |

The risk agent recommended ESCALATE, so there was no disagreement. Outcome: **ESCALATE**, analyst
review required. The summary lines are fixed templates, each labelled with a claim type (observed
fact, inferred anomaly, policy requirement, agent interpretation) and an evidence id. The anomaly
line always ends "*An anomaly is not evidence of intent.*"

## 6. Human in the loop

* **Every non-MONITOR outcome is queued for an analyst.** The dashboard offers *agree*, *should be
  lower*, *should be higher* and *needs more information*, with a note.
* **Decisions are recorded, not executed.** They're marked `simulated: true` and
  `not_gold_adjudication: true`. They never become evaluation labels.
* **They feed the controlled learning loop** ([`learning-loop.md`](learning-loop.md)), where a
  candidate rubric change must pass an offline gate and a person's approval.

## 7. Rubric history

| Version | Change | Found by |
|---|---|---|
| 1.0.0 | Initial | |
| 1.1.0 | Verified-approval ceiling | First offline dry run: an approved migration (I10) escalated on volume alone |
| 1.2.0 | Role-context gating | Offline dry run: month-end finance work (I07, I08) scored as out-of-role data |
| 1.3.0-candidate | Agent review requests on quiet days are logged, not forced | The learning loop; **not promoted**, awaiting a human decision |

Each change was approved by the product owner and recorded in the rubric's changelog. The golden
set was never changed to fit a rubric.
