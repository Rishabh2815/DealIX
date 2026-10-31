# DealIX

> **Make room for what matters.**
>
> A personalized affordability agent that looks beyond your balance before recommending your next money move.

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Container-Docker-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)

DealIX helps answer questions like **“Can I afford this laptop?”** using a deterministic cash-flow forecast personalized to each user. It considers recurring costs, upcoming payments, confirmed income, reserve preferences, risk tolerance, financial goals, and payment choices—not just the current account balance.

## What it does

- Recommends whether to pay in full, pay partially, use a safe installment plan, wait, or not proceed.
- Forecasts cash flow across the complete proposed payment schedule and checks the user's balance floor at each event.
- Personalizes decisions using income stability, risk tolerance, payment preferences, priorities, and goals.
- Can extract financial context from a message or bill image when an Anthropic API key is configured.
- Returns the maximum safe amount today, a dated payment plan, the earliest safe full-payment date, flexible spending changes, and an explanation.
- Includes a responsive browser interface with rupee-denominated inputs and INR-formatted results.

## Run with Docker Compose

Clone the repository, move into its directory, and start DealIX:

```powershell
git clone https://github.com/Rishabh2815/DealIX.git
cd DealIX
docker compose up --pull always --build -d
```

Open **http://localhost:8000**. The API health endpoint is **http://localhost:8000/health**.

To stop the app:

```powershell
docker compose down
```

Message and image extraction requires an Anthropic API key. Put it in a local `.env` file (ignored by Git):

```dotenv
ANTHROPIC_API_KEY=your-key
```

Restart the service after setting the key:

```powershell
docker compose up --pull always --build -d
```

Never commit API keys or other secrets, and do not bake them into a Docker image.

## Run locally

Python 3.11 or newer is required.

```powershell
python -m pip install -e ".[test]"
uvicorn dealix.api:app --reload --app-dir src
```

The browser app is served at **http://localhost:8000**. Run the test suite with:

```powershell
python -m pytest
```

## API

### `POST /decide`

Submit a structured profile, financial state, and purchase request. The deterministic decision engine does not need an LLM API key.

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

### `POST /decide/from-query`

Send a free-text message and/or a base64-encoded image together with the user's profile, financial state, and a fallback purchase request. This endpoint uses Anthropic for extraction and explanation, so configure `ANTHROPIC_API_KEY` first.

### Response

The decision response includes:

- `amount_safe_to_pay`
- `affordability_status`
- `recommended_payment_method`
- `payment_plan`
- `earliest_date_for_full_payment`
- `spending_changes_needed`
- `decision_explanation`

Interactive API documentation is available at **http://localhost:8000/docs**.

## Project layout

```text
src/dealix/
├── api.py                # FastAPI routes and request validation
├── decision_engine.py    # Deterministic forecast and affordability decisions
├── agent_response.py    # Public decision response and explanation
├── llm_layer.py         # Optional message/image extraction and explanation
├── session.py           # Redis-backed conversational session helpers
└── static/              # Browser interface, styles, and JavaScript

tests/
├── api/                  # API and frontend tests
└── unit/                 # Decision engine tests
```

## How the decision works

The deterministic engine calculates a personalized safe-spend limit, then tests available strategies against forecast cash-flow events. Each proposed plan must preserve the greater of the user's essential reserve and preferred minimum balance while accounting for scheduled payments, recurring costs, and forecast income. Message/image-derived signals are conservatively applied to income forecasts.

LLM extraction and natural-language explanations do not replace the deterministic affordability calculation.

## Important safety note

DealIX is a decision-support demo, not financial, credit, tax, or investment advice. Its recommendations are only as reliable as the figures and context supplied. The demo app does not include user authentication or production-grade protection for sensitive financial data. Do not expose it publicly or enter real financial information until you have added suitable authentication, authorization, secure storage, privacy controls, and operational safeguards.
