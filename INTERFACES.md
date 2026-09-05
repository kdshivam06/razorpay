# RecoveryOS — Cross-Track Interfaces (Contract Freeze)

This file is the **single authoritative contract** that the ML track and the
Policy track build against. Every signature here is a stub in `app/` that
currently raises `NotImplementedError`. Tracks implement the bodies.

**Two tracks, two ownerships:**

| Track letter | Modules it owns |
|---|---|
| **ML track** | `classifier/`, `revenue_risk/`, `nlp/`, `measurement/`, `health/`, plus the ML-facing parts of `optimizer/`, `reconciliation/`, `audit/`, `modules/`, `b2b/` |
| **Policy track** | `policy/`, `executor/`, `optimizer/` (selection), `dashboard/`, plus the policy-facing parts of `audit/`, `modules/`, `b2b/`, `reconciliation/` |

> The docstring of each stubbed function/method already names its track letter
> (e.g. `TODO: ML track — see implementation_plan.md §5.1`). This file just
> groups them for convenience.

---

## General rules

1. **Monetary amounts are integer paise** unless a field is an explicit ratio,
   probability, score, or a free-form JSON amount.
2. **All data classes are frozen** (`@dataclasses.dataclass(frozen=True)`).
3. **Enums live in `app/contracts.py`** and are the single source of truth.
   Do NOT define parallel enums. Reuse: `Action`, `ContactChannel`,
   `ConversationalIntent`, `Emotion`, `HumanPriority`, `RootCause`,
   `ConsentStatus`, `ExecutionState`, `PaymentLinkState`, `SubscriptionState`,
   `PolicyGateResult`, `ProcessingStatus`, `RecoveryStopReason`,
   `ActionImpactLevel`, `RiskLevel`.
4. **Uplift segments** (`SURE_THING / PERSUADABLE / LOST_CAUSE / SLEEPING_DOG`)
   are already implemented in `app/core/recovery_case.py::UpliftSegment` — reuse.
5. Models must **never see** the hidden `true_*` ground-truth columns (§15.3).
6. `app/contracts.py` additionally defines the shared records `RiskAssessment`
   (§4.2), `CandidateAction` (§6.2), and `ConversationResult` (§10) used across
   modules below.

---

## `app/core/` — already implemented (not stubs, but referenced)

These implemented contracts are the inputs the stubs consume:

- `RecoveryCase` (`recovery_case.py`): `.state`, `.transition(target)`,
  `.total_remaining`, `.add_obligation(o)`, `.lock_recovery()`,
  `.unlock_recovery()`, `.add_communication(d)`, `.add_payment_link(d)`,
  `.add_ptp(d)`, `.add_decision(d)`, `.add_audit_event(e)`.
- `Obligation` (`obligation.py`): status transitions `record_payment`,
  `record_refund`, `mark_disputed`, `start_recovery`, `create_ptp`,
  `resume_after_ptp`, `write_off`, `block`; `.remaining_amount`,
  `.is_terminal`, `.is_recovery_locked`, `.release_recovery_lock`.
- `UpliftSegment` (`recovery_case.py`): the §5.4 enum.
- `CaseStateMachine` / `RecoveryState` (`state_machine.py`), `RecoveryLock`
  (`recovery_lock.py`), `dependency_health.py`.

---

## `app/classifier/` — Root Cause Engine (ML)

`rules_engine.py`
```python
class RulesEngine:
    def evaluate(self, case: RecoveryCase, event: dict) -> list[RuleVerdict]
    def classify_with_rules(self, case: RecoveryCase, event: dict) -> RuleVerdict
def is_terminal_failure(error_source: str, error_description: str) -> bool        # §9.3 §9.1
def mandate_revocation_direction(error_source: str, error_description: str) -> str  # CUSTOMER|BANK|UNKNOWN §9.3
def dispute_or_fraud_halt(case: RecoveryCase) -> RuleVerdict
```
`RuleVerdict = dataclass(frozen=True): name, applies, root_cause: RootCause|None, reason, priority: int = 0`

`ml_classifier.py`
```python
class ClassificationModel:
    def __init__(self, artifact_path: Path | None = None)
    def predict_proba(self, features: dict[str, Any]) -> dict[RootCause, float]
    def predict(self, features: dict[str, Any]) -> RootCause
    @property model_version -> str
def train(X: pd.DataFrame, y: pd.Series, output_dir: Path, *, booster="lightgbm", seed=42) -> ClassificationModel
```

