"""Public response model and deterministic explanation for affordability decisions."""
from dataclasses import asdict, dataclass
from typing import Optional

from .decision_engine import DecisionEngine, DecisionResult, ExpenseRequest, FinancialState, UserProfile, Verdict


_STATUS = {
    Verdict.PAY_IN_FULL: "affordable_now",
    Verdict.USE_INSTALLMENTS: "affordable_with_plan",
    Verdict.PAY_PARTIALLY: "affordable_with_plan",
    Verdict.WAIT: "affordable_later",
    Verdict.NOT_PROCEED: "not_affordable",
}
_METHOD = {
    Verdict.PAY_IN_FULL: "full_payment_now",
    Verdict.USE_INSTALLMENTS: "installment_plan",
    Verdict.PAY_PARTIALLY: "partial_payment_now",
    Verdict.WAIT: "wait_then_pay_full",
    Verdict.NOT_PROCEED: "do_not_proceed",
}


@dataclass
class AgentResponse:
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: list
    earliest_date_for_full_payment: Optional[str]
    spending_changes_needed: list
    decision_explanation: str


def build_agent_response(profile: UserProfile, state: FinancialState, expense: ExpenseRequest,
                         result: Optional[DecisionResult] = None, use_llm_explanation: bool = False) -> AgentResponse:
    result = result or DecisionEngine().decide(profile, state, expense)
    safe_today = expense.cost if result.verdict == Verdict.PAY_IN_FULL else (
        result.payment_plan[0]["amount"] if result.payment_plan and result.verdict in (Verdict.USE_INSTALLMENTS, Verdict.PAY_PARTIALLY) else 0.0
    )
    if use_llm_explanation:
        try:
            from .llm_layer import explain_decision
            explanation = explain_decision(result, profile, expense)
        except Exception:
            explanation = _explanation(result, expense, safe_today)
    else:
        explanation = _explanation(result, expense, safe_today)
    return AgentResponse(
        amount_safe_to_pay=round(safe_today, 2),
        affordability_status=_STATUS[result.verdict],
        recommended_payment_method=_METHOD[result.verdict],
        payment_plan=result.payment_plan,
        earliest_date_for_full_payment=(result.earliest_date_for_full_payment.isoformat()
                                        if result.earliest_date_for_full_payment else None),
        spending_changes_needed=result.spending_changes_needed,
        decision_explanation=explanation,
    )


def _explanation(result: DecisionResult, expense: ExpenseRequest, safe_today: float) -> str:
    if result.verdict == Verdict.PAY_IN_FULL:
        lead = f"You can safely pay {expense.cost:.2f} for {expense.item_name} today."
    elif result.verdict == Verdict.USE_INSTALLMENTS:
        lead = f"The full upfront payment is not safe, but the complete installment schedule for {expense.item_name} passes your forecast."
    elif result.verdict == Verdict.PAY_PARTIALLY:
        lead = f"You can safely pay {safe_today:.2f} toward {expense.item_name} today; pay the remainder on the dated plan."
    elif result.verdict == Verdict.WAIT:
        lead = f"Wait before buying {expense.item_name}; the forecast supports full payment on {result.suggested_wait_until}."
    else:
        lead = f"Do not proceed with {expense.item_name} under the current financial forecast."
    trace = result.reasoning_trace[-1] if result.reasoning_trace else ""
    return f"{lead} {trace}".strip()
