"""
FastAPI wrapper for the affordability agent.
-----------------------------------------------
Two endpoints, matching the two ways a "request" can arrive per the
problem statement:

  POST /decide             - fully structured input (profile + state + expense)
                              -> runs the engine directly, no LLM call needed.
                              Good for automated testing / judges poking at the API.

  POST /decide/from-query  - natural request: a free-text message and/or an
                              image, plus the user's stored profile/state.
                              -> runs extraction (llm_layer) first, then the
                              same engine + response builder.
                              This is the one that demos "Can I afford this
                              laptop?" style queries end-to-end.

Run with:  uvicorn api:app --reload
Docs at:   http://localhost:8000/docs   (auto-generated, good for a live demo)
"""

from typing import Optional, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import base64

from decision_engine import (
    UserProfile,
    FinancialState,
    ExpenseRequest,
    ItemizedExpense,
    RiskTolerance,
    PaymentPreference,
    IncomeStability,
    ExpenseCategory,
)
from agent_response import build_agent_response

app = FastAPI(
    title="Affordability Agent API",
    description="Decides whether, how, and when a user can safely afford an expense.",
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Pydantic request/response models (API boundary — kept separate from the
# internal dataclasses so the API shape can evolve independently)
# ---------------------------------------------------------------------------

class ItemizedExpenseIn(BaseModel):
    name: str
    amount: float
    flexible: bool
    category: str = "discretionary"


class UserProfileIn(BaseModel):
    user_id: str
    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    payment_preference: PaymentPreference = PaymentPreference.OK_WITH_INSTALLMENTS
    income_stability: IncomeStability = IncomeStability.FIXED_SALARY
    flexible_expense_willingness: float = Field(0.5, ge=0.0, le=1.0)
    category_priorities: dict = {}
    active_financial_goals: List[str] = []


class FinancialStateIn(BaseModel):
    current_balance: float
    confirmed_monthly_income: float
    recurring_monthly_expenses: float
    pending_payments: float
    essential_reserve: float
    itemized_expenses: List[ItemizedExpenseIn] = []


class ExpenseRequestIn(BaseModel):
    item_name: str
    cost: float
    category: ExpenseCategory = ExpenseCategory.DISCRETIONARY
    installment_available: bool = False
    installment_months: Optional[int] = None
    installment_monthly_amount: Optional[float] = None
    urgency: str = "normal"


class DecideRequest(BaseModel):
    profile: UserProfileIn
    state: FinancialStateIn
    expense: ExpenseRequestIn
    use_llm_explanation: bool = False


class DecideFromQueryRequest(BaseModel):
    profile: UserProfileIn
    state: FinancialStateIn
    message: Optional[str] = None
    image_base64: Optional[str] = None
    image_media_type: str = "image/jpeg"
    # fallback expense fields, used for anything extraction doesn't find
    fallback_expense: ExpenseRequestIn


class AgentResponseOut(BaseModel):
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: list
    earliest_date_for_full_payment: Optional[str]
    spending_changes_needed: list
    decision_explanation: str


# ---------------------------------------------------------------------------
# Conversion helpers (Pydantic in -> internal dataclasses)
# ---------------------------------------------------------------------------

def _to_profile(p: UserProfileIn) -> UserProfile:
    return UserProfile(
        user_id=p.user_id,
        risk_tolerance=p.risk_tolerance,
        payment_preference=p.payment_preference,
        income_stability=p.income_stability,
        flexible_expense_willingness=p.flexible_expense_willingness,
        category_priorities=p.category_priorities,
        active_financial_goals=p.active_financial_goals,
    )


def _to_state(s: FinancialStateIn) -> FinancialState:
    return FinancialState(
        current_balance=s.current_balance,
        confirmed_monthly_income=s.confirmed_monthly_income,
        recurring_monthly_expenses=s.recurring_monthly_expenses,
        pending_payments=s.pending_payments,
        essential_reserve=s.essential_reserve,
        itemized_expenses=[
            ItemizedExpense(name=i.name, amount=i.amount, flexible=i.flexible, category=i.category)
            for i in s.itemized_expenses
        ],
    )


def _to_expense(e: ExpenseRequestIn) -> ExpenseRequest:
    return ExpenseRequest(
        item_name=e.item_name,
        cost=e.cost,
        category=e.category,
        installment_available=e.installment_available,
        installment_months=e.installment_months,
        installment_monthly_amount=e.installment_monthly_amount,
        urgency=e.urgency,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/decide", response_model=AgentResponseOut)
def decide(req: DecideRequest):
    """Fully structured input — no LLM call, pure deterministic engine."""
    profile = _to_profile(req.profile)
    state = _to_state(req.state)
    expense = _to_expense(req.expense)

    response = build_agent_response(profile, state, expense, use_llm_explanation=req.use_llm_explanation)
    return response.__dict__


@app.post("/decide/from-query", response_model=AgentResponseOut)
def decide_from_query(req: DecideFromQueryRequest):
    """
    Natural-language / image entry point. Runs extraction first, then the
    same deterministic engine. This is the endpoint that answers
    'Can I afford this laptop?' style free-form requests.
    """
    if not req.message and not req.image_base64:
        raise HTTPException(status_code=400, detail="Provide a message and/or an image_base64.")

    from llm_layer import (
        extract_from_message,
        extract_from_image,
        merge_extraction_into_state,
        build_expense_from_extraction,
    )

    profile = _to_profile(req.profile)
    state = _to_state(req.state)
    fallback_expense = _to_expense(req.fallback_expense)

    extraction = {"expense": {}, "soft_signals": []}
    if req.message:
        extraction = extract_from_message(req.message)
    if req.image_base64:
        image_bytes = base64.b64decode(req.image_base64)
        image_extraction = extract_from_image(image_bytes, req.image_media_type)
        extraction["soft_signals"] = extraction.get("soft_signals", []) + image_extraction.get("soft_signals", [])
        if not extraction.get("expense"):
            extraction["expense"] = image_extraction.get("expense", {})

    state = merge_extraction_into_state(state, extraction)
    expense = build_expense_from_extraction(extraction, fallback_expense)

    response = build_agent_response(profile, state, expense, use_llm_explanation=True)
    return response.__dict__


@app.get("/health")
def health():
    return {"status": "ok"}