`hybrid_classifier.py`
```python
class HybridClassifier:
    def __init__(self, rules: RulesEngine|None, ml: ClassificationModel|None)
    def classify(self, case, features: RiskFeatures, event) -> Classification
    def classify_batch(self, cases) -> list[Classification]
Classification = dataclass(frozen=True): root_cause, confidence, reasoning, sources: tuple[str,...], classifier_version
```

---

## `app/revenue_risk/` — Revenue-at-Risk Engine (ML)

`exposure_engine.py`
```python
class ExposureEngine:
    def assess(self, case) -> RiskAssessment                 # returns contracts.RiskAssessment §4.2
    def expected_natural_recovery(amount_remaining: int, natural_payment_probability: float) -> int
    def incremental_recovery_opportunity(amount_remaining: int, expected_natural_recovery: int) -> int
```

`payment_probability.py` (P(pay within horizon), §5.1)
```python
class PaymentPropensityModel:
    def __init__(self, artifact_path: Path|None)
    def propensity_curve(features) -> PropensityCurve
    def probability_within(features, hours: int) -> float
def train(...) -> PaymentPropensityModel
PropensityCurve = dataclass(frozen=True): p_pay_within_1h/6h/24h/3d/7d
```

`time_to_payment.py` (§5.2)
```python
class TimeToPaymentModel:
    def expected_days_to_payment(self, features: dict[str,float]) -> float
    def median_days_to_payment(self, features: dict[str,float]) -> float
def train(...) -> TimeToPaymentModel
```

`uplift_model.py` (§5.3 — the biggest differentiator)
```python
class IncrementalUpliftModel:
    def uplift_for(self, features: dict[str,float], action: Action) -> float
    def estimates(self, features: dict[str,float]) -> UpliftEstimates
    def best_action(self, features: dict[str,float]) -> Action
def train(X, treatment: pd.Series, outcome: pd.Series, output_dir, *, seed=42) -> IncrementalUpliftModel
UpliftEstimates = dataclass(frozen=True): baseline_natural_probability: float,
    per_action_probability: dict[Action,float], per_action_uplift: dict[Action,float]
```

`recovery_opportunity.py`
```python
class RecoveryOpportunityEngine:
    def opportunity_per_action(amount_remaining: int, per_action_uplift: dict[Action,float]) -> dict[Action,float]
    def best_incremental_action(amount_remaining: int, per_action_uplift: dict[Action,float]) -> tuple[Action,float]
def opportunity_from_candidate(candidate: CandidateAction) -> float
```

`risk_features.py`
```python
class FeatureBuilder:
    def build(self, case: RecoveryCase, raw_event: dict) -> RiskFeatures
    def to_frame(self, cases: list[RiskFeatures]) -> pd.DataFrame
RiskFeatures = dataclass(frozen=True): amount_paise, failure_reason, payment_method, bank,
    hour_of_day, day_of_week, day_of_month, days_overdue, previous_retry_success,
    previous_dunning_response, ptp_history_count, customer_segment, recent_activity_ts
```
> NOTE: `RiskFeatures` lives in `revenue_risk/risk_features.py` (per §18).
> The classifier imports it from there. Do not move it.

---

## `app/optimizer/` — Intervention Optimizer (ML + Policy)

`intervention_optimizer.py` — **Policy track owns selection** (§6.1, §6.2, §6.9)
```python
class InterventionOptimizer:
    def optimize(self, case: RecoveryCase, estimates: UpliftEstimates) -> OptimizationRecommendation
    def build_candidates(self, case, estimates) -> list[CandidateAction]
    def select_best(self, candidates) -> CandidateAction
    def financial_stop_check(best, amount_remaining, minimum_incremental_paise) -> RecoveryStopReason|None
```

`channel_affinity.py` — **ML** (§5.6)
```python
class ChannelAffinityModel:
    def affinity_for(self, case) -> ChannelAffinity
    def update_from_outcome(self, case_id, channel: ContactChannel, success: bool)
class ChannelAffinityStore:
    def record(self, case_id, channel, clicked, opened, latency_s)
ChannelAffinity = dataclass(frozen=True): per_channel_conversion: dict[ContactChannel,float], preferred_channel: ContactChannel|None
```

`optimal_timing.py` — **ML** (§5.5, §9.5)
```python
class OptimalTimingModel:
    def recommend(self, features: dict[str,float], now: datetime) -> TimingRecommendation
class SalaryDayHeuristic:
    def compute(self, customer_id: str, salary_day: int|None) -> int
TimingRecommendation = dataclass(frozen=True): best_send_at, retry_horizon_hours, predicted_conversion
```

