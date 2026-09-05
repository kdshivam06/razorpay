# RecoveryOS — Compliance Rules & Policy Reference

This document is the definitive reference for every compliance rule, stopping condition, and policy gate enforced by RecoveryOS. These are **deterministic** — AI models cannot alter or bypass them.

> [!IMPORTANT]
> **Constitutional Safety Invariant:** AI models propose actions. Deterministic rules govern execution. The Policy Engine holds absolute veto authority over any AI recommendation.

---

## 1. The Policy Engine Gate Hierarchy

Every outbound action must pass through all enabled policy gates **sequentially**. A single failure blocks the action and logs the reason. The order matters — cheapest checks run first to fail fast.

```
CandidateAction (from AI/ML Layer)
    │
    ├── 1. Consent Gate           [TRAI compliance]
    ├── 2. Contact Window Gate    [Per-channel hourly restrictions]
    ├── 3. DND / Preferences Gate [Customer opt-out preferences]
    ├── 4. Cooldown Gate          [Redis-backed, fail-closed]
    ├── 5. Fraud Gate             [Probability threshold]
    ├── 6. Dispute Gate           [Active dispute freeze]
    ├── 7. Reversibility Gate     [Impact classification]
    ├── 8. Amount / RBAC Gate     [Autonomous action limits]
    ├── 9. Blast Radius Gate      [Global rate limiting]
    ├── 10. Contact Fatigue Gate  [Cumulative contact burden]
    ├── 11. Platform Awareness    [Cross-platform dedup]
    └── 12. Simulation Gate       [Dry-run mode check]
    │
    ▼
 APPROVED  →  Pre-Action Reconciliation  →  Executor
 BLOCKED   →  Decision Trace (with reason)  →  Prevention Log
```

---

## 2. Detailed Gate Specifications

### Gate 1: Consent (TRAI Compliance)

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/consent_manager.py` |
| **Regulation** | TRAI Telecom Commercial Communications Customer Preference Regulations |
| **Principle** | A phone number existing is NOT consent. Explicit opt-in required per channel AND per purpose. |
| **Consent States** | `GRANTED`, `REVOKED`, `UNKNOWN` |
| **On REVOKED** | BLOCK — permanent for that channel until re-consent |
| **On UNKNOWN** | BLOCK — fail-closed; cannot assume consent |
| **Audit** | Every consent check result is logged in the Decision Trace |

### Gate 2: Contact Window

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/contact_policy.py` |
| **Default Windows** | Configurable per channel per merchant (via YAML) |
| **Example** | SMS: 9:00 AM – 8:00 PM IST weekdays; no weekends |
| **On violation** | HOLD — action queued for next valid window |
| **Weekend handling** | Saturday PM/Sunday → held until Monday 9:00 AM |
| **Timezone** | All times in IST (UTC+05:30) |

### Gate 3: DND / Customer Preferences

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/customer_preferences.py` |
| **Channels tracked** | SMS, Email, WhatsApp, Voice Call, Payment Link |
| **Per-customer windows** | Customers can set preferred contact times |
| **DND flag** | Global "do not contact" across all channels |
| **On DND** | PERMANENT BLOCK — no automated outreach ever |

### Gate 4: Cooldown (Redis, Fail-Closed)

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/cooldown_manager.py` |
| **Storage** | Redis with per-customer, per-channel TTL keys |
| **Default cooldown** | Configurable per strategy (e.g., 48 hours for SMS) |
| **Backoff** | Exponential backoff on repeated contacts |
| **Redis unavailable** | **FAIL-CLOSED: assume within cooldown → BLOCK** |
| **Rationale** | Never contact when cooldown state is unknown |

### Gate 5: Fraud Detection

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/fraud_detector.py` |
| **Threshold** | Configurable fraud probability threshold (default: 0.5) |
| **On fraud** | PERMANENT STOP — zero retries, zero contact |
| **Actions suppressed** | ALL (including WAIT and NO_ACTION variants) |
| **Audit** | Fraud assessment and probability logged |

### Gate 6: Dispute Halt

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/policy_engine.py` (dispute check) |
| **Trigger** | `Obligation.mark_disputed()` sets dispute flag |
| **On dispute** | FREEZE all automated actions immediately |
| **Duration** | Until dispute is manually resolved by human operator |
| **Scope** | Per-obligation freeze (other obligations for same customer may continue) |
| **Rationale** | Chasing disputed accounts damages relationships and violates conventions |

### Gate 7: Reversibility (Impact Classification)

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/reversibility.py` |
| **Impact levels** | LOW, MEDIUM, HIGH |
| **LOW examples** | NO_ACTION, WAIT, internal state change |
| **MEDIUM examples** | SEND_SMS, SEND_EMAIL, SEND_WHATSAPP |
| **HIGH examples** | SEND_PAYMENT_LINK, VOICE_CALL, WRITE_OFF, LEGAL_NOTICE |
| **On HIGH** | Requires human approval via `HumanTaskQueue` |
| **SLA** | P0 (HIGH impact, high amount) = 2-hour SLA for human response |

### Gate 8: Amount / RBAC Ceiling

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/policy_engine.py` |
| **Principle** | Autonomous actions limited to configurable amount thresholds |
| **Above ceiling** | ESCALATE to human queue with priority based on amount |
| **RBAC** | Different operator roles have different autonomous limits |

