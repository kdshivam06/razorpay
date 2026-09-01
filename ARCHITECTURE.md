# RecoveryOS Architecture

The authoritative design lives in `docs/implementation_plan.md` §2. This page
reproduces the definitive architecture diagram and explains the non-negotiable
**three-layer separation** that every module must respect.

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