`recovery_economics.py` — **ML** (§6.2)
```python
class RecoveryEconomics:
    def net_economic_value(expected_recovery, communication_cost, operational_cost, risk_penalty, cx_penalty) -> int
    def score_candidates(self, estimates: UpliftEstimates, amount_remaining_paise: int) -> list[CandidateAction]
class CommunicationCostTable:
    def cost_paise(self, channel: str) -> int
```

`recovery_budget.py` — **ML** (§6.6)
```python
class RecoveryBudgetOptimizer:
    def allocate(self, cases, daily_budget_paise, expected_incremental_recovery_paise, projected_cost_paise) -> BudgetAllocation
```

`multi_obligation.py` — **ML** (§6.7, §6.8)
```python
class MultiObligationOptimizer:
    def optimize_customer(self, customer_cases: list[RecoveryCase], max_contacts_per_day: int) -> CustomerPortfolioDecision
class CustomerContactBudget:
    def remaining_today(customer_id) -> int
    def consume(customer_id, amount=1) -> bool
```

`action_suppression.py` — **ML** (§6.5)
```python
class ActionSuppressor:
    def check(self, case: RecoveryCase, action: Action) -> SuppressionVerdict
    def suppressed_actions(self, case) -> list[str]
```

`contact_fatigue.py` — **Policy** (§6.3)
```python
class ContactFatigueEngine:
    def score(self, features: dict[str,float]) -> FatigueAssessment
    def should_pause_automation(self, assessment) -> bool
FatigueAssessment = dataclass(frozen=True): score, level: ContactFatigue, reasons: tuple[str,...]
```

---

## `app/policy/` — Policy Engine (Policy)

`policy_engine.py` (§7.1, §7.7)
```python
class PolicyEngine:
    def evaluate(self, case: RecoveryCase, action: Action) -> PolicyEvaluation
    def evaluate_autonomy(self, case, action) -> PolicyEvaluation
    def human_approval_required(self, case, action) -> bool
```

`contact_policy.py` (§7.2)
```python
class ContactPolicyEngine:
    def load_from_yaml(self, path: str) -> None
    def is_contact_allowed(self, channel: Channel, at: datetime, customer_preference: tuple[str,...]) -> bool
class ContactWindowEvaluator:
    def allowed(self, channel: Channel, at: datetime) -> bool
```

`consent_manager.py` (§7.3)
```python
class ConsentManager:
    def get_eligibility(customer_id, channel, purpose) -> CommunicationEligibility
    def record_consent(customer_id, channel, purpose, timestamp)
    def revoke_consent(customer_id, channel, purpose, timestamp)
```

`customer_preferences.py` (§7.4)
```python
class CustomerPreferenceEngine:
    def load_preferences(customer_id) -> ContactPreference
    def can_contact(customer_id, channel, at) -> bool
    def allowed_window(customer_id, channel, at) -> tuple[datetime, datetime]
```

`cooldown_manager.py` (§7.1) — `redis` import is optional-guarded
```python
class CooldownManager:
    def __init__(self, client: redis.Redis|None = None)
    def within_cooldown(customer_id, channel) -> bool
    def touch(customer_id, channel, ttl_seconds)
    def backoff_seconds(customer_id, channel) -> int
```

`fraud_detector.py` (Policy)
```python
class FraudDetector:
    def assess(self, features: dict[str,float]) -> FraudVerdict
    def assert_not_fraudulent(self, fraud_score: float, threshold: float) -> None
```

`blast_radius.py` (§7.5)
```python
class BlastRadiusGuard:
    def check_global_rate(self, action: Action) -> BlastRadiusStatus
    def detect_anomaly(self, current_ratio, baseline_ratio, threshold) -> bool
    def open_circuit(self) -> None
```

`reversibility.py` (§6.4)
```python
class ReversibilityScorer:
    def impact_level(self, action: Action) -> ActionImpactLevel
    def requires_human_approval(self, action: Action) -> bool
```

`platform_awareness.py` (§7.6)
```python
class PlatformAwareness:
    def history_for(self, customer_id: str) -> PlatformActionHistory
    def should_duplicate_sms(self, history: PlatformActionHistory) -> bool
```

