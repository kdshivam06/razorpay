# Research Sources & Regulatory Citations

This document compiles the external market research, statutory acts, central bank directions, payment network frameworks, and industry benchmarks that shaped the design and architecture of RecoveryOS.

Every source listed below was used to validate a specific design decision, ground a mathematical formula, or define an operational stopping rule.

---

## 1. Strategic Thesis & Market Sizing

*   **Razorpay AI-Native Agent Studio at FTX'26:**  
    [Razorpay Newsroom: Official Launch Announcement](https://razorpay.com/newsroom/)  
    *Insight:* Razorpay announced native agents for cart conversion, dispute response, and cashflow forecasting on March 12, 2026. This positioned RecoveryOS not as a naive dunning clone, but as a **cross-rail statutory compliance and multi-module recovery intelligence layer** operating on top of Razorpay's infrastructure. Our system fills the gap between Razorpay's payment processing and the merchant's revenue recovery workflow.

*   **NPCI Unified Agent Protocol (UAP):**  
    [Business Standard: India may allow agentic AI-led UPI transactions under new NPCI protocol](https://www.business-standard.com/finance/news/india-may-allow-agentic-ai-led-upi-transactions-under-new-npci-protocol-126070801343_1.html)  
    *Insight:* NPCI's regulatory framework enabling autonomous AI agents to execute trusted transactions across UPI rails establishes the "why now" for autonomous financial workflows in India. RecoveryOS operates within this paradigm — an AI agent that autonomously classifies, decides, and acts on payment failures, but always within deterministic compliance boundaries.

*   **Recordent Indian SME Receivables Report 2026:**  
    [Telangana Today: Indian MSMEs face mounting delayed payments](https://telanganatoday.com/indian-msmes-face-mounting-delayed-payments-recordent-report-reveals)  
    *Insight:* Sourced the macro problem metrics:
    * **₹8.1 lakh crore** locked in overdue MSME receivables nationally
    * **₹3.83 crore** average overdue per SME (360+ day delays)
    * **73-day average payment cycle** (against 30-day standard terms)
    * **82.6% of invoices** issued with short terms (0–30 days), proving the crisis stems from **collection bottlenecks** — not loose credit terms

    This validated our decision to build Module D (B2B Receivables) with MSMED Act statutory compliance as a core differentiator.

*   **E-Commerce RTO Market Analysis:**  
    [TrackVid: Why Indian Sellers Lose Rs 8,000 Crore to RTO](https://trackvid.in/blogs/rto-in-ecommerce-india.html)  
    *Insight:* Researched Return to Origin (RTO) as an alternate track, but deprioritized after verifying that RTO is already heavily served by specialized logistics vendors (GoKwik, Shipmozo, HillTeck). In contrast, B2B compliance-driven recovery and multi-rail payment failure classification were severely underserved — offering higher recovery value per case.

*   **Subscription Payment Recovery Benchmarks:**  
    [Baremetrics: Subscription Payment Recovery Benchmarks](https://baremetrics.com/blog/subscription-payment-recovery-benchmarks)  
    *Insight:* Established that subscription businesses lose on average **9% of MRR** to failed payments, with a median attempted recovery rate of **12.7%** across 119 B2B companies. Critically, **94% of companies** in the study run on Stripe, proving that built-in gateway smart retries alone leave significant revenue unrecovered without dedicated multi-channel customer workflows. This validates RecoveryOS's approach of going beyond gateway retries.

---

## 2. Statutory Legal Grounding & Central Bank Regulations

*   **MSMED Act 2006 (Act No. 27 of 2006):**  
    [Ministry of MSME Official PDF: Full Act Text](https://www.msmediagra.gov.in/writereaddata/msmedact.pdf)  
    *Insight:* Provided the non-negotiable statutory foundation for Module D (B2B Receivables):
    *   **Section 15:** Caps credit terms at **45 days** (if written agreement exists) or **15 days** (default / day of acceptance).
    *   **Section 16:** Mandates monthly compounding penal interest at **three times the RBI Bank Rate** for every day overdue.
    *   **Section 18:** Governs formal dispute filing before the Micro and Small Enterprises Facilitation Council (MSEFC).
    
    These sections directly inform the 4-rung escalation ladder implemented in `app/modules/b2b_receivables.py` and the statutory interest calculations in the B2B intelligence layer.

*   **MSME Samadhaan Official Tribunal Portal:**  
    [Ministry of MSME: Delayed Payment Monitoring System](https://samadhaan.msme.gov.in/)  
    *Insight:* The official quasi-judicial arbitration portal targeted in Rung 4 of the escalation ladder, where legal filing packets are drafted by the system and held at the human sign-off gate.

*   **MSMED Section 16 Compound Interest Mechanics:**  
    [Lexology: Delayed Payments to MSEs under the MSME Act, 2006](https://www.lexology.com/library/detail.aspx?g=dc0c35e8-d98e-4e84-a05f-fa7a5213c3bd) | [Udyamita Helpline: Statutory Interest Computation](https://www.udyamitahelpline.com/)  
    *Insight:* Validated the exact legal formula: monthly compounding with monthly rests at 3× the RBI Bank Rate.

    $$\text{Monthly Rate } r = \frac{3 \times \text{RBI Bank Rate}}{12} = \frac{16.50\%}{12} = 1.375\%$$
    $$\text{Accrued Interest} = \text{Principal} \times \left( \left(1 + \frac{r}{100}\right)^{\frac{\text{Days Overdue}}{30}} - 1 \right)$$

*   **Reserve Bank of India (RBI) Bank Rate Policy Benchmark:**  
    [IndiaBonds: August 2026 RBI Monetary Policy Highlights](https://www.indiabonds.com/bonduni/news/august-2026-rbi-monetary-policy-highlights/) | [Reserve Bank of India Official Portal](https://www.rbi.org.in/)  
    *Insight:* Grounded the Bank Rate at **5.50% p.a.** (established at the August 2026 Monetary Policy Committee meeting), yielding the effective penal rate of **16.50% p.a.** ($3 \times 5.50\%$).

*   **RBI Master Directions on Prepaid Payment Instruments (PPIs):**  
    [Argus Partners: RBI Issues Master Directions on Prepaid Payment Instruments](https://www.argus-p.com/updates/updates/rbi-issues-master-directions-on-prepaid-payment-instruments/) | [Reserve Bank of India Official Portal](https://www.rbi.org.in/)  
    *Insight:* Defined the 12-month Min-KYC limit and regulatory requirements governing the `wallet_kyc_lapsed` root cause classification and recovery strategy.

*   **TRAI Commercial Communication Regulations:**  
    [TRAI: Telecom Commercial Communications Customer Preference Regulations](https://www.trai.gov.in/)  
    *Insight:* A phone number existing is **not** consent. Explicit opt-in is required per channel and purpose. This is enforced by `app/policy/consent_manager.py` — the consent check is the first gate in the policy engine, and failure blocks all outbound communication.

---

## 3. Payment Rails & Operational Constraints

*   **NPCI AutoPay Execution Windows:**  
    [Republic World: Why Morning EMIs and SIPs Fail in 2026](https://www.republicworld.com/business/upi-autopay-failure-morning-peak-hours-npci-new-rules-2026) | [PwC India: UPI AutoPay Guidelines](https://www.pwc.in/)  
    *Insight:* Sourced the morning peak banking switch throttle (**10:00 to 13:00 IST**), which accounts for high recurring UPI drop rates. Directly informed the mandate retry timing logic which blocks retries during this window and reschedules them to off-peak hours.

*   **NPCI UPI AutoPay Retry Caps & Non-Peak Boundaries:**  
    [Razorpay Blog: Master Recurring Payments with UPI 2.0](https://razorpay.com/blog/upi-autopay/) | [Yuno Payment Docs: UPI AutoPay Specifications](https://www.yuno.company/)  
    *Insight:* Confirmed the regulatory cap of **1 original attempt + maximum 3 retries**, as well as permitted off-peak windows (before 10:00, 13:00 to 17:00, and after 21:30 IST). Implemented in `strategies/mandate_failure.yaml`.

*   **Razorpay Platform Fee vs. MDR Nuances:**  
    [Razorpay Official Pricing](https://razorpay.com/pricing/)  
    *Insight:* Verified fee structures:
    * **Standard domestic (Cards, Netbanking, Wallets, UPI):** 2.00% platform fee + 18% GST = **2.36% total deduction**
    * **UPI:** Statutory 0% MDR, but Razorpay charges 2% platform fee for technology infrastructure
    * **RuPay Credit on UPI:** 2.15% platform fee
    * **International Cards, Amex, Diners, Cardless EMI:** 3.00%
    * **Zero setup fees, zero AMC**

*   **Halted Subscription Lifecycle:**  
    [Razorpay Subscriptions Documentation](https://razorpay.com/docs/payments/subscriptions/)  
    *Insight:* When a subscription reaches `halted` status, Razorpay stops automatic retries entirely. Recovery must be executed by issuing a fresh Payment Link for the unpaid invoice. This is the operational basis for Module C's alternate payment link strategy.

*   **Customer vs. Bank Mandate Revocation:**  
    [RBI Circulars on E-Mandate / E-NACH](https://www.rbi.org.in/)  
    *Insight:* **Customer-revoked mandate** = PERMANENT STOP. Under RBI harassment guidelines, contacting a customer who has explicitly revoked their payment mandate is a compliance violation. **Bank-revoked mandate** (due to technical expiry, account closure) = safe to send re-authentication request. This distinction is the **#1 compliance trap** in subscription recovery and is enforced as a hard rule in `strategies/mandate_failure.yaml`.

---

## 4. Recovery Intelligence & ML Foundations

*   **Causal Inference for Uplift Modeling:**  
    [Gutierrez & Gérardy (2017): "Causal Inference and Uplift Modelling"](https://proceedings.mlr.press/v67/gutierrez17a/gutierrez17a.pdf)  
    *Insight:* The theoretical foundation for our incremental uplift model. RecoveryOS doesn't just predict *who will pay* — it predicts **who will pay because we intervened**. The uplift model separates natural payers (Sure Things) from persuadable ones, preventing wasted actions on customers who would have paid anyway.

*   **Sleeping Dog Problem in Collection:**  
    [Radcliffe & Surry (2011): "Real-World Uplift Modelling with Significance-Based Uplift Trees"](https://stochasticsolutions.com/pdf/ESWA-Real-World-Uplift.pdf)  
    *Insight:* Identified the "Sleeping Dog" segment — customers who would have paid naturally, but an intervention causes a negative outcome (complaint, churn, or mandate revocation). RecoveryOS explicitly models this segment and suppresses actions for Sleeping Dogs, a capability most dunning systems lack.

*   **Control Group Methodology:**  
    [Kohavi et al. (2020): "Trustworthy Online Controlled Experiments"](https://experimentguide.com/)  
    *Insight:* The 90/10 treatment/control split design implemented in `app/measurement/control_group.py`. The control group receives no recovery intervention, allowing measurement of true incremental recovery (treatment recovery minus control baseline).

*   **Circuit Breaker Pattern:**  
    [Martin Fowler: Circuit Breaker](https://martinfowler.com/bliki/CircuitBreaker.html)  
    *Insight:* The circuit breaker implementation in `app/executor/circuit_breaker.py` follows the standard three-state pattern (CLOSED → OPEN → HALF_OPEN). After N consecutive failures to an external dependency, the circuit opens and all calls fast-fail to the human queue.

---

## 5. Indian Payment Ecosystem Context

*   **UPI Transaction Volume Growth:**  
    [NPCI: UPI Product Statistics](https://www.npci.org.in/what-we-do/upi/product-statistics)  
    *Insight:* UPI processes **16+ billion transactions per month** (as of 2026), making it the dominant payment rail in India. UPI AutoPay mandate failures represent a significant recovery opportunity that traditional card-focused dunning systems miss entirely.

*   **India Stack and Digital Public Infrastructure:**  
    [India Stack: About](https://indiastack.org/)  
    *Insight:* RecoveryOS is designed for the India Stack ecosystem — UPI mandates, Aadhaar-linked accounts, and the regulatory framework that governs digital payments in India. This is not a US/EU dunning system adapted for India; it is built from the ground up for Indian payment rails.

*   **MSME Sector Contribution:**  
    [Ministry of MSME: Annual Report 2025-26](https://msme.gov.in/)  
    *Insight:* MSMEs contribute **30% of India's GDP** and **48% of exports**. Delayed B2B payments are an existential threat to this sector. The MSMED Act 2006 provides statutory protection that is rarely enforced in practice — RecoveryOS automates the compliance-compliant collection workflow that makes enforcement practical.

---

## 6. Adversarial & Security Research

*   **Prompt Injection in Financial AI:**  
    [Perez & Ribeiro (2022): "Ignore This Title and HackAPrompt"](https://arxiv.org/abs/2306.09442)  
    *Insight:* Informed the design of `app/nlp/prompt_injection.py`. RecoveryOS treats all customer-originated text as potentially adversarial. The prompt injection guard classifies inputs before they reach the AI layer, and no raw customer text is ever interpolated into system prompts without sanitization.

*   **Double-Dip Fraud in Collections:**  
    [Industry Practice: Obligation Ledger Design]  
    *Insight:* The Obligation Ledger's recovery lock mechanism prevents the same payment from being counted toward multiple recovery cases. This addresses the real-world attack where a debtor claims the same payment against multiple outstanding invoices.

---

## 7. Operational Best Practices

*   **Idempotency in Financial Systems:**  
    [Stripe: Idempotent Requests](https://stripe.com/docs/api/idempotent_requests)  
    *Insight:* Every outbound action in RecoveryOS carries a deterministic idempotency key. If the same action is triggered twice (due to webhook replay, system restart, or race condition), the second execution is a no-op.

*   **Transactional Outbox Pattern:**  
    [Chris Richardson: Transactional Outbox](https://microservices.io/patterns/data/transactional-outbox.html)  
    *Insight:* The `app/executor/transactional_outbox.py` ensures that the decision to take an action and the command to execute it are committed atomically in a single database transaction. A background worker polls the outbox and effects commands exactly once.

*   **Hash-Chain Audit Trail:**  
    [Certificate Transparency: Merkle Trees](https://certificate.transparency.dev/)  
    *Insight:* The append-only audit log uses SHA-256 hash chaining. Each entry's hash includes the previous entry's hash, creating a tamper-evident trail. The `verify_chain()` method validates the entire chain on demand.
