# RecoveryOS — AI Revenue Recovery Optimizer

Razorpay Hackathon Track 03. An event-driven agent that recovers failed/overdue
revenue by combining ML intelligence with a **policy-gated execution layer**:
the AI proposes, the Policy Engine gates, the Executor performs — never
`LLM → Razorpay API` (§1.3, §2.2).

> Authoritative design: the master plan at `docs/implementation_plan.md`
> (referenced throughout as `§`). See [`ARCHITECTURE.md`](ARCHITECTURE.md) for
> the system overview and [`INTERFACES.md`](INTERFACES.md) for the cross-track
> contract freeze.

## Features

- **Event Gateway** — HMAC-SHA256 webhook verification with secret rotation,
  replay/freshness checks, idempotent dedup (`EventInbox`), and a dead-letter
  queue (§2.3).
- **Root-Cause & Revenue-Risk** — deterministic rules + ML hybrid classifier,
  propensity and per-action uplift models (§3–§4).
- **Intervention Optimizer** — net-economic-value scoring, channel affinity,
  contact fatigue, optimal timing, budget & multi-obligation allocation (§5).
- **Policy Engine** — consent, contact windows, DND/preferences, cooldown
  (Redis, fail-closed), fraud, dispute, reversibility, blast-radius, platform
  awareness, simulation mode (§7).
- **Executor** — idempotency, circuit breaker, transactional outbox, payment
  link lifecycle, human queue (§8).
- **Reconciliation** — pre-action checks, out-of-order event precedence,
  UNKNOWN-state reconciliation (never blind retry) (§9).
- **NLP** — intent/emotion classification, Hinglish PTP extraction, prompt
  injection & wrong-person detection; Gemini-backed (§6).
- **Audit & Measurement** — append-only hash-chain audit, full decision
  traces, control group, counterfactual simulation (§10–§13).
- **Dashboard** — plain HTML + Chart.js recovery waterfall, scorecard, and an
  interactive "Attack the Agent" red-team demo (§14–§16, §17.1).

## Repository layout

```
app/
├── main.py              # FastAPI entry point: webhooks, routers, /health, /static
├── config.py            # pydantic-settings config, fail-fast on missing vars
├── contracts.py         # canonical cross-track enums/records (freeze point)
├── core/                # Obligation ledger, RecoveryCase, FSM, locks, dependency health
├── ingestion/           # Event Gateway: webhook, HMAC+rotation, dedup, batch, DLQ
├── db/                  # SQLAlchemy models, Alembic migrations, session
├── classifier/          # Root Cause Engine (rules + ML hybrid)
├── revenue_risk/        # Revenue-at-Risk Engine (propensity + uplift)
├── optimizer/           # Intervention Optimizer (economics, budget, fatigue)
├── policy/              # Policy Engine (all gates + simulation mode)
├── executor/            # Execution Layer (idempotency, breaker, outbox, links)
├── reconciliation/      # State Safety (pre-action, out-of-order, UNKNOWN)
├── nlp/                 # Communication Intelligence (intent, PTP, safety)
├── measurement/         # Experimentation (control group, counterfactual)
├── audit/               # Audit & Explainability (hash chain, decision trace)
├── health/              # Agent Health (behavioral panels)
├── modules/             # Recovery Modules (payment degradation, mandate retry, …)
├── b2b/                 # B2B Intelligence (customer profile, cashflow)
└── dashboard/           # Dashboard API + static HTML/JS + red-team demo
strategies/              # Recovery strategy library (YAML)
data/                    # Synthetic batch + generator + ground truth
tests/                   # Track test suites
```

## Prerequisites

