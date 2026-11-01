<div align="center">

# DealIX

### Make room for what matters.

**A personal finance agent that looks beyond your balance to find a safer way forward.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Container-Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Currency](https://img.shields.io/badge/Currency-INR%20%E2%82%B9-3D6848)](#)

</div>

DealIX answers **“Can I afford this?”** with a personalized forecast—not just a glance at your current balance. Enter your income, bills, pending payments, reserve, priorities, and a purchase to get a practical recommendation and dated payment plan.

## Why DealIX

- **Personalized decisions:** account for risk tolerance, income stability, payment preferences, goals, purchase priority, and willingness to adjust flexible spending.
- **Forecast the full plan:** test payments against known bills, recurring costs, expected income, and the user's minimum balance throughout the schedule.
- **Actionable options:** recommend paying in full, paying partially, using installments, waiting, or not proceeding.
- **Explainable output:** receive the safe amount today, payment schedule, earliest safe full-payment date, possible spending changes, and a plain-language explanation.
- **Optional AI extraction:** analyze user messages and bill images for financial context when an Anthropic API key is configured. The affordability calculation itself remains deterministic.
- **Interactive INR experience:** responsive browser interface with ₹ inputs and Indian-number-format results.

## Run locally with Docker

Prerequisites: Docker Desktop or Docker Engine with Docker Compose.

```powershell
git clone https://github.com/Rishabh2815/DealIX.git
cd DealIX
docker compose up --pull always --build -d
```

Open **http://localhost:8000**. Interactive API docs are at **http://localhost:8000/docs**, and the health endpoint is **http://localhost:8000/health**.

Stop the service:

```powershell
docker compose down
```

## Run locally with Python

Python 3.11 or newer is required. From the repository root:

```powershell
python -m pip install -e ".[test]"
uvicorn dealix.api:app --reload --app-dir src
```

Open **http://localhost:8000**. Run the test suite with:

```powershell
python -m pytest
```

## Optional message and image analysis

Structured affordability checks work without an LLM key. To enable message/image extraction and AI-generated explanations, provide `ANTHROPIC_API_KEY` to the service. For Compose, create a local `.env` file in the repository root:

```dotenv
ANTHROPIC_API_KEY=your-key
```

Then start or recreate the service:

```powershell
docker compose up --pull always --build -d
```

Never commit secrets or bake them into an image. The `.env` file is ignored by Git.

## API example

`POST /decide` accepts a user profile, financial state, and expense request. Amounts are numeric and interpreted in the user's currency; the browser UI displays INR.

```json
{
  "profile": {
    "user_id": "demo-user",
    "risk_tolerance": "moderate",
    "payment_preference": "ok_with_installments",
    "income_stability": "fixed_salary",
    "flexible_expense_willingness": 0.5,
    "preferred_minimum_balance": 15000,
    "active_financial_goals": []
  },
  "state": {
    "current_balance": 60000,
    "confirmed_monthly_income": 60000,
    "recurring_monthly_expenses": 20000,
    "pending_payments": 5000,
    "essential_reserve": 15000
  },
  "expense": {
    "item_name": "Laptop",
    "cost": 45000,
    "category": "semi_essential",
    "installment_available": true,
    "installment_months": 6,
    "installment_monthly_amount": 8000
  }
}
```

The response includes:

```json
{
  "amount_safe_to_pay": 8000,
  "affordability_status": "affordable_with_plan",
  "recommended_payment_method": "installment_plan",
  "payment_plan": [],
  "earliest_date_for_full_payment": "YYYY-MM-DD",
  "spending_changes_needed": [],
  "decision_explanation": "The complete installment schedule passes your forecast."
}
```

`POST /decide/from-query` accepts a message and/or base64-encoded image along with the profile, state, and a fallback expense. This endpoint requires `ANTHROPIC_API_KEY`.

## Run tests in Docker

The multi-stage Dockerfile includes a dedicated test target. From the repository root:

```powershell
docker build --target test -t dealix-test .
```

The test stage compiles the Python modules and runs the API, frontend, and decision-engine tests. The production runtime image excludes test-only dependencies.

## Deploy to Vercel

DealIX exposes the FastAPI application through the root `main.py` Vercel entrypoint, configured as `main:app` in `pyproject.toml`. Import the GitHub repository into Vercel and set `ANTHROPIC_API_KEY` as a project environment variable if AI extraction is needed. A push to a non-production branch, such as `preview/inr-currency`, can be used for a preview deployment when the repository is connected to Vercel.

## How the decision engine works

The deterministic engine computes a personalized safe-spend limit, then evaluates full payment, installments, partial payment, and waiting against forecast cash-flow events. A plan is accepted only when the forecast preserves the greater of the essential reserve and preferred minimum balance while accounting for due payments, recurring expenses, and forecast income. Message/image-derived signals are applied conservatively.

LLM extraction and explanations do not make or override the affordability calculation.

## Project structure

```text
main.py                     # Vercel FastAPI entrypoint
src/dealix/
├── api.py                  # FastAPI routes and validation
├── decision_engine.py      # Deterministic forecast and recommendations
├── agent_response.py       # Response shape and explanations
├── llm_layer.py            # Optional text/image extraction
├── session.py              # Redis-backed session helpers
└── static/                 # Responsive INR browser interface

tests/
├── api/                    # API and frontend tests
└── unit/                   # Decision engine tests
```

## Safety and privacy

DealIX is an experimental decision-support tool, not financial, credit, tax, or investment advice. Recommendations depend on the accuracy of the figures and context provided. This demo does not include user authentication or production-grade controls for sensitive financial information. Do not enter real financial data or expose the app publicly until appropriate authentication, authorization, secure storage, privacy protections, and operational safeguards are in place.

## Repository description

> Your money, in context: a personal finance agent that forecasts purchases and finds a safer way forward.
