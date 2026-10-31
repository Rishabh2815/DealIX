"""
Agent Response Builder
------------------------
Takes a computed DecisionResult (from decision_engine.py) plus the original
profile/state/expense, and assembles the final response in the exact shape
required by the spec:

    amount_safe_to_pay
    affordability_status
    recommended_payment_method
    payment_plan
    earliest_date_for_full_payment
    spending_changes_needed
    decision_explanation

This module does NOT re-decide anything. It's a pure mapping/formatting
layer on top of the already-computed, already-audited DecisionResult.
Keeping this separate means you can change the *shape* of the API response
without touching the financial logic, and vice versa.
"""

from dataclasses import dataclass, asdict
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from decision_engine import (
    DecisionEngine,
    DecisionResult,
    UserProfile,
    FinancialState,
    ExpenseRequest,
    ItemizedExpense,
    ExpenseCategory,
    RiskTolerance,
    IncomeStability,
    Verdict,
    PaymentPreference,
)


# ---------------------------------------------------------------------------
# Output enums / models
# ---------------------------------------------------------------------------

class AffordabilityStatus(str, Enum):
    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class RecommendedPaymentMethod(str, Enum):
    FULL_PAYMENT_NOW = "full_payment_now"
    PARTIAL_PAYMENT_NOW = "partial_payment_now"
    INSTALLMENT_PLAN = "installment_plan"
    WAIT_THEN_PAY_FULL = "wait_then_pay_full"
    DO_NOT_PROCEED = "do_not_proceed"


@dataclass
class PaymentPlanEntry:
    date: str          # ISO date string
    amount: float
    label: str         # e.g. "Initial payment", "Installment 2 of 6", "Full payment"


@dataclass
class SpendingChange:
    expense_name: str
    current_amount: float
    action: str        # "pause" | "reduce"
    reason: str


@dataclass
class AgentResponse:
    amount_safe_to_pay: float
    affordability_status: str
    recommended_payment_method: str
    payment_plan: list          # list[PaymentPlanEntry] (as dicts)
    earliest_date_for_full_payment: Optional[str]
    spending_changes_needed: list   # list[SpendingChange] (as dicts)
    decision_explanation: str


# ---------------------------------------------------------------------------
# Verdict -> status / method mappings
# ---------------------------------------------------------------------------

_STATUS_MAP = {
    Verdict.PAY_IN_FULL: AffordabilityStatus.AFFORDABLE_NOW,
    Verdict.USE_INSTALLMENTS: AffordabilityStatus.AFFORDABLE_WITH_PLAN,
    Verdict.PAY_PARTIALLY: AffordabilityStatus.AFFORDABLE_WITH_PLAN,
    Verdict.WAIT: AffordabilityStatus.AFFORDABLE_LATER,
    Verdict.NOT_PROCEED: AffordabilityStatus.NOT_AFFORDABLE,
}

_METHOD_MAP = {
    Verdict.PAY_IN_FULL: RecommendedPaymentMethod.FULL_PAYMENT_NOW,
    Verdict.USE_INSTALLMENTS: RecommendedPaymentMethod.INSTALLMENT_PLAN,
    Verdict.PAY_PARTIALLY: RecommendedPaymentMethod.PARTIAL_PAYMENT_NOW,
    Verdict.WAIT: RecommendedPaymentMethod.WAIT_THEN_PAY_FULL,
    Verdict.NOT_PROCEED: RecommendedPaymentMethod.DO_NOT_PROCEED,
}


