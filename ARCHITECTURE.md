# RecoveryOS Architecture

The authoritative design lives in `docs/implementation_plan.md` §2. This page
reproduces the definitive architecture diagram and explains the non-negotiable
**three-layer separation** that every module must respect, then maps each layer
to the implemented components and the runtime pipeline.

> Companion docs: [`README.md`](README.md) for setup/run, [`INTERFACES.md`](INTERFACES.md)
> for the frozen cross-track contracts.

## §2.1 Definitive Architecture Diagram

```
RAZORPAY Webhooks / APIs / Test Mode
│
▼
┌─────────────────────────┐
│    EVENT GATEWAY         │
│  HMAC / Dedup / Schema   │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  EVENT INBOX / DLQ       │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│   CANONICAL STATE        │
│  Obligation Ledger       │
│  Recovery Cases          │
└──────────┬──────────────┘
           │
   ┌───────┼────────┐
   ▼       ▼        ▼
Root    Fraud/    Reconciliation
Cause   Risk      Engine
Engine  Engine
   │       │        │
   └───────┼────────┘
           ▼
┌─────────────────────────┐
│  REVENUE-AT-RISK ENGINE  │
└──────────┬──────────────┘
           │
   ┌───────┼────────┐
   ▼       ▼        ▼
Pay     Time-to   Uplift
Model   -Pay      Model
   │       │        │
   └───────┼────────┘
           ▼
┌─────────────────────────┐
│  INTERVENTION OPTIMIZER  │
│  Retry / Link / SMS      │
│  Email / WhatsApp / Voice│
│  PTP / Partial Payment   │
│  Human / WAIT / NO_ACTION│
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│     POLICY ENGINE        │
│  Consent / Contact Window│
│  DND / Cooldown / Fraud  │
│  Dispute / RBAC / Amount │
│  Reversibility           │
│  Blast Radius / Fatigue  │
└──────────┬──────────────┘
           │
      APPROVED ONLY
           ▼
┌─────────────────────────┐
│    EXECUTION LAYER       │
│  Razorpay APIs           │
│  Payment Links           │
│  Notifications           │
│  Mock Voice              │
│  Human Queue             │
│  Transactional Outbox    │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│    OUTCOME ENGINE        │
│  Reconcile / Recover     │
│  Stop / Escalate         │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│     MEASUREMENT          │
│  Control Group / Uplift  │
│  Experiments / ROI       │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│   AUDIT / LEARNING       │
│  Hash Chain              │
│  Decision Trace          │
│  Model Versions          │
│  Policy Versions         │
└─────────────────────────┘
```

## §2.2 The Three-Layer Separation (Non-Negotiable)

Every outbound action flows through exactly three responsibilities. This
ordering is a hard invariant of the system — it is what makes the agent safe,
auditable, and demo-ready.

| Layer | Role | What It Does |
|---|---|---|
| **AI/ML Layer** | Proposes | Diagnoses root cause, predicts probabilities, estimates uplift, ranks candidate actions |
| **Policy Engine** | Gates | Evaluates consent, contact window, DND, cooldown, fraud, dispute, RBAC, amount, reversibility, blast-radius. **Only approved actions proceed.** |
| **Executor** | Performs | Actually calls the Razorpay API with idempotency key, circuit breaker, timeout. Records in the transactional outbox. |

**Never:** `LLM → Razorpay API`

### Why this ordering matters

1. **The AI proposes, it does not act.**
   The ML/NLP layer may *suggest* `SEND_PAYMENT_LINK` with an expected uplift of
   +37%, but it has no authority to reach a payment provider. Its output is a
   ranked set of candidates and their economics — nothing more.

