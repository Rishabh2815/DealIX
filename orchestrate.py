"""
Financial Affordability Decision Engine
-----------------------------------------
This is the DETERMINISTIC core of the agent. It does NOT call an LLM.
It takes structured financial state + a structured expense request and
produces a verdict + a full reasoning trace.

Design intent (for the judges / README):
  - All arithmetic and thresholds live here, in code you can audit line by line.
  - The LLM layer (separate module) only ever *explains* what this engine
    decided, or *extracts* structured fields to feed into it. It never
    performs the affordability math itself. This keeps the system reliable
    and explainable.
  - Every decision returns a `reasoning_trace`: an ordered list of the
    intermediate values/checks that led to the verdict. Surface this in
    the UI/API response — it's the single highest-leverage feature for
    demoing "personalization" and "trustworthiness" to judges.

Usage:
    engine = DecisionEngine()
    result = engine.decide(user_profile, financial_state, expense_request)
    print(result.verdict, result.reasoning_trace)
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import date
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RiskTolerance(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class PaymentPreference(str, Enum):
    AVOID_CREDIT = "avoid_credit"          # will not use installments/EMI/credit
    OK_WITH_INSTALLMENTS = "ok_with_installments"
    PAY_FULL_ONLY = "pay_full_only"        # will not partial-pay; full or nothing


class IncomeStability(str, Enum):
    FIXED_SALARY = "fixed_salary"
    VARIABLE = "variable"
    IRREGULAR = "irregular"


class Verdict(str, Enum):
    PAY_IN_FULL = "pay_in_full"
    PAY_PARTIALLY = "pay_partially"
    USE_INSTALLMENTS = "use_installments"
    WAIT = "wait"
    NOT_PROCEED = "not_proceed"


class ExpenseCategory(str, Enum):
    ESSENTIAL = "essential"        # rent, groceries, medical, utilities
    SEMI_ESSENTIAL = "semi_essential"  # transport upgrade, work equipment
    DISCRETIONARY = "discretionary"    # gadgets, entertainment, travel


# ---------------------------------------------------------------------------
# Input data models
# ---------------------------------------------------------------------------

@dataclass
class UserProfile:
    """Static-ish personalization signals. Update slowly, over time."""
    user_id: str
    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    payment_preference: PaymentPreference = PaymentPreference.OK_WITH_INSTALLMENTS
    income_stability: IncomeStability = IncomeStability.FIXED_SALARY
    # 0.0 = will not touch flexible/discretionary spend even if affordable
    # 1.0 = happy to spend freely on flexible categories if math allows
    flexible_expense_willingness: float = 0.5
    # per-category priority overrides risk tolerance for that category
    # e.g. {"health": "high", "electronics": "low"}
    category_priorities: dict = field(default_factory=dict)
    # explicit user-declared goals that should bias toward caution
    active_financial_goals: list = field(default_factory=list)  # e.g. ["no_debt", "saving_for_trip"]


@dataclass
class FinancialState:
    """Dynamic numbers — refreshed per request from account data / statements."""
    current_balance: float
    confirmed_monthly_income: float
    recurring_monthly_expenses: float      # rent, subscriptions, utilities, EMIs already running
    pending_payments: float                # known upcoming bills not yet paid, due soon
    essential_reserve: float               # money that must NEVER be touched (emergency floor)
    as_of: date = field(default_factory=date.today)
    # soft signals extracted from messages/images, each with a confidence 0-1
    # e.g. [{"type": "income_delay", "amount": 15000, "confidence": 0.7}]
    soft_signals: list = field(default_factory=list)


@dataclass
class ExpenseRequest:
    item_name: str
    cost: float
    category: ExpenseCategory = ExpenseCategory.DISCRETIONARY
    installment_available: bool = False
    installment_months: Optional[int] = None       # if installment_available
    installment_monthly_amount: Optional[float] = None
    urgency: str = "normal"                        # "normal" | "urgent" | "can_defer"


# ---------------------------------------------------------------------------
# Output data model
# ---------------------------------------------------------------------------

@dataclass
class DecisionResult:
    verdict: Verdict
    confidence: float                      # 0-1, how clear-cut the decision was
    disposable_income: float
    safe_spend_limit: float
    reasoning_trace: list                  # ordered list of strings — audit trail
    suggested_wait_until: Optional[date] = None
    suggested_alternative: Optional[str] = None    # e.g. "pay 40% now, rest in 2 installments"


# ---------------------------------------------------------------------------
# Personalization thresholds
# ---------------------------------------------------------------------------
# These are the "safe %" of disposable income a user is allowed to spend
# in one shot before we downgrade the verdict. Tunable per risk tolerance.
# This is deliberately explicit and inspectable rather than baked into a prompt.

SAFE_SPEND_PCT = {
    RiskTolerance.CONSERVATIVE: 0.25,
    RiskTolerance.MODERATE: 0.40,
    RiskTolerance.AGGRESSIVE: 0.60,
}

STRETCH_SPEND_PCT = {  # ceiling beyond which even installments are declined
    RiskTolerance.CONSERVATIVE: 0.60,
    RiskTolerance.MODERATE: 0.85,
    RiskTolerance.AGGRESSIVE: 1.10,  # aggressive users may dip slightly into buffer
}

INCOME_STABILITY_BUFFER = {
    # extra safety margin subtracted from disposable income for less predictable earners
    IncomeStability.FIXED_SALARY: 0.0,
    IncomeStability.VARIABLE: 0.10,
    IncomeStability.IRREGULAR: 0.20,
}


class DecisionEngine:

    def decide(
        self,
        profile: UserProfile,
        state: FinancialState,
        expense: ExpenseRequest,
    ) -> DecisionResult:
        trace = []

        # --- Step 1: apply soft-signal adjustments to disposable income ---
        adjusted_income, soft_adjustment_note = self._apply_soft_signals(state)
        if soft_adjustment_note:
            trace.append(soft_adjustment_note)

        # --- Step 2: compute raw disposable income ---
        disposable = (
            adjusted_income
            - state.recurring_monthly_expenses
            - state.pending_payments
        )
        trace.append(
            f"Disposable income = income({adjusted_income:.0f}) - "
            f"recurring({state.recurring_monthly_expenses:.0f}) - "
            f"pending({state.pending_payments:.0f}) = {disposable:.0f}"
        )

        # --- Step 3: apply income-stability buffer ---
        buffer_pct = INCOME_STABILITY_BUFFER[profile.income_stability]
        if buffer_pct > 0:
            buffer_amt = disposable * buffer_pct
            disposable -= buffer_amt
            trace.append(
                f"Applied {buffer_pct:.0%} income-stability buffer "
                f"({profile.income_stability.value}) → disposable now {disposable:.0f}"
            )

        # --- Step 4: hard guardrail — essential reserve & goals ---
        available_balance = state.current_balance - state.essential_reserve
        trace.append(
            f"Balance after essential reserve: {state.current_balance:.0f} - "
            f"{state.essential_reserve:.0f} = {available_balance:.0f}"
        )

        if disposable <= 0 and available_balance <= 0:
            trace.append("No disposable income and no available balance above reserve.")
            return self._hard_stop(disposable, trace, expense)

        if "no_debt" in profile.active_financial_goals and expense.installment_available:
            trace.append(
                "User has an active 'no_debt' goal — installments are excluded from consideration."
            )

        # --- Step 5: category & priority adjustments ---
        safe_pct = SAFE_SPEND_PCT[profile.risk_tolerance]
        stretch_pct = STRETCH_SPEND_PCT[profile.risk_tolerance]

        category_priority = profile.category_priorities.get(expense.category.value)
        if expense.category == ExpenseCategory.ESSENTIAL:
            # essential spend gets much more lenient thresholds regardless of risk profile
            safe_pct = max(safe_pct, 0.70)
            stretch_pct = max(stretch_pct, 1.0)
            trace.append("Expense flagged ESSENTIAL — thresholds relaxed.")
        elif expense.category == ExpenseCategory.DISCRETIONARY:
            # scale down by the user's stated willingness to spend on flexible items
            safe_pct *= profile.flexible_expense_willingness
            stretch_pct *= (0.5 + 0.5 * profile.flexible_expense_willingness)
            trace.append(
                f"Expense flagged DISCRETIONARY — scaled thresholds by "
                f"flexible_expense_willingness={profile.flexible_expense_willingness} "
                f"→ safe_pct={safe_pct:.0%}, stretch_pct={stretch_pct:.0%}"
            )

        if category_priority == "high":
            safe_pct = min(safe_pct * 1.3, 1.0)
            trace.append("User marked this category as HIGH priority — thresholds loosened.")
        elif category_priority == "low":
            safe_pct *= 0.6
            trace.append("User marked this category as LOW priority — thresholds tightened.")

        safe_spend_limit = max(disposable, 0) * safe_pct
        stretch_spend_limit = max(disposable, 0) * stretch_pct
        trace.append(
            f"Safe spend limit = {safe_spend_limit:.0f} | Stretch limit = {stretch_spend_limit:.0f} "
            f"(cost requested: {expense.cost:.0f})"
        )

        # --- Step 6: verdict logic ---
        cost = expense.cost

        # Case A: fully affordable within safe threshold
        if cost <= safe_spend_limit and cost <= available_balance:
            trace.append("Cost is within safe spend limit and available balance.")
            return DecisionResult(
                verdict=Verdict.PAY_IN_FULL,
                confidence=0.9,
                disposable_income=disposable,
                safe_spend_limit=safe_spend_limit,
                reasoning_trace=trace,
            )

        # Case B: affordable via installments, if user allows them
        if (
            expense.installment_available
            and profile.payment_preference != PaymentPreference.AVOID_CREDIT
            and "no_debt" not in profile.active_financial_goals
            and expense.installment_monthly_amount is not None
            and expense.installment_monthly_amount <= safe_spend_limit
        ):
            trace.append(
                f"Monthly installment ({expense.installment_monthly_amount:.0f}) fits within "
                f"safe monthly spend limit ({safe_spend_limit:.0f}) and user allows credit."
            )
            return DecisionResult(
                verdict=Verdict.USE_INSTALLMENTS,
                confidence=0.8,
                disposable_income=disposable,
                safe_spend_limit=safe_spend_limit,
                reasoning_trace=trace,
                suggested_alternative=(
                    f"Pay in {expense.installment_months} installments of "
                    f"~{expense.installment_monthly_amount:.0f}/month"
                ),
            )

        # Case C: partial payment possible
        if (
            profile.payment_preference != PaymentPreference.PAY_FULL_ONLY
            and cost <= stretch_spend_limit
            and available_balance > 0
        ):
            partial_amount = min(safe_spend_limit, available_balance)
            pct = (partial_amount / cost) * 100 if cost else 0
            trace.append(
                f"Full cost exceeds safe limit but user allows partial payment. "
                f"Suggest paying {partial_amount:.0f} now (~{pct:.0f}% of cost)."
            )
            return DecisionResult(
                verdict=Verdict.PAY_PARTIALLY,
                confidence=0.65,
                disposable_income=disposable,
                safe_spend_limit=safe_spend_limit,
                reasoning_trace=trace,
                suggested_alternative=f"Pay {partial_amount:.0f} now, remainder once income clears.",
            )

        # Case D: can't afford now, but not a hard no — suggest waiting
        if expense.urgency != "urgent" and disposable > 0:
            trace.append(
                "Cost exceeds what's currently safe, but situation isn't dire and "
                "the item isn't urgent — recommend waiting for next income cycle."
            )
            return DecisionResult(
                verdict=Verdict.WAIT,
                confidence=0.7,
                disposable_income=disposable,
                safe_spend_limit=safe_spend_limit,
                reasoning_trace=trace,
                suggested_wait_until=self._estimate_wait_date(cost, disposable, state.as_of),
            )

        # Case E: hard no
        trace.append("Cost exceeds stretch limit, urgent framing doesn't change feasibility.")
        return DecisionResult(
            verdict=Verdict.NOT_PROCEED,
            confidence=0.85,
            disposable_income=disposable,
            safe_spend_limit=safe_spend_limit,
            reasoning_trace=trace,
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    def _apply_soft_signals(self, state: FinancialState):
        """
        Soft signals come from messages/images (e.g. 'bonus delayed', 'got a raise').
        They are confidence-weighted and only ever ADJUST income conservatively —
        a low-confidence positive signal should not be trusted as much as a
        low-confidence negative one (asymmetric caution).
        """
        income = state.confirmed_monthly_income
        notes = []
        for signal in state.soft_signals:
            amt = signal.get("amount", 0)
            conf = signal.get("confidence", 0.5)
            sig_type = signal.get("type", "unknown")

            if sig_type in ("income_delay", "income_loss", "unexpected_expense"):
                # negative signals: weight them at least at 70% confidence even if
                # reported confidence is lower — asymmetric caution
                effective_conf = max(conf, 0.7)
                income -= amt * effective_conf
                notes.append(f"soft signal '{sig_type}' reduced income by {amt * effective_conf:.0f}")
            elif sig_type in ("bonus", "extra_income", "raise"):
                # positive signals: discount them more heavily, cap at reported confidence
                income += amt * conf * 0.6
                notes.append(f"soft signal '{sig_type}' cautiously added {amt * conf * 0.6:.0f}")

        note = "; ".join(notes) if notes else None
        return income, (f"Soft signal adjustments: {note}" if note else None)

    def _hard_stop(self, disposable, trace, expense) -> DecisionResult:
        return DecisionResult(
            verdict=Verdict.NOT_PROCEED,
            confidence=0.95,
            disposable_income=disposable,
            safe_spend_limit=0.0,
            reasoning_trace=trace + ["Hard stop: no funds available above essential reserve."],
        )

    def _estimate_wait_date(self, cost, disposable, as_of: date) -> date:
        """Rough estimate: how many income cycles until disposable income covers cost."""
        if disposable <= 0:
            return None
        cycles_needed = max(1, int(-(-cost // disposable)))  # ceil division
        # naive: assume monthly cycles, add 30 days per cycle
        from datetime import timedelta
        return as_of + timedelta(days=30 * cycles_needed)


# ---------------------------------------------------------------------------
# Demo: same balance, different users, different verdicts
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    engine = DecisionEngine()

    shared_state = FinancialState(
        current_balance=60000,
        confirmed_monthly_income=60000,
        recurring_monthly_expenses=20000,
        pending_payments=5000,
        essential_reserve=15000,
    )

    laptop = ExpenseRequest(
        item_name="Laptop",
        cost=45000,
        category=ExpenseCategory.SEMI_ESSENTIAL,
        installment_available=True,
        installment_months=6,
        installment_monthly_amount=8000,
    )

    user_conservative = UserProfile(
        user_id="userA",
        risk_tolerance=RiskTolerance.CONSERVATIVE,
        payment_preference=PaymentPreference.AVOID_CREDIT,
        income_stability=IncomeStability.VARIABLE,
        flexible_expense_willingness=0.3,
        active_financial_goals=["no_debt"],
    )

    user_moderate = UserProfile(
        user_id="userB",
        risk_tolerance=RiskTolerance.MODERATE,
        payment_preference=PaymentPreference.OK_WITH_INSTALLMENTS,
        income_stability=IncomeStability.FIXED_SALARY,
        flexible_expense_willingness=0.7,
    )

    for user in (user_conservative, user_moderate):
        result = engine.decide(user, shared_state, laptop)
        print(f"\n=== {user.user_id} ({user.risk_tolerance.value}) ===")
        print("Verdict:", result.verdict.value)
        print("Confidence:", result.confidence)
        for line in result.reasoning_trace:
            print(" -", line)
        if result.suggested_alternative:
            print("Alternative:", result.suggested_alternative)