def build_agent_response(
    profile: UserProfile,
    state: FinancialState,
    expense: ExpenseRequest,
    result: Optional[DecisionResult] = None,
    use_llm_explanation: bool = False,
) -> AgentResponse:
    """
    Main entry point. If `result` isn't passed, runs the engine itself.
    Set use_llm_explanation=True to call llm_layer.explain_decision for
    decision_explanation instead of the deterministic template (requires
    an ANTHROPIC_API_KEY to be configured).
    """
    if result is None:
        result = DecisionEngine().decide(profile, state, expense)

    amount_safe_to_pay = _compute_amount_safe_to_pay(result, expense)
    payment_plan = _build_payment_plan(result, expense, state, amount_safe_to_pay)
    earliest_full = _compute_earliest_full_payment_date(result, expense, state)
    spending_changes = _compute_spending_changes(result, expense, state, amount_safe_to_pay)

    if use_llm_explanation:
        from llm_layer import explain_decision
        explanation = explain_decision(result, profile, expense)
    else:
        explanation = _deterministic_explanation(result, expense, amount_safe_to_pay, spending_changes)

    return AgentResponse(
        amount_safe_to_pay=round(amount_safe_to_pay, 2),
        affordability_status=_STATUS_MAP[result.verdict].value,
        recommended_payment_method=_METHOD_MAP[result.verdict].value,
        payment_plan=[asdict(p) for p in payment_plan],
        earliest_date_for_full_payment=earliest_full.isoformat() if earliest_full else None,
        spending_changes_needed=[asdict(s) for s in spending_changes],
        decision_explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Field builders
# ---------------------------------------------------------------------------

def _compute_amount_safe_to_pay(result: DecisionResult, expense: ExpenseRequest) -> float:
    """The maximum the user can safely pay TODAY, regardless of the full cost."""
    if result.verdict == Verdict.PAY_IN_FULL:
        return expense.cost
    if result.verdict == Verdict.PAY_PARTIALLY:
        return result.partial_amount or 0.0
    if result.verdict == Verdict.USE_INSTALLMENTS:
        # today's safe payment is the first installment (or the full safe limit,
        # whichever is smaller — never suggest paying more than the plan requires)
        return min(expense.installment_monthly_amount or 0.0, result.safe_spend_limit)
    # WAIT / NOT_PROCEED: nothing is safe to pay today
    return 0.0


def _build_payment_plan(
    result: DecisionResult,
    expense: ExpenseRequest,
    state: FinancialState,
    amount_safe_to_pay: float,
) -> list:
    today = state.as_of
    plan = []

    if result.verdict == Verdict.PAY_IN_FULL:
        plan.append(PaymentPlanEntry(date=today.isoformat(), amount=expense.cost, label="Full payment"))

    elif result.verdict == Verdict.USE_INSTALLMENTS:
        months = expense.installment_months or 1
        monthly = expense.installment_monthly_amount or (expense.cost / months)
        for i in range(months):
            due = today + timedelta(days=30 * i)
            plan.append(
                PaymentPlanEntry(
                    date=due.isoformat(),
                    amount=round(monthly, 2),
                    label=f"Installment {i + 1} of {months}",
                )
            )

    elif result.verdict == Verdict.PAY_PARTIALLY:
        partial = result.partial_amount or 0.0
        remainder = max(expense.cost - partial, 0.0)
        plan.append(PaymentPlanEntry(date=today.isoformat(), amount=round(partial, 2), label="Initial payment"))
        if remainder > 0:
            # estimate remainder date the same way the engine estimates wait dates
            cycles_needed = max(1, -(-remainder // max(result.disposable_income, 1)))
            remainder_date = today + timedelta(days=30 * int(cycles_needed))
            plan.append(
                PaymentPlanEntry(
                    date=remainder_date.isoformat(),
                    amount=round(remainder, 2),
                    label="Remaining balance",
                )
            )

    elif result.verdict == Verdict.WAIT and result.suggested_wait_until:
        plan.append(
            PaymentPlanEntry(
                date=result.suggested_wait_until.isoformat(),
                amount=expense.cost,
                label="Full payment (once affordable)",
            )
        )

    # NOT_PROCEED -> empty plan, intentionally
    return plan


def _compute_earliest_full_payment_date(
    result: DecisionResult,
    expense: ExpenseRequest,
    state: FinancialState,
) -> Optional[date]:
    if result.verdict == Verdict.PAY_IN_FULL:
        return state.as_of
    if result.verdict == Verdict.USE_INSTALLMENTS:
        months = expense.installment_months or 1
        return state.as_of + timedelta(days=30 * (months - 1))
    if result.verdict == Verdict.PAY_PARTIALLY:
        remainder = max(expense.cost - (result.partial_amount or 0.0), 0.0)
        if result.disposable_income <= 0:
            return None
        cycles = max(1, -(-remainder // result.disposable_income))
        return state.as_of + timedelta(days=30 * int(cycles))
    if result.verdict == Verdict.WAIT:
        return result.suggested_wait_until
    return None  # NOT_PROCEED: no safe date can be given with current information


def _compute_spending_changes(
    result: DecisionResult,
    expense: ExpenseRequest,
    state: FinancialState,
    amount_safe_to_pay: float,
) -> list:
    """
    If the request isn't cleanly affordable now, suggest which flexible
    (itemized) expenses to pause/reduce to close the gap — greedy by size,
    only touching items marked flexible=True.
    """
    if result.verdict == Verdict.PAY_IN_FULL:
        return []  # no changes needed, already affordable

    gap = max(expense.cost - amount_safe_to_pay, 0.0)
    if gap <= 0:
        return []

    flexible_items = sorted(
        [e for e in state.itemized_expenses if e.flexible],
        key=lambda e: e.amount,
        reverse=True,
    )

    changes = []
    covered = 0.0
    for item in flexible_items:
        if covered >= gap:
            break
        action = "pause" if item.amount <= (gap - covered) else "reduce"
        changes.append(
            SpendingChange(
                expense_name=item.name,
                current_amount=item.amount,
                action=action,
                reason=(
                    f"Pausing/reducing this frees up roughly {min(item.amount, gap - covered):.0f} "
                    f"toward the {gap:.0f} shortfall for this request."
                ),
            )
        )
        covered += item.amount

    return changes


def _deterministic_explanation(
    result: DecisionResult,
    expense: ExpenseRequest,
    amount_safe_to_pay: float,
    spending_changes: list,
) -> str:
    """
    Template-based explanation — no LLM call required. Good default for
    demos/tests where you don't want a network dependency, and a reasonable
    fallback if the LLM call fails.
    """
    if result.verdict == Verdict.PAY_IN_FULL:
        return (
            f"You can safely pay the full {expense.cost:.0f} for {expense.item_name} today "
            f"without touching your essential reserve or upcoming bills."
        )
    if result.verdict == Verdict.USE_INSTALLMENTS:
        return (
            f"Paying {expense.cost:.0f} upfront would eat into money needed for recurring bills, "
            f"but an installment plan of {expense.installment_monthly_amount:.0f}/month fits "
            f"comfortably within your safe spending limit."
        )
    if result.verdict == Verdict.PAY_PARTIALLY:
        return (
            f"The full {expense.cost:.0f} isn't safe to pay today, but {amount_safe_to_pay:.0f} is. "
            f"Paying that now and the remainder once your next income lands keeps you within a safe margin."
        )
    if result.verdict == Verdict.WAIT:
        changes_note = ""
        if spending_changes:
            names = ", ".join(c.expense_name for c in spending_changes)
            changes_note = f" Reducing {names} would speed this up."
        return (
            f"Paying {expense.cost:.0f} now would leave too little for recurring expenses and "
            f"your essential reserve. Waiting until your finances recover keeps you safe.{changes_note}"
        )
    return (
        f"This expense of {expense.cost:.0f} isn't safe to take on right now under any payment "
        f"structure available, given your current commitments and reserve requirements."
    )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    state = FinancialState(
        current_balance=60000,
        confirmed_monthly_income=60000,
        recurring_monthly_expenses=20000,
        pending_payments=5000,
        essential_reserve=15000,
        itemized_expenses=[
            ItemizedExpense(name="Streaming subscriptions", amount=1200, flexible=True, category="subscription"),
            ItemizedExpense(name="Dining out", amount=4000, flexible=True, category="discretionary"),
            ItemizedExpense(name="Gym membership", amount=1500, flexible=True, category="discretionary"),
            ItemizedExpense(name="Rent", amount=15000, flexible=False, category="essential"),
        ],
    )

    laptop = ExpenseRequest(
        item_name="Laptop",
        cost=45000,
        category=ExpenseCategory.SEMI_ESSENTIAL,
        installment_available=True,
        installment_months=6,
        installment_monthly_amount=8000,
    )

    conservative_user = UserProfile(
        user_id="userA",
        risk_tolerance=RiskTolerance.CONSERVATIVE,
        payment_preference=PaymentPreference.AVOID_CREDIT,
        income_stability=IncomeStability.VARIABLE,
        flexible_expense_willingness=0.3,
        active_financial_goals=["no_debt"],
    )

    response = build_agent_response(conservative_user, state, laptop)
    print(json.dumps(asdict(response), indent=2))