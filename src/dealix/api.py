"""FastAPI endpoints for structured and natural-language affordability checks."""
import base64
from datetime import date
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field

from .decision_engine import (DecisionEngine, ExpenseCategory, ExpenseRequest, FinancialState,
                              IncomeStability, ItemizedExpense, PaymentPreference, PendingPayment,
                              RiskTolerance, UserProfile)
from .agent_response import build_agent_response

app = FastAPI(title="DealIX Affordability Agent", description="Personalized, forecast-based safe-spending decisions.", version="1.0.0")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(STATIC_DIR / "index.html")


class ItemizedExpenseIn(BaseModel):
    name: str
    amount: float = Field(ge=0)
    flexible: bool
    category: str = "discretionary"


class PendingPaymentIn(BaseModel):
    amount: float = Field(ge=0)
    due_date: date
    description: str = "Pending payment"


class UserProfileIn(BaseModel):
    user_id: str
    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    payment_preference: PaymentPreference = PaymentPreference.OK_WITH_INSTALLMENTS
    income_stability: IncomeStability = IncomeStability.FIXED_SALARY
    flexible_expense_willingness: float = Field(0.5, ge=0, le=1)
    category_priorities: dict = Field(default_factory=dict)
    active_financial_goals: List[str] = Field(default_factory=list)
    preferred_minimum_balance: float = Field(0, ge=0)


class FinancialStateIn(BaseModel):
    current_balance: float = Field(ge=0)
    confirmed_monthly_income: float = Field(ge=0)
    recurring_monthly_expenses: float = Field(ge=0)
    pending_payments: float = Field(ge=0)
    essential_reserve: float = Field(ge=0)
    as_of: date = Field(default_factory=date.today)
    next_income_date: Optional[date] = None
    soft_signals: list = Field(default_factory=list)
    itemized_expenses: List[ItemizedExpenseIn] = Field(default_factory=list)
    pending_payment_items: List[PendingPaymentIn] = Field(default_factory=list)


class ExpenseRequestIn(BaseModel):
    item_name: str
    cost: float = Field(gt=0)
    category: ExpenseCategory = ExpenseCategory.DISCRETIONARY
    installment_available: bool = False
    installment_months: Optional[int] = Field(default=None, ge=1)
    installment_monthly_amount: Optional[float] = Field(default=None, gt=0)
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
    fallback_expense: ExpenseRequestIn


class AgentResponseOut(BaseModel):
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: list
    earliest_date_for_full_payment: Optional[str]
    spending_changes_needed: list
    decision_explanation: str


def _profile(p):
    return UserProfile(**p.model_dump())


def _state(s):
    d = s.model_dump()
    d["itemized_expenses"] = [ItemizedExpense(**i) for i in d["itemized_expenses"]]
    d["pending_payment_items"] = [PendingPayment(**i) for i in d["pending_payment_items"]]
    return FinancialState(**d)


def _expense(e):
    return ExpenseRequest(**e.model_dump())


@app.post("/decide", response_model=AgentResponseOut)
def decide(req: DecideRequest):
    try:
        return build_agent_response(_profile(req.profile), _state(req.state), _expense(req.expense),
                                    use_llm_explanation=req.use_llm_explanation).__dict__
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/decide/from-query", response_model=AgentResponseOut)
def decide_from_query(req: DecideFromQueryRequest):
    if not req.message and not req.image_base64:
        raise HTTPException(status_code=400, detail="Provide a message and/or image_base64.")
    from .llm_layer import extract_from_image, extract_from_message, merge_extraction_into_state, build_expense_from_extraction
    profile, state, fallback = _profile(req.profile), _state(req.state), _expense(req.fallback_expense)
    extraction = {"expense": {}, "soft_signals": []}
    try:
        if req.message:
            extraction = extract_from_message(req.message)
        if req.image_base64:
            image_data = base64.b64decode(req.image_base64, validate=True)
            image_extract = extract_from_image(image_data, req.image_media_type)
            extraction["soft_signals"] = extraction.get("soft_signals", []) + image_extract.get("soft_signals", [])
            if not extraction.get("expense"):
                extraction["expense"] = image_extract.get("expense", {})
        state = merge_extraction_into_state(state, extraction)
        expense = build_expense_from_extraction(extraction, fallback)
        return build_agent_response(profile, state, expense, use_llm_explanation=True).__dict__
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/health")
def health():
    return {"status": "ok"}
