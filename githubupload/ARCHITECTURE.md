# RecoveryOS — System Architecture

> Deep technical design document covering the three-layer separation, runtime pipeline, 
> data flow, state machines, and implementation mapping.

---

## 1. Design Philosophy

RecoveryOS is built on a single, non-negotiable principle:

> **The AI proposes. The Policy Engine gates. The Executor performs.**
>
> There is no path from an LLM output to a Razorpay API call without a deterministic
> policy check in between. This is a hard architectural invariant, not a suggestion.

### Why This Matters

1. **Auditability:** Every action taken (and every action blocked) has a complete trace from raw event to final outcome.
2. **Safety:** AI hallucinations, adversarial inputs, and model drift cannot cause unintended financial actions.
3. **Compliance:** Regulatory constraints (TRAI, RBI, NPCI, MSMED) are enforced deterministically, not probabilistically.
4. **Testability:** Each layer can be tested independently — the classifier doesn't need a running Razorpay sandbox to verify.

---

## 2. The Three-Layer Separation

```
┌─────────────────────────────────────────────────┐
│                  AI/ML LAYER                     │
│  Root Cause Engine · Propensity · Uplift         │
│  NLP · Channel Affinity · Optimal Timing         │
│                                                   │
│  OUTPUT: Ranked CandidateAction[]                 │
│  with per-action economics and uplift estimates   │
├─────────────────────────────────────────────────┤
│                 POLICY ENGINE                     │
│  Consent · Contact Window · DND · Cooldown       │
│  Fraud · Dispute · Reversibility · Blast Radius  │
│  Fatigue · Platform Awareness · Simulation       │
│                                                   │
│  INPUT:  Best CandidateAction from AI layer      │
│  OUTPUT: APPROVED or BLOCKED (with reason)        │
├─────────────────────────────────────────────────┤
│               EXECUTION LAYER                     │
│  Pre-Action Reconciliation · Idempotency Key     │
│  Circuit Breaker · Transactional Outbox          │
│  Razorpay API calls · Payment Links              │
│  Notifications · Voice Agent · Human Queue       │
│                                                   │
│  INPUT:  APPROVED action only                     │
│  OUTPUT: ExecutionResult (SUCCESS/FAILED/UNKNOWN) │
└─────────────────────────────────────────────────┘
```

### Layer 1: AI/ML Layer — Proposes

The AI layer's job is to *suggest* the best recovery action. It has no authority to execute anything.

**Components:**
- `app/classifier/` — Hybrid root-cause classification (deterministic rules + ML)
- `app/revenue_risk/` — Payment propensity, time-to-pay, incremental uplift model
- `app/optimizer/` — Intervention optimizer (economic scoring, budget allocation, channel affinity)
- `app/nlp/` — Communication intelligence (intent, emotion, PTP extraction, safety guards)
- `app/modules/` — Domain-specific recovery modules (A through G)

**Output Contract:**
```python
@dataclass(frozen=True)
class CandidateAction:
    action: Action                    # One of 14 actions
    expected_recovery_paise: int      # Expected incremental recovery
    communication_cost_paise: int     # Direct channel cost
    operational_cost_paise: int       # Processing overhead
    risk_penalty_paise: int           # Fraud/compliance risk cost
    cx_penalty_paise: int             # Customer experience cost
    economic_score: float             # Net economic value
```

### Layer 2: Policy Engine — Gates

The Policy Engine evaluates every proposed action against a battery of compliance checks. A single failure blocks the action.

**Components:**
- `app/policy/policy_engine.py` — Master gate orchestrator
- `app/policy/consent_manager.py` — TRAI communication consent
- `app/policy/contact_policy.py` — Per-channel contact windows
- `app/policy/customer_preferences.py` — DND and customer preferences
- `app/policy/cooldown_manager.py` — Redis-backed per-customer cooldown (fail-closed)
- `app/policy/fraud_detector.py` — Fraud probability gate
- `app/policy/blast_radius.py` — Global action rate limiting
- `app/policy/reversibility.py` — Impact classification for human approval
- `app/policy/platform_awareness.py` — Cross-platform deduplication
- `app/policy/simulation_mode.py` — Dry-run mode