2. **The Policy Engine gates, it does not imagine.**
   Before any action proceeds, the policy layer enforces regulatory and
   merchant constraints:
   - TRAI communication **consent** (a phone number existing is *not* consent)
   - configurable **contact windows** per channel (never a blanket
     `RBI = 8 AM–7 PM`
   - **DND / customer preference**, **cooldown** (Redis, fail-closed)
   - **fraud**, **dispute halt**, **RBAC / amount** ceilings
   - **reversibility** (high-impact actions require human approval)
   - global **blast-radius** limits and contact **fatigue**.
   Only an action that passes every enabled check is marked `APPROVED`.
   A rejected action is logged with the specific reason it failed.

3. **The Executor performs, safely.**
   The executor is the only layer that ever touches external APIs. It wraps
   every call in:
   - **reconciliation first** — re-check the ledger before any outbound so we
     never send an SMS for a case that just got paid seconds ago;
   - an **idempotency key** so duplicate runs never duplicate effects;
   - a **circuit breaker** so a degraded dependency fails closed;
   - the **transactional outbox**, so the decision and its command commit in
     one DB transaction and a worker effects it exactly once.
   An API that returns UNKNOWN is reconciled, never blindly retried (§8.3).

### How this maps to the codebase

- **AI/ML Layer** → `app/classifier/`, `app/revenue_risk/`, `app/optimizer/`,
  `app/nlp/`, `app/modules/`.
- **Policy Engine** → `app/policy/` (`PolicyEngine` is the master gate).
- **Executor** → `app/executor/`, `app/reconciliation/pre_action_check.py`.
- Everything is traced back through `app/audit/` (decision trace + hash-chain
  audit) and measured via `app/measurement/` (control group, uplift, ROI).

The exact signatures these layers must satisfy are frozen in
[`INTERFACES.md`](INTERFACES.md).

## Implemented components per layer

Every layer below is fully implemented (no `NotImplementedError` remains),
with the AI Loop (`§1.3`) driven by `app/modules/base_module.py`.

| Layer | In `app/` | Key components |
|---|---|---|
| **Event Gateway** | `ingestion/` | `webhook_validator` (HMAC + rotation, replay), `event_inbox` (dedup), `webhook_handler`, `dead_letter_queue`, `batch_loader` |
| **State** | `core/` | `Obligation` ledger (double-dip prevention, recovery lock), `RecoveryCase` + 18-state FSM, `dependency_health` |
| **AI/ML (Propose)** | `classifier/`, `revenue_risk/`, `optimizer/`, `nlp/`, `modules/` | hybrid classifier, propensity + uplift models, intervention optimizer (economics, fatigue, budget, multi-obligation), intent/PTP/NLP safety, recovery modules |
| **Policy (Gate)** | `policy/` | `PolicyEngine` master gate + consent, contact window, preferences, cooldown (Redis), fraud, dispute, reversibility, blast radius, fatigue, platform awareness, `simulation_mode` |
| **Executor (Perform)** | `executor/`, `reconciliation/` | `ActionExecutor`, `idempotency`, `circuit_breaker`, `transactional_outbox`, `payment_link` lifecycle, `human_queue`, `PreActionReconciler`, `OutOfOrderGuard`, `UnknownStateHandler` |
| **Outcome / Measure** | `reconciliation/`, `measurement/` | reconciliation engine, control group, experiment engine, counterfactual simulator |
| **Audit / Health** | `audit/`, `health/` | `AuditLogger` (hash chain), `PreventionLog`, `DecisionTracer`, `AgentMonitor` |
| **Dashboard / Demo** | `dashboard/` | `api` (waterfall, scorecard, segments, contacts, queue), `red_team_api` (Attack-the-Agent demo), `static/*` (HTML + Chart.js) |

## Runtime pipeline (one case)

```
POST /webhooks  (or batch load)
      │  HMAC-verify → freshness → dedup (EventInbox) → DLQ on failure
      ▼
BaseRecoveryModule.run(case)                    app/modules/base_module.py
  OBSERVE → RECONSTRUCT (Obligation ledger)
  DIAGNOSE  → root-cause classification
  PREDICT   → natural payment probability
  GENERATE CANDIDATES → ranked economic candidates
  OPTIMIZE  → sort by economic score
  POLICY    → PolicyEngine: first APPROVED candidate wins
  PRE-ACTION RECONCILE → re-check ledger before any outbound
  EXECUTE   → ActionExecutor (idempotency key, circuit breaker, outbox)
  LEARN     → DecisionTracer trace + AuditLogger hash-chained entry
      │
      ▼
Dashboard read-only projection (app/dashboard/api.py)
  waterfall + scorecard from DecisionTracer / AuditLogger / PreventionLog
```

## Fail-closed safety properties (§2.4)

- **Redis down → outbound contact refused** (`DependencyHealth.fail_closed_reason("contact")`).
- **DB cannot guarantee state → irreversible financial action blocked**
  (`DependencyHealth.fail_closed_reason("financial")`).
- **Circuit breaker opens after N failures** — degraded dependency stops being
  called and work falls to the human queue.
- **Duplicate runs dedup** by idempotency key; **UNKNOWN never blindly retried**
  — the `UnknownStateHandler` routes to reconciliation instead.