### Gate 9: Blast Radius Guard

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/blast_radius.py` |
| **Mechanism** | Global hourly rate counters per action type |
| **Anomaly detection** | Compares current ratio against historical baseline |
| **On anomaly** | CIRCUIT OPEN — all actions of that type blocked system-wide |
| **Recovery** | Manual reset or automatic after configurable cooldown |
| **Rationale** | Prevents bugs from causing mass-contact events |

### Gate 10: Contact Fatigue

| Property | Value |
|---|---|
| **Implemented in** | `app/optimizer/contact_fatigue.py` |
| **Scoring** | Cumulative score across all channels and time |
| **Factors** | Number of contacts, recency, channel mix, customer response |
| **Threshold** | When fatigue score exceeds limit → PAUSE all automation |
| **Duration** | Configurable cool-off period |
| **Rationale** | Prevents slow-burn harassment where each individual contact is policy-compliant but the aggregate is excessive |

### Gate 11: Platform Awareness

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/platform_awareness.py` |
| **Principle** | Check if Razorpay or another system already sent a notification |
| **On duplicate** | SUPPRESS — prevent sending duplicate messages |
| **Audit** | Platform action history logged |

### Gate 12: Simulation Mode

| Property | Value |
|---|---|
| **Implemented in** | `app/policy/simulation_mode.py` |
| **Mechanism** | Dry-run mode where all actions are logged but not executed |
| **Use case** | Testing new strategies, training new operators, demo mode |
| **Output** | Full `SimulationReport` showing what *would* have happened |

---

## 3. Domain-Specific Stopping Rules

### Module C: Subscription Dunning — Mandate Distinction

| Rule | Trigger | Action |
|---|---|---|
| **Customer-revoked mandate** | `error_source = "customer"` or `error_description` contains "revoked by customer" | **PERMANENT STOP** — zero actions, zero contact (RBI compliance) |
| **Bank-revoked mandate** | `error_source = "bank"` or mandate expired | Safe to send re-authentication request |
| **Retry cap** | 3 retries exhausted | Stop automated retries; issue payment link |
| **Subscription cancelled** | Customer cancelled subscription | STOP — respect cancellation |
| **Case age limit** | > 30 days from first failure | Close case as unrecovered |

### Module B: Checkout Abandonment — Anti-Spam

| Rule | Trigger | Action |
|---|---|---|
| **Single nudge cap** | 1 nudge already sent for this session | No further contact — TRAI compliance |
| **Low-value floor** | Cart total < ₹200 (configurable) | Skip recovery — negative ROI |
| **Session age** | > 72 hours since abandonment | Expire — intent has decayed |

### Module D: B2B Receivables — MSMED Compliance

| Rule | Trigger | Action |
|---|---|---|
| **Dispute halt** | Buyer contests goods/pricing/terms | FREEZE all automated outreach immediately |
| **Broken promise cap** | 3 promises broken | Bypass soft reminders → escalate to Rung 3/4 |
| **Contact frequency cap** | < 7 days since last contact | HOLD — align with corporate AP review cycles |
| **No fabricated claims** | Interest cited in notices | MUST match `msmed.py` computation exactly |
| **Human sign-off (Rung 4)** | Invoice reaches legal filing stage | DRAFT packet but HALT dispatch until human approval |

### Module E: Mandate Retry — NPCI Rules

| Rule | Trigger | Action |
|---|---|---|
| **NPCI execution window** | Retry falls in 10:00–13:00 IST | Reschedule to 13:00:01 IST |
| **Retry ceiling** | 1 original + 3 retries | No further automated retries |
| **Off-peak enforcement** | Retry outside permitted windows | HOLD until next permitted window |

---

## 4. Fail-Closed Safety Properties

| Failure Mode | System Behavior | Rationale |
|---|---|---|
| **Redis down** | Outbound contact refused | Cannot verify cooldown → default to "within cooldown" |
| **DB unreachable** | Irreversible financial action blocked | Cannot guarantee state → cannot take irreversible action |
| **Circuit breaker open** | Degraded dependency calls fast-fail | Prevents timeout cascades |
| **Duplicate webhook** | Deduped by idempotency key | Prevents double-processing |
| **UNKNOWN API response** | Route to reconciliation | Never blindly retry ambiguous outcomes |
| **Pre-action state change** | Abort outbound action | Case may have resolved during processing |

---

## 5. Measurement & Experimentation Rules

### Control Group

| Property | Value |
|---|---|
| **Split ratio** | 90% treatment / 10% control |
| **Control treatment** | NO intervention — cases in control group receive zero recovery actions |
| **Assignment** | Deterministic by case ID (reproducible, not random) |
| **Measurement** | Incremental recovery = treatment recovery − control baseline |
| **Contamination** | Control cases are never exposed to recovery actions, even accidentally |

### Counterfactual Simulation

