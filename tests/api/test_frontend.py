from fastapi.testclient import TestClient

from dealix.api import app

client = TestClient(app)


def test_homepage_serves_frontend():
    response = client.get("/")
    assert response.status_code == 200
    assert "Spend with" in response.text
    assert 'id="afford-form"' in response.text


def test_frontend_assets_are_served():
    css = client.get("/static/styles.css")
    js = client.get("/static/app.js")
    assert css.status_code == 200 and "workspace" in css.text
    assert js.status_code == 200 and "/decide/from-query" in js.text


def test_decide_accepts_frontend_financial_profile():
    body = {
        "profile": {"user_id": "frontend-test", "risk_tolerance": "moderate",
                    "payment_preference": "ok_with_installments", "preferred_minimum_balance": 1000},
        "state": {"current_balance": 20000, "confirmed_monthly_income": 8000,
                  "recurring_monthly_expenses": 3000, "pending_payments": 1000,
                  "essential_reserve": 5000, "as_of": "2026-01-01"},
        "expense": {"item_name": "Work laptop", "cost": 1000,
                    "category": "semi_essential", "installment_available": False}
    }
    response = client.post("/decide", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["affordability_status"] == "affordable_now"
    assert result["amount_safe_to_pay"] == 1000
    assert result["payment_plan"]