**Decision Flow:**
```
CandidateAction
    │
    ├── Consent check      → BLOCKED: "no SMS consent for this customer"
    ├── Contact window     → BLOCKED: "outside 9AM-8PM IST window"
    ├── DND check          → BLOCKED: "customer on Do-Not-Disturb"
    ├── Cooldown check     → BLOCKED: "contacted via SMS 6 hours ago"
    ├── Fraud check        → BLOCKED: "fraud probability 0.87 > threshold 0.5"
    ├── Dispute check      → BLOCKED: "active dispute on obligation"
    ├── Reversibility      → ESCALATED: "payment link creation requires human approval"
    ├── Blast radius       → BLOCKED: "global SMS rate exceeded hourly limit"
    ├── Fatigue check      → BLOCKED: "cumulative fatigue score exceeds threshold"
    └── Platform check     → BLOCKED: "Razorpay already sent notification for this case"
    │
    ▼
 APPROVED → Proceed to Executor
```

### Layer 3: Executor — Performs

The Executor is the **only** layer that touches external APIs. Every call is wrapped in safety mechanisms.

**Components:**
- `app/executor/action_executor.py` — Central execution orchestrator
- `app/executor/idempotency.py` — Deterministic key generation and dedup
- `app/executor/circuit_breaker.py` — Dependency health tracking
- `app/executor/transactional_outbox.py` — Atomic commit of decision + command
- `app/executor/payment_link.py` — Razorpay Payment Link lifecycle
- `app/executor/notification.py` — Multi-channel notification sender
- `app/executor/voice_agent.py` — Voice call handling
- `app/executor/human_queue.py` — P0–P4 priority human escalation queue

**Safety Mechanisms:**
1. **Pre-Action Reconciliation:** Before any outbound call, re-query the ledger. If the case state changed since the decision was made (e.g., payment arrived during processing), abort.
2. **Idempotency Key:** Every action generates a deterministic key. Duplicate executions are no-ops.
3. **Circuit Breaker:** After N consecutive failures to an external dependency, the circuit opens. All subsequent calls fast-fail and route to the human queue.
4. **Transactional Outbox:** The decision and its outbound command commit in a single DB transaction. A background worker effects the command exactly once.
5. **UNKNOWN State Handling:** If an API returns an ambiguous response, the `UnknownStateHandler` routes to reconciliation — never blindly retries.

---

## 3. State Management

### Recovery Case State Machine (18 States)

```
NEW → TRIAGED → DIAGNOSING → DIAGNOSED → PREDICTING → PREDICTED
  → OPTIMIZING → OPTIMIZED → POLICY_CHECK → APPROVED → EXECUTING
  → EXECUTED → RECONCILING → RECOVERED → CLOSED

  Special transitions:
  Any → ESCALATED (human intervention required)
  Any → STOPPED (compliance halt)
  Any → UNKNOWN → RECONCILING (ambiguous state recovery)
```

### Obligation Ledger

The Obligation Ledger is the canonical financial truth for every recovery case:

```python
class Obligation:
    status transitions:
        record_payment()      # Partial or full payment received
        record_refund()       # Refund processed
        mark_disputed()       # Active dispute registered
        start_recovery()      # Recovery process initiated
        create_ptp()          # Promise to pay registered
        resume_after_ptp()    # PTP window expired
        write_off()           # Uncollectible
        block()               # Fraud/compliance block
    
    properties:
        remaining_amount      # Current outstanding balance
        is_terminal           # No further action possible
        is_recovery_locked    # Prevents concurrent recovery
```