| Property | Value |
|---|---|
| **Purpose** | "What if" analysis comparing strategies |
| **Input** | Synthetic cases + strategy definitions |
| **Output** | Per-strategy comparison: recovery, cost, CX impact |
| **Ground truth** | Synthetic labels (for evaluation) or model estimates (for projection) |

---

## 6. Audit & Explainability Rules

### Hash-Chain Audit

| Property | Value |
|---|---|
| **Storage** | Append-only PostgreSQL table |
| **Integrity** | SHA-256 hash chain — each entry includes hash of previous entry |
| **Tampering** | `verify_chain()` detects any modification to historical entries |
| **Retention** | Indefinite (compliance requirement) |

### Decision Trace

Every recovery decision produces a complete trace:

```
DecisionTrace {
    case_id: "...",
    event: { raw webhook payload },
    state: { case state at decision time },
    root_cause: RootCause.INSUFFICIENT_FUNDS,
    risk: RiskAssessment { ... },
    natural_payment_probability: 0.34,
    candidate_actions: [
        CandidateAction(SEND_SMS, score=0.87),
        CandidateAction(SEND_PAYMENT_LINK, score=0.72),
        CandidateAction(NO_ACTION, score=0.65),
    ],
    per_action_uplift: { SEND_SMS: 0.12, SEND_PAYMENT_LINK: 0.08 },
    per_action_score: { SEND_SMS: 4500, SEND_PAYMENT_LINK: 3200 },
    policy_gate: PolicyGateResult.APPROVED,
    selected_action: Action.SEND_SMS,
    rejected_reasons: {
        SEND_PAYMENT_LINK: "cooldown active (last contact 6h ago)",
        VOICE_CALL: "outside contact window"
    },
    execution_result: ExecutionResult(SUCCESS, ...),
    outcome: "SMS sent, link clicked, payment initiated"
}
```

### Prevention Log

Records what the agent **deliberately prevented**:

```
PreventionEntry {
    case_id: "...",
    prevented_action: "SEND_SMS",
    reason: "Customer on DND list since 2026-08-15",
    amount_saved_paise: 2500  // SMS cost avoided
}
```

---

## 7. Strategy YAML Schema

Each recovery strategy in `strategies/` follows this schema:

```yaml
name: strategy_identifier          # Unique key
version: "1.0"                     # Semantic version
module: module_name                # Which module owns this strategy
severity: HIGH | MEDIUM | LOW     # Default severity classification

description: >
  Free-text description of the strategy purpose and compliance context.

root_cause_signals:                # How to detect this root cause
  - error_code: "MANDATE_FAILED"
  - error_source: "emandate"

compliance:                        # Domain-specific compliance rules
  customer_revoked:
    detection: [...]
    action: PERMANENT_STOP
    rationale: "..."

timing:                            # Timing parameters
  initial_wait_hours: 4
  max_retry_attempts: 3
  retry_backoff: exponential
  contact_window_start: "09:00"
  contact_window_end: "20:00"

action_sequence:                   # Ordered recovery steps
  - step: 0
    action: ACTION_NAME
    condition: "boolean expression"
    rationale: "Why this step"
    cooldown_hours: 48

stopping_rules:                    # Hard stop conditions
  - "condition expression"

suppression_rules:                 # What to never do
  - rule: "rule_name"
    action: PERMANENT_BLOCK
    log: "Reason logged"

uplift_expectations:               # Per-segment conversion rates
  sure_thing: 0.05
  persuadable: 0.40
  lost_cause: 0.03
  sleeping_dog: -0.15

cost_model:                        # Per-action costs in paise
  sms_paise: 25
  payment_link_paise: 150
```

---

## 8. The 14-Action Expanded Set

| Action | Category | Reversibility | Typical Cost |
|---|---|---|---|
| `NO_ACTION` | Inaction | LOW | 0 |
| `WAIT` | Inaction | LOW | 0 |
| `RETRY_SAME_METHOD` | Retry | LOW | Gateway fee |
| `RETRY_ALTERNATE_METHOD` | Retry | MEDIUM | Gateway fee |
| `SEND_PAYMENT_LINK` | Outbound | HIGH | ₹1.50 |
| `SEND_SMS` | Outbound | MEDIUM | ₹0.25 |
| `SEND_EMAIL` | Outbound | MEDIUM | ₹0.10 |
| `SEND_WHATSAPP` | Outbound | MEDIUM | ₹0.50 |
| `VOICE_CALL` | Outbound | HIGH | ₹2.00 |
| `REQUEST_PAYMENT_METHOD_UPDATE` | Outbound | MEDIUM | ₹0.25 |
| `OFFER_PARTIAL_PAYMENT` | Financial | HIGH | Link cost |
| `CREATE_PTP` | Internal | LOW | 0 |
| `HUMAN_ESCALATION` | Escalation | LOW | Operator time |
| `WRITE_OFF` | Terminal | HIGH | Revenue loss |
| `BLOCK` | Terminal | HIGH | Revenue loss |

> [!NOTE]
> `NO_ACTION` and `WAIT` are **first-class decisions**, not failures. They represent the intelligent choice to not intervene when the uplift model predicts negative or zero incremental recovery.
