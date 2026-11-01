from fastapi.testclient import TestClient
from dealix.api import app
from main import app as vercel_app

client = TestClient(app)


def test_vercel_entrypoint_exports_the_dealix_app():
    assert vercel_app is app


def payload():
    return {
        "profile": {"user_id": "api-user", "risk_tolerance": "moderate",
                    "payment_preference": "ok_with_installments", "preferred_minimum_balance": 3000},
        "state": {"current_balance": 50000, "confirmed_monthly_income": 60000,
                  "recurring_monthly_expenses": 20000, "pending_payments": 5000,
                  "essential_reserve": 15000, "as_of": "2025-01-01"},
        "expense": {"item_name": "Laptop", "cost": 4000, "category": "semi_essential"}
    }


def test_decide_response_has_required_fields():
    response = client.post("/decide", json=payload())
    assert response.status_code == 200
    data = response.json()
    assert {"amount_safe_to_pay", "affordability_status", "recommended_payment_method",
            "payment_plan", "earliest_date_for_full_payment", "spending_changes_needed",
            "decision_explanation"} <= data.keys()


def test_invalid_purchase_rejected():
    body = payload()
    body["expense"]["cost"] = -1
    assert client.post("/decide", json=body).status_code == 422


def test_query_requires_message_or_image():
    body = payload()
    body["fallback_expense"] = body.pop("expense")
    assert client.post("/decide/from-query", json=body).status_code == 400
