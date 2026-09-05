# RecoveryOS — 8-minute live demo script

A presenter-run walkthrough built on the E.10 MSMED ladder, E.11 AI Analyzer
anomaly clusters, and the E.12 demo clock. Everything below runs against the
default in-memory demo dashboard — no database rows or external calls are
required, and the numbers come from the real decision traces, not hard-coding.

## 0. Boot (60s)

```bash
uvicorn app.main:app --reload                 # or however the app is started
python -c "from app.dashboard.demo_data import build_synthetic_demo_dashboard; d=build_synthetic_demo_dashboard(); print('seeded', len(d._tracer._traces), 'traces')"
```

Open in the browser:
- Command Center — http://127.0.0.1:8000/static/index.html
- Attack the Agent (red team) — http://127.0.0.1:8000/static/red_team.html

## 1. The honest numbers (90s)

Hit `/api/dashboard/load-synthetic` (the "Load synthetic batch" button), then:

```
GET /api/dashboard/scorecard
GET /api/dashboard/waterfall
GET /api/dashboard/contacts_avoided
```

Narration: every number on these cards is aggregated from `DecisionTrace`
records written during the batch run (`§14`), so the dashboard cannot report a
recovery that wasn't logged. The waterfall separates natural recovery,
intervention recovery, and contacts avoided — the bars are real.

## 2. AI Analyzer — anomaly clusters (90s)

Open the Command Center and scroll to the **AI Analyzer → Anomaly Clusters**
panel, or:

```
GET /api/dashboard/anomalies
```

Narration: a two-proportion z-test over the last hour of traces flags root-cause
families whose failure share is statistically unusual (`z ≥ 2.58`), with a
high-value tier for exposures at/over ₹1L. Flagged clusters show the failure
proxy, the cohort, and the severity badge (HIGH/MEDIUM/WATCH). Nothing is
executed on their behalf — the analyzer only produces a note, so the demo stays
"detection only" (guardrail, §16).

## 3. The demo clock (90s)

In the **Ops Console** block use **+1h / +1d / Reset** (or drive the API):

```
GET  /api/dev/clock
POST /api/dev/advance-clock?hours=24
GET  /api/dev/clock
POST /api/dev/reset-clock
```

Narration: every time-sensitive path in the system — policy timing, PTP
extraction, message templates, risk features, statutory accrual — reads from
one overridable UTC clock (E.12). Advancing it by a day recomputes MSMED §16
interest, rungs, and decision-status freshness deterministically, so a
presenter can show "what happens tomorrow" without waiting.

## 4. MSMED ladder — day-45/past-due B2B case (2 min)

Find a B2B case that is past Statutory Day 45:

```bash
curl -s http://127.0.0.1:8000/api/dashboard/cases?limit=250 |
  python -c "import sys,json;d=json.load(sys.stdin);print([c['case_id'] for c in d if c['root_cause']=='overdue_invoice'][:5])"
```

Pick one, then:

```
GET  /api/cases/{case_id}/msmed/status
POST /api/cases/{case_id}/msmed/status            # refresh vs. current clock
GET  /api/cases/{case_id}/decision-packet
```

Narration:
1. **§16 interest is computed, never charged** — the status shows the principal,
   accrued interest at 3× the RBI bank rate with monthly rests (MSMED Act 2006
   §16), the statutory due date (invoice + 45 days, §15), and the rung.
2. Advance the clock past the next rung boundary (`POST /api/dev/advance-clock?hours=24`,
   then `POST /api/cases/{case_id}/msmed/status`) — the rung and the accrued
   figure recompute on the spot. Hit **Reset** when done.
3. If the case is at Rung 3/4, show the demand-notice draft is **drafted for
   human review**, not sent automatically.

## 5. Conciliation filing — request → approve → dispatch (2 min)

Still on the day-45+ case:

```
POST /api/cases/{case_id}/msmed/conciliation   {"action": "request"}    → PENDING_SIGNOFF
POST /api/cases/{case_id}/msmed/conciliation   {"action": "approve"}    → APPROVED
POST /api/cases/{case_id}/msmed/conciliation   {"action": "dispatch"}   → FILED
```

Narration: the ladder rejects any straight-line request→dispatch; each filing
needs an explicit human sign-off (audit action `MSMED_RUNG_ESCALATED` /
`MSMED_FILING_*` records the transition). The notice cites MSMED Act 2006
§§15–18. **This workflow can never auto-file** — there is no code path that
skips the human.

## 6. Reason to believe the guardrails (60s)

Open **Attack the Agent** and fire two attacks:
- **Prompt injection** → classified as prompt-injection; recovery reads the
  injected line back to the attacker but does not act on it.
- **Wrong person** → outreach stops (no disclosure), case flagged.

Then show the "7:01 PM" contact-window hold: queue a message at 19:01, watch it
held for the next legal window, not sent.

## 7. Close (30s)

Summary slide hooks:
- The AI proposes, the Policy Engine gates, the Executor performs — no
  `LLM → Razorpay API` shortcut (§1.3).
- Statutory instruments carry **citations** on the trace; internal controls are
  honestly labelled `internal_policy` (see `app/policy/legal_basis.py` and the
  README compliance table).
- Audit: append-only hash chain, full decision traces, counterfactual —
  "measured money recovered, with compliant escalation and a stopping rule."