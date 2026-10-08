# UC5 demo guide (about 5 minutes)

**Core message:** "The agent does not decide whether an employee is malicious. It investigates the
incident, correlates evidence, exposes uncertainty, and helps the analyst reach a faster,
better-grounded decision."

## Run it

```
cd ~/Documents/GitHub/dataguard-ai
.venv/bin/dataguard-uc4 demo serve --mode replay      # add --port 8766 if 8765 is busy
```

Open `http://localhost:8765`, then **Data Security Incident Investigation Agent → Incident
Investigation**. Replay needs no network or keys and reproduces the recorded live runs exactly.

## Script

1. **The alert (30 s).** Select *Flagship: possible customer-data exfiltration* (INC-001). Point at the
   incident panel: the alert, an alias for the subject, three file handles. "The agent starts with only
   this."
2. **Investigate (15 s).** Press **Investigate**.
3. **The decision first (45 s).** *Severity and review*: **HIGH**, floor HIGH, rule
   `sensitive_exfiltration`; review **REQUIRED**; the **potential Severity 1** notice ("only an analyst
   can confirm it and set CRITICAL"); the agent agreed; remediation: none executed.
4. **The agent's report (30 s).** Its summary ends "consistent with a potential data-exposure incident
   requiring analyst investigation". "AI investigation, not authoritative."
5. **Timeline (45 s).** Authoritative times; hour-level events marked; **order uncertain** where the
   23:00 upload and the 23:48 DLP alert share an hour; the agent-retrieved column.
6. **Correlation (45 s).** The gap first: **who controls the Dropbox account is not established**. Then
   the links: upload ↔ alert, 262-file download before the transfer, after-hours, Highly Confidential
   files, data outside the role's expected classes.
7. **Evidence by source (30 s).** UC4 (Highly Confidential, financial), UC2 (HIGH anomaly, 0.7996), UC3
   (an active project grant), UC1 (personal cloud), UC6 (policy citations).
8. **Claims (30 s).** Each claim's type and status; one is **excluded as unsupported** (a policy claim
   citing a result instead of a policy section). "Nothing reaches the analyst without evidence."
9. **Trace (20 s).** The tools the agent chose, one out-of-scope call refused, tokens.
10. **Analyst (20 s).** In *Analyst review*, set **CRITICAL** with a note: only here, only by a person;
    nothing executed.
11. **One adversarial example (30 s).** Select INC-012 (*Prompt injection in a security log*), Investigate:
    the comment is withheld, severity stays HIGH, review reasons include "untrusted input flagged".
12. **Evidence of quality (30 s).** *Evaluations* → Incident Investigation (UC5): 15/16, 0 floor
    violations, evidence 1.0; *Responsible AI / Guardrails* → live probes; *Observability* → privacy
    audit clean.

**Optional:** switch the backend to *Foundry agent: dataguard-incident-investigator* for INC-001, 003,
012 or 014 (the recorded cases); other cases show a message in replay.

## Likely questions

* *Why one agent?* The capabilities are already authoritative services; UC2 showed one agent matched
  multi-agent accuracy at a fraction of the calls.
* *Can the agent be talked into a lower severity?* No: the harness recomputes the facts and the agent
  can only raise. Ten live attacks did not weaken an outcome.
* *What did evaluation find?* Over-escalation and unverified approvals in run 1 (fixed in v2); a
  rubric gap on content-unknown uploads (INC-014); a prompt-vs-tool mismatch the Foundry judges caught.
* *Privacy?* Aliases only; no raw user id reached Foundry; DataGuard telemetry clean live.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Address already in use | add `--port 8766` |
| Incident Investigation missing | `git checkout main && git pull`, restart |
| "The Foundry agent is recorded for …" | expected in replay; use the app agent or a recorded case |
| Queue empty | investigate an incident that needs review first |