`simulation_mode.py` (§7.8)
```python
class SimulationMode:
    def simulate(self, cases: list[dict], strategy_name: str, *, execute_after: bool = False) -> SimulationReport
```

---

## `app/executor/` — Execution Layer (Policy)

`action_executor.py` — `ExecutionResult(action, state, idempotency_key, external_ref, detail)`
```python
class ActionExecutor:
    def execute(self, case: RecoveryCase, action: Action, payload: dict) -> ExecutionResult
    def execute_noop(self, case: RecoveryCase) -> ExecutionResult
```

`transactional_outbox.py` — `OutboxCommand(command_id, case_id, action, payload, created_at_ts)`
```python
class TransactionalOutbox:
    def append(self, command: OutboxCommand)
    def claim_next(self) -> OutboxCommand|None
    def worker_loop_tick(self) -> int
```

`payment_link.py` — `PaymentLinkRecord(link_id, state: PaymentLinkState, amount_paid_paise, amount_outstanding_paise, expires_at_ts)`
```python
class PaymentLinkLifecycle:
    def resolve_outstanding(self, case_id, invoice_amount_paise) -> int
    def create_or_reuse(self, case_id) -> PaymentLinkRecord
    def cancel_active_link(self, case_id)
    def get(self, link_id) -> PaymentLinkRecord
```

`notification.py` — `NotificationRecord(notification_id, channel, template_key, rendered_body, state)`
```python
class NotificationSender:
    def send(self, channel, template_key, variables: dict, note="") -> NotificationRecord
```

`subscription_ops.py` — `SubscriptionOpResult(subscription_id, new_state, execution_state, detail)`
```python
class SubscriptionOps:
    def pause(self, subscription_id) -> SubscriptionOpResult
    def resume(self, subscription_id) -> SubscriptionOpResult
    def cancel(self, subscription_id) -> SubscriptionOpResult
```

`circuit_breaker.py`
```python
class CircuitBreaker:
    def allow(self, dependency: str) -> bool
    def record_success(self, dependency: str)
    def record_failure(self, dependency: str)
    def state(self, dependency: str) -> CircuitState
```

`idempotency.py`
```python
class IdempotencyManager:
    def new_key(self, scope: str) -> str
    def check(self, key: str) -> bool
    def release(self, key: str)
```

`human_queue.py` — `HumanTask(task_id, case_id, priority, action, proposed_reasoning, sla_minutes)`
```python
class HumanTaskQueue:
    def enqueue(self, task: HumanTask)
    def next_highest_priority(self) -> HumanTask|None
    def approve(self, task_id)
    def reject(self, task_id, reason: str)
def priority_for(case_id: str) -> int
```

`voice_agent.py` — `MockTelephonyClient`, `VoiceRecovery` (§9.6)
```python
class MockTelephonyClient:
    def place_call(self, phone: str, script: dict, waited_s: int = 0) -> str
class VoiceRecovery:
    def handle_call(self, call_id: str, transcript: str, emotion: Emotion|None = None) -> VoiceCallOutcome
```

---

## `app/reconciliation/` — State Safety (ML)

`reconciliation_engine.py` — `Mismatch`, `ReconciliationReport`
```python
class ReconciliationEngine:
    def reconcile(self, since_ts: float|None = None) -> ReconciliationReport
    def repair_safe_transition(self, mismatch: Mismatch)
```

`pre_action_check.py` — §8.2 strongest demo feature
```python
class PreActionReconciler:
    def check(self, case: RecoveryCase, action: Action) -> PreActionCheck
```

`unknown_state_handler.py` — §8.3/§11.3
```python
class UnknownStateHandler:
    def resolve_unknown(self, case_id: str, action: Action) -> UnknownResolution
```

`out_of_order.py` — §11.2
```python
class OutOfOrderGuard:
    def apply(self, current: EventState|None, incoming: StateVersion) -> EventState
```

---

## `app/nlp/` — Communication Intelligence (ML)

`ptp_extractor.py`
```python
class PtpExtractor:
    def extract(self, text: str, language: str = "hinglish") -> PtpExtraction
PtpExtraction = dataclass(frozen=True): intent, promised_date: date|None, amount_paise: int|None, currency, confidence
```

`ptp_reliability.py`
```python
class PtpReliabilityScorer:
    def score(self, promises: int, fulfilled: int) -> ReliabilityAssessment
```

`intent_classifier.py`
```python
class IntentClassifier:
    def classify(self, text: str, language: str = "hinglish") -> IntentResult
```