**Double-Dip Prevention:** The Obligation Ledger prevents the same payment from being counted toward multiple recovery cases. Recovery locks ensure exactly one module operates on an obligation at a time.

---

## 4. Event Gateway Architecture

```
                    ┌──────────────────────┐
                    │   POST /webhooks     │
                    └──────────┬───────────┘
                               │
                    ┌──────────┴───────────┐
                    │  HMAC-SHA256 Verify   │
                    │  (dual-key rotation)  │
                    └──────────┬───────────┘
                               │
                   ┌───────────┴──────────────┐
                   │ Freshness / Replay Check  │
                   └───────────┬──────────────┘
                               │
                   ┌───────────┴──────────────┐
                   │  EventInbox Dedup         │
                   │  (by event ID)            │
                   └───────────┬──────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                              │
           [Valid Event]              [Invalid / Error]
                │                              │
                ▼                              ▼
        Pipeline Processing           Dead-Letter Queue
        (RecoveryModule.run)           (manual review)
```

### HMAC Secret Rotation

The webhook validator supports dual-key rotation:
1. Try verification with the primary `WEBHOOK_SECRET`.
2. If that fails, try the `WEBHOOK_SECRET_PREVIOUS` (rotation key).
3. If both fail, reject with HTTP 400.

This allows zero-downtime secret rotation during key rollover periods.

---

## 5. Multi-Model AI Architecture

### Model Router

RecoveryOS routes to different AI models based on task complexity:

| Task | Model | Latency | Why |
|---|---|---|---|
| **Root-cause classification** | Rules Engine + LightGBM hybrid | < 10ms | Deterministic rules for known patterns; ML for ambiguous signals |
| **Unstructured error parsing** | Gemini | ~500ms | Bank switch errors are inconsistent across gateways |
| **Intent classification** (NLP) | Gemini | ~300ms | Free-text customer replies in Hinglish |
| **Emotion detection** | Gemini | ~200ms | Sentiment analysis for escalation decisions |
| **Conflicting signal reasoning** | Gemini | ~800ms | When rule-based default contradicts case history |
| **PTP extraction** | Gemini + regex post-processing | ~400ms | Date and amount extraction from unstructured text |

### Bounded Autonomy

```
AI Model Recommendation
         │
         ▼
┌─────────────────────────┐
│  DETERMINISTIC POLICY   │
│  GATE (all checks)      │
└────────┬────────────────┘
         │
    [Passed]    [Vetoed / Halted]
    ┌────┴────┐     ┌──────┐
    ▼         ▼     ▼      ▼
 Execute   Audit  Audit  Human
 Action    Log    Log    Queue
```

---

## 6. Data Flow — Complete Case Lifecycle

```
1. Razorpay Webhook → Event Gateway
   ├── HMAC verify (raw bytes, before JSON parse)
   ├── Freshness check (timestamp window)
   ├── Dedup (EventInbox by event_id)
   └── DLQ fallback (on any failure)

2. Event → Canonical State
   ├── Obligation Ledger update
   ├── RecoveryCase creation/advancement
   └── Recovery lock acquisition

3. RecoveryCase → AI/ML Layer
   ├── Root cause classification (hybrid)
   ├── Feature building (RiskFeatures)
   ├── Payment propensity prediction
   ├── Per-action uplift estimation
   ├── Economic scoring
   ├── Channel affinity lookup
   ├── Optimal timing recommendation
   └── Candidate action ranking

4. Best Candidate → Policy Engine
   ├── Sequential gate evaluation
   ├── First APPROVED candidate wins
   └── All rejections logged with reason

5. APPROVED Action → Executor
   ├── Pre-action reconciliation (re-check ledger)
   ├── Idempotency key generation
   ├── Circuit breaker check
   ├── External API call (Razorpay)
   ├── Transactional outbox commit
   └── UNKNOWN → reconciliation

6. Outcome → Measurement
   ├── Control group accounting
   ├── Experiment metric update
   ├── Drift detection check
   └── Counterfactual comparison

7. Everything → Audit
   ├── Hash-chain append
   ├── Decision trace build
   ├── Prevention log entry
   └── PII masking
```