- Python 3.11+ (project targets 3.14)
- [Docker](https://docs.docker.com/get-docker/) (for PostgreSQL 15 + Redis 7)
- [ngrok](https://ngrok.com/) (required for live Razorpay webhook testing —
  Razorpay rejects `localhost`)

## Setup

```bash
# 1. Clone the repository
git clone <this-repo> razorpay-recovery-os
cd razorpay-recovery-os

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
#    then edit .env and set at least:
#    RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET, WEBHOOK_SECRET,
#    DATABASE_URL, REDIS_URL, GEMINI_API_KEY

# 5. Start PostgreSQL + Redis
docker compose up -d

# 6. Apply database migrations (creates tables + audit hash-chain triggers)
alembic upgrade head

# 7. Run the API (dev, auto-reload)
uvicorn app.main:app --reload
```

The app is now served at http://127.0.0.1:8000, with interactive docs at
http://127.0.0.1:8000/docs.

> `app/config.py` validates required env vars at import time and fails fast
> with the list of missing ones, so a misconfigured `.env` surfaces immediately.

## Running the demo (5-minute pitch)

```bash
# 0. Make sure the API is running (step 7 above)
# 1. Load the synthetic batch so the dashboard has real numbers to aggregate
python -c "from app.ingestion.batch_loader import load_synthetic_batch; r=load_synthetic_batch(); print('loaded', len(r.records), 'records')"
```

Open the dashboard in a browser:

- **Recovery Waterfall** — http://127.0.0.1:8000/static/index.html
- **Attack the Agent** (interactive red-team demo) — http://127.0.0.1:8000/static/red_team.html

Both are served by the D.1 (`/api/dashboard/*`) and D.2 (`/api/red-team/*`)
routers. Use the demo's "Attack the Agent" page to trigger each exploit and
watch the `ATTACK → DETECTED → BLOCKED → REASON` result.

## Testing live Razorpay webhooks with ngrok

Razorpay will not send webhooks to `localhost`, so expose the app with ngrok:

```bash
# 1. With the API running, start ngrok on the same port
ngrok http 8000

# 2. Copy the https URL ngrok prints, e.g. https://abc123.ngrok.io
```

In the Razorpay Dashboard → **Webhooks**:

- Set the webhook URL to `https://abc123.ngrok.io/webhooks`
- Set the secret to the same value as `WEBHOOK_SECRET` in your `.env`
- Subscribe to `payment.captured`, `payment.failed`, `payment.authorized`,
  `invoice.paid`, and `mandate.revoked` events.

The app verifies the `X-Razorpay-Signature` (HMAC-SHA256 envelope or bare
64-hex), checks freshness/replay, dedups by event id, and routes invalid
events to the dead-letter queue. Watch the server logs for ACCEPTED /
DUPLICATE / REJECTED results. Confirm the live path end-to-end:

- `GET /health` reports PostgreSQL and Redis reachability, plus uptime.

## API endpoints (summary)

| Method | Path | Description |
|---|---|---|
| POST | `/webhooks` | Razorpay webhook ingestion (HMAC + dedup + DLQ) |
| GET | `/health` | Liveness + dependency (postgres/redis) checks |
| GET | `/api/dashboard/waterfall` | §14.1 recovery waterfall, paise |
| GET | `/api/dashboard/scorecard` | §14.2 agent scorecard |
| GET | `/api/dashboard/uplift_segments` | §14.6 segment buckets |
| GET | `/api/dashboard/contacts_avoided` | §14.4 contacts avoided |
| GET | `/api/dashboard/exception_queue` | §14.6 human-review queue |
| GET | `/api/red-team/attacks` | List of §16.1 demo attacks |
| POST | `/api/red-team/attack/{name}` | Trigger an attack; returns outcome |
| GET | `/static/index.html`, `/static/red_team.html` | HTML/Chart.js dashboards |

## Tests & linting

```bash
pytest                      # run the full suite (see §21.1)
pytest tests/ -v --tb=short # verbose output
pytest tests/test_webhook_validator.py -v   # ingestion (signature/replay/rotation)
pytest tests/test_red_team.py -v            # adversarial scenarios
ruff check app/ tests/      # lint
black --check app tests/    # formatting
```

## Manual verification

The §21.2 manual checklist is implemented across the test suites and the live
dashboard. Key checks (each backed by a dedicated test):

- Hash-chain audit integrity, decision traces per case
- Dedup of duplicate webhooks; correct final state on out-of-order events
- Invalid signature rejected; 7:01 PM action held for 8 AM
- Prompt injection classified but never executed; wrong person stops outreach
- Circuit breaker opens after N failures; UNKNOWN → reconcile, not blind retry
- Mandate-revoked-by-customer → zero actions; fraud/risk-block → zero retries

## Synthetic data

Generate/refresh the batch and held-out labels:

```bash
python data/generate_synthetic.py   # writes data/synthetic_batch.csv (+ labels)
```

Load (and validate) the batch in code:

```python
from app.ingestion.batch_loader import load_synthetic_batch
result = load_synthetic_batch()   # .records, .row_count, .errors
```

## License

Hackathon project; see the repository owner.