`emotion_detector.py`
```python
class EmotionDetector:
    def detect(self, text: str, language: str = "hinglish") -> EmotionResult
```

`prompt_injection.py`
```python
class PromptInjectionGuard:
    def assess(self, text: str) -> InjectionAssessment
```

`wrong_person.py`
```python
class WrongPersonProtector:
    def assess(self, text: str) -> IdentityAssessment
```

`message_templates.py`
```python
class TemplateEngine:
    def render(self, template_key: str, variables: dict, locale: str = "en") -> RenderedTemplate
    def has_template(self, template_key: str) -> bool
```

> The voice agent and the PTP tracker communicate via
> `contracts.ConversationResult(intent, emotion, opt_out_intent, ptp, confidence, raw_text)`.

---

## `app/measurement/` — Experimentation (ML)

`control_group.py` — `Assignment(case_id, group)`; §12.1 90/10 split
```python
class ControlGroup:
    def assign(self, case_id: str) -> Assignment
    def split(self, case_ids: list[str], treatment_ratio: float = 0.9) -> tuple[list[str], list[str]]
```

`experiment_engine.py` — `Experiment` (§12.2)
```python
class ExperimentEngine:
    def create(self, experiment: Experiment) -> str
    def redeem(self, experiment_id: str, metric: str, value: float)
    def should_stop(self, experiment_id: str) -> bool
```

`counterfactual_sim.py` (§12.3)
```python
class CounterfactualSimulator:
    def run(self, strategies: dict[str,dict], synthetic_cases: list[dict], outcome_model="ground_truth") -> StrategyComparison
```

`model_evaluation.py` (§12.4) — evaluate predicted vs hidden true ground truth
```python
class ModelEvaluator:
    def classification(self, y_true, y_pred) -> ModelMetrics
    def probability(self, y_true, y_score) -> ModelMetrics
    def uplift(self, y_true, uplift_scores) -> UpliftMetrics
```

`drift_detector.py` (§12.5)
```python
class DriftDetector:
    def check(self, baseline: float, current: float, threshold: float) -> DriftSignal
```

---

## `app/audit/` — Audit & Explainability (ML + Policy)

`audit_logger.py` — §13.1/§13.2/§13.6, append-only + hash chain
```python
class AuditLogger:
    def append(self, *, trigger_type, trigger_event, payload, case_id, obligation_id, customer_id,
               transaction_id, action, **attrs) -> AuditEntry
    def verify_chain(self) -> list[str]
```

`decision_trace.py` — §13.3/§13.4
```python
class DecisionTracer:
    def build(self, **kwargs) -> DecisionTrace
    def why_not(self, case_id: str, chosen: Action) -> dict[Action, str]
DecisionTrace = dataclass(frozen=True): case_id, event, state, root_cause, risk, natural_payment_probability,
    candidate_actions, per_action_uplift, per_action_score, policy_gate, selected_action,
    rejected_reasons, execution_result, outcome
```

`prevention_log.py` — §13.5 'what the agent prevented'
```python
class PreventionLog:
    def log(self, case_id, prevented_action: str, reason: str, amount_saved_paise: int|None = None)
    def summary(self) -> dict[str, object]
```

`pii_masker.py` (**Policy**)
```python
class PiiMasker:
    def mask_phone(self, value: str) -> str
    def mask_email(self, value: str) -> str
    def mask_text(self, text: str) -> str
```

`version_tracker.py` — §12.6
```python
class VersionTracker:
    def current(self) -> VersionSnapshot
    def snapshot_for_decision(self, **overrides: str) -> VersionSnapshot
```

---

## `app/health/` — Agent Health (ML)

`agent_monitor.py`
```python
class AgentMonitor:
    def report(self) -> AgentHealthReport
    def record_classification(self, root_cause: str)
    def record_action(self, action: str)
```

`anomaly_detector.py`
```python
class AnomalyDetector:
    def detect(self, signals: dict[str,dict]) -> list[Anomaly]
```

---

## `app/modules/` — Recovery Modules (ML + Policy)

`payment_degradation.py` — **Module A** (§9.1)
```python
class PaymentDegradationModule:
    def on_downtime_start(self, instrument: str, severity: str) -> DegradationDecision
    def on_downtime_resolve(self, instrument: str) -> DegradationDecision
```

`checkout_abandonment.py` — **Module B** (§9.2)
```python
class CheckoutAbandonmentModule:
    def detect(self, session_event: dict) -> CheckoutAbandonmentCase
```