---

## 7. Persistence Architecture

### PostgreSQL (Primary Datastore)

| Table | Purpose |
|---|---|
| `recovery_cases` | Case lifecycle and state machine |
| `obligations` | Financial ledger (amounts, status) |
| `audit_log` | Append-only hash-chain audit entries |
| `event_inbox` | Webhook deduplication |
| `dead_letter_queue` | Failed/rejected events |
| `payment_links` | Razorpay payment link lifecycle |
| `human_tasks` | Human escalation queue |
| `outbox_commands` | Transactional outbox for at-most-once delivery |

### Redis (Operational State)

| Key Pattern | Purpose | TTL |
|---|---|---|
| `cooldown:{customer_id}:{channel}` | Contact cooldown tracking | Configurable per strategy |
| `circuit:{dependency}` | Circuit breaker state | Until manual reset |
| `blast:{action}:hourly` | Global action rate counter | 1 hour |

### Hash-Chain Audit

Every audit entry includes:
```
hash(current_entry) = SHA-256(previous_hash + entry_payload + timestamp)
```

This creates a tamper-evident chain. The `verify_chain()` method walks the entire chain and flags any inconsistency.

---

## 8. Recovery Strategy Library

The `strategies/` directory contains YAML-defined recovery playbooks:

| Strategy File | Domain | Key Compliance Logic |
|---|---|---|
| `insufficient_funds.yaml` | Payment | Salary-day retry timing; exponential backoff |
| `expired_card.yaml` | Payment | Zero retry on expired instrument; method update request |
| `bank_timeout.yaml` | Infrastructure | Silent off-peak retry; gateway reroute |
| `checkout_abandoned.yaml` | E-commerce | Unit economics filter; single nudge cap |
| `mandate_failure.yaml` | Subscription | Customer vs. bank revocation distinction (CRITICAL) |
| `overdue_invoice.yaml` | B2B | MSMED Act 4-rung escalation ladder |
| `ptp_broken.yaml` | Collection | Reliability scoring; escalation after 3 broken promises |
| `dispute.yaml` | All | PERMANENT FREEZE on active dispute |

Each strategy defines:
- Root-cause signals (error codes, sources)
- Compliance rules (stopping conditions)
- Timing parameters (cooldowns, windows, backoff)
- Action sequences (ordered steps with conditions)
- Suppression rules (what to never do)
- Uplift expectations (per-segment conversion rates)
- Cost model (per-action channel costs)

---

## 9. Dashboard Architecture

### Frontend (HTML + Chart.js)

| Page | URL | Purpose |
|---|---|---|
| Recovery Waterfall | `/static/index.html` | Visual funnel from at-risk → recovered → net |
| Case Intelligence | `/static/intelligence.html` | Per-case decision trace explorer |
| Attack the Agent | `/static/red_team.html` | Interactive adversarial demo |

### Backend API Layer

The dashboard is a **read-only projection** over the Audit Logger, Decision Tracer, and Prevention Log. It never writes to the recovery pipeline — it only reads the results of decisions that have already been made and audited.

---

## 10. Scalability Considerations

| Component | Current Design | Production Scale |
|---|---|---|
| **Webhook Processing** | Synchronous per-event | Add message queue (RabbitMQ/SQS) for async batch processing |
| **State Locks** | In-process Python locks | Migrate to Redis distributed locks |
| **Circuit Breaker** | In-memory counter | Redis-backed shared counter across instances |
| **Audit Chain** | Single PostgreSQL table | Partition by month; archive to cold storage |
| **ML Inference** | Inline in request path | Move to async inference service with result caching |
| **Dashboard** | Server-rendered on API call | Add WebSocket for real-time push updates |