`subscription_dunning.py` — **Module C** (§9.3) — customer-revoked mandate → PERMANENT STOP
```python
class SubscriptionDunningModule:
    def dunning_step(self, subscription_id: str, state: SubscriptionState) -> DunningDecision
```

`b2b_receivables.py` — **Module D** (§9.4)
```python
class B2BReceivablesModule:
    def prioritize(self, invoices: list[dict]) -> list[B2BCollectionDecision]
    def top_workload(self, invoices: list[dict], limit: int = 20) -> list[str]
```

`mandate_retry.py` — **Module E** (§9.5)
```python
class MandateRetryModule:
    def schedule(self, sub_id: str, customer_id: str) -> RetrySequence
```

`voice_recovery.py` — **Module F** (§9.6)
```python
class VoiceRecoveryModule:
    def handle_call(self, call_id: str, transcript: str, emotion: Emotion|None = None) -> VoiceCallResult
```

`promise_to_pay.py` — **Module G** (§9.7, §10.2, §10.3)
```python
class PromiseToPayTracker:
    def register(self, customer_id, extraction: PtpExtraction, reliability: ReliabilityAssessment) -> PtpTrackerEntry
    def honour_or_escalate(self, customer_id: str) -> str
```

---

## `app/b2b/` — B2B Intelligence (ML + Policy)

`customer_profile.py` (ML)
```python
class CustomerProfile:
    def profile_for(self, client_id: str) -> B2BCustomerProfile
```

`cashflow_forecast.py` (ML)
```python
class CashflowForecaster:
    def forecast(self, client_ids: list[str]) -> CashflowForecast
```

`collection_priority.py` (ML)
```python
class CollectionPrioritizer:
    def rank(self, items: list[CollectionItem]) -> list[CollectionItem]
```

`workload_optimizer.py` (Policy)
```python
class WorkloadOptimizer:
    def plan(self, cases: list[dict], collector_ids: list[str], limit: int = 20) -> list[Workload]
```

---

## `app/dashboard/` — Dashboard (Policy)

`api.py` (§14.1–14.6)
```python
class DashboardApi:
    def waterfall(self) -> Waterfall
    def scorecard(self) -> dict[str, object]
    def decision_trace(self, case_id: str) -> DecisionTrace
```

`red_team_api.py` (§16.1)
```python
class RedTeamApi:
    def run_attack(self, attack_name: str, payload: dict) -> RedTeamOutcome
```
Track A done. 58 passed (full pytest tests/ -v output pasted above), black reformatted 102 files, ruff check . all clean, committed as 58ce099. Working tree clean.
Implemented vs stubbed
Fully implemented (tested):
- Core — Obligation ledger with state transitions, double-dip prevention, recovery lock; RecoveryCase + custom 18-state FSM (incl. UNKNOWN→RECONCILE); dependency health.
- Config & env — pydantic-settings, fail-fast validation.
- DB — SQLAlchemy 2.0 models (RecoveryCase, Obligation, AuditLog §13.6) + Alembic migration with append-only and hash-chain triggers (validated offline — live DB untested, Docker/Postgres creds unavailable).
- Ingestion/Event Gateway — bulletproof HMAC-SHA256 + secret rotation, replay/freshness checks, dedup inbox, DLQ, batch loader, FastAPI POST /webhooks.
- Docs — README setup, ARCHITECTURE (three-layer), INTERFACES (contract freeze), vendored master plan.
Stubbed (60 modules, NotImplementedError pending Track B/C): classifier, revenue_risk, optimizer, policy, executor, reconciliation, nlp, measurement, audit, health, modules, b2b, dashboard — all with full type-hinted signatures and cross-track vocabulary in app/contracts.py.
Things I'm least sure about in INTERFACES.md
1. Track ownership split — the plan never names tracks, so the ML/Policy boundary (e.g. optimizer/ selection → Policy, reconciliation/ → ML, audit/ notably split both ways) is my judgment. Flippable if tracks disagree.
2. OptimizationRecommendation vs a single decision shape — I left selected_action + candidates + stop reasons in one frozen record; tracks may want a leaner or richer decision bundle.
3. Monetary fields typed int (paise) — I standardized on paise; §4.2 display numbers read like rupees, so teams must confirm convention with DB amount_paise columns.
4. Enums I invented beyond the plan's boxes — Emotion, ContactFatigue, RiskLevel, ProcessingStatus aren't spelled out verbatim in the plan; treat as suggestions, not gospel.