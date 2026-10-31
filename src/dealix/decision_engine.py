"""Deterministic, auditable affordability decisions with a cash-flow forecast."""
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from math import ceil
from typing import Optional


class RiskTolerance(str, Enum):
    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class PaymentPreference(str, Enum):
    AVOID_CREDIT = "avoid_credit"
    OK_WITH_INSTALLMENTS = "ok_with_installments"
    PAY_FULL_ONLY = "pay_full_only"


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
    ESSENTIAL = "essential"
    SEMI_ESSENTIAL = "semi_essential"
    DISCRETIONARY = "discretionary"


@dataclass
class UserProfile:
    user_id: str
    risk_tolerance: RiskTolerance = RiskTolerance.MODERATE
    payment_preference: PaymentPreference = PaymentPreference.OK_WITH_INSTALLMENTS
    income_stability: IncomeStability = IncomeStability.FIXED_SALARY
    flexible_expense_willingness: float = 0.5
    category_priorities: dict = field(default_factory=dict)
    active_financial_goals: list = field(default_factory=list)
    preferred_minimum_balance: float = 0.0


@dataclass
class ItemizedExpense:
    name: str
    amount: float
    flexible: bool
    category: str = "discretionary"


@dataclass
class PendingPayment:
    amount: float
    due_date: date
    description: str = "Pending payment"


@dataclass
class FinancialState:
    current_balance: float
    confirmed_monthly_income: float
    recurring_monthly_expenses: float
    pending_payments: float
    essential_reserve: float
    as_of: date = field(default_factory=date.today)
    soft_signals: list = field(default_factory=list)
    itemized_expenses: list = field(default_factory=list)
    next_income_date: Optional[date] = None
    pending_payment_items: list = field(default_factory=list)


@dataclass
class ExpenseRequest:
    item_name: str
    cost: float
    category: ExpenseCategory = ExpenseCategory.DISCRETIONARY
    installment_available: bool = False
    installment_months: Optional[int] = None
    installment_monthly_amount: Optional[float] = None
    urgency: str = "normal"


@dataclass
class DecisionResult:
    verdict: Verdict
    confidence: float
    disposable_income: float
    safe_spend_limit: float
    reasoning_trace: list
    suggested_wait_until: Optional[date] = None
    suggested_alternative: Optional[str] = None
    available_balance: float = 0.0
    partial_amount: Optional[float] = None
    payment_plan: list = field(default_factory=list)
    earliest_date_for_full_payment: Optional[date] = None
    spending_changes_needed: list = field(default_factory=list)


SAFE_SPEND_PCT = {
    RiskTolerance.CONSERVATIVE: 0.25,
    RiskTolerance.MODERATE: 0.40,
    RiskTolerance.AGGRESSIVE: 0.60,
}
INCOME_STABILITY_BUFFER = {
    IncomeStability.FIXED_SALARY: 0.0,
    IncomeStability.VARIABLE: 0.10,
    IncomeStability.IRREGULAR: 0.20,
}


class DecisionEngine:
    """Try payment strategies in order; accept only plans safe at every forecast event."""

    def decide(self, profile: UserProfile, state: FinancialState, expense: ExpenseRequest) -> DecisionResult:
        self._validate(profile, state, expense)
        trace = []
        income = self._adjusted_income(state, trace)
        monthly_surplus = income - state.recurring_monthly_expenses - state.pending_payments
        stability_buffer = INCOME_STABILITY_BUFFER[profile.income_stability]
        monthly_surplus = max(0.0, monthly_surplus * (1 - stability_buffer))
        trace.append(f"Forecast monthly surplus after recurring expenses, pending payments, and income-stability buffer: {monthly_surplus:.2f}.")
        floor = max(state.essential_reserve, profile.preferred_minimum_balance)
        cash_available = max(0.0, state.current_balance - floor - self._due_now_pending(state))
        safe_pct = SAFE_SPEND_PCT[profile.risk_tolerance]
        if expense.category == ExpenseCategory.ESSENTIAL:
            safe_pct = max(safe_pct, 0.70)
        elif expense.category == ExpenseCategory.DISCRETIONARY:
            safe_pct *= profile.flexible_expense_willingness
        priority = profile.category_priorities.get(expense.category.value)
        if priority == "high":
            safe_pct = min(1.0, safe_pct * 1.3)
        elif priority == "low":
            safe_pct *= 0.6
        safe_spend_limit = min(cash_available, monthly_surplus * safe_pct)
        trace.append(f"Protected balance floor is {floor:.2f}; immediately available cash is {cash_available:.2f}; personalized safe-spend cap is {safe_spend_limit:.2f}.")

        if expense.cost <= safe_spend_limit and self._plan_is_safe(profile, state, [(state.as_of, expense.cost)]):
            plan = [(state.as_of, expense.cost, "Full payment")]
            trace.append("Full payment passes the complete cash-flow forecast and preserves the preferred reserve.")
            return self._result(Verdict.PAY_IN_FULL, .95, monthly_surplus, safe_spend_limit, trace, state, expense, plan, expense.cost)

        if self._installments_allowed(profile, expense):
            plan = self._installment_plan(state, expense)
            monthly_amount = expense.installment_monthly_amount or expense.cost / max(expense.installment_months or 1, 1)
            if plan and monthly_amount <= monthly_surplus * safe_pct and self._plan_is_safe(profile, state, [(d, a) for d, a, _ in plan]):
                trace.append("The complete installment schedule passes every forecast balance-floor check.")
                return self._result(Verdict.USE_INSTALLMENTS, .88, monthly_surplus, safe_spend_limit, trace, state, expense, plan, plan[0][1], f"Use {len(plan)} scheduled installments.")
            trace.append("Installments were unavailable, disallowed by the user's preferences/goals, or failed the full forecast.")

        if profile.payment_preference != PaymentPreference.PAY_FULL_ONLY and safe_spend_limit > 0:
            initial = min(safe_spend_limit, expense.cost)
            future_date = self._find_safe_full_payment_date(profile, state, expense.cost, initial)
            if initial < expense.cost and future_date:
                plan = [(state.as_of, initial, "Initial payment"), (future_date, expense.cost - initial, "Remaining balance")]
                if self._plan_is_safe(profile, state, [(d, a) for d, a, _ in plan]):
                    trace.append("Partial payment and the dated remaining balance both pass the complete forecast.")
                    return self._result(Verdict.PAY_PARTIALLY, .78, monthly_surplus, safe_spend_limit, trace, state, expense, plan, initial, "Pay a safe amount now and the remaining balance on the forecast-safe date.")

        if expense.urgency != "urgent":
            wait_date = self._find_safe_full_payment_date(profile, state, expense.cost, 0.0)
            if wait_date:
                trace.append(f"Paying now is unsafe; the first forecast-safe full-payment date is {wait_date.isoformat()}.")
                plan = [(wait_date, expense.cost, "Full payment after funds recover")]
                return self._result(Verdict.WAIT, .82, monthly_surplus, safe_spend_limit, trace, state, expense, plan, 0.0, "Wait until the forecast-safe date before paying in full.", wait_date)

        changes = self._suggest_spending_changes(state, expense.cost - safe_spend_limit)
        trace.append("No payment strategy currently satisfies the reserve and full-plan safety checks.")
        return DecisionResult(Verdict.NOT_PROCEED, .93, monthly_surplus, safe_spend_limit, trace,
                              available_balance=cash_available, spending_changes_needed=changes)

    @staticmethod
    def _validate(profile, state, expense):
        values = (state.current_balance, state.confirmed_monthly_income, state.recurring_monthly_expenses,
                  state.pending_payments, state.essential_reserve, expense.cost, profile.preferred_minimum_balance)
        if any(v < 0 for v in values):
            raise ValueError("Balances, expenses, income, reserves, and purchase cost must be non-negative.")
        if not 0 <= profile.flexible_expense_willingness <= 1:
            raise ValueError("flexible_expense_willingness must be between 0 and 1.")
        if expense.cost <= 0:
            raise ValueError("Purchase cost must be greater than zero.")
        if expense.installment_months is not None and expense.installment_months < 1:
            raise ValueError("installment_months must be at least 1.")

    @staticmethod
    def _adjusted_income(state, trace):
        income = state.confirmed_monthly_income
        for signal in state.soft_signals:
            amount = max(0.0, float(signal.get("amount", 0)))
            confidence = min(1.0, max(0.0, float(signal.get("confidence", 0.5))))
            kind = signal.get("type")
            if kind in ("income_delay", "income_loss", "unexpected_expense"):
                income -= amount * max(confidence, 0.7)
            elif kind in ("bonus", "extra_income", "raise"):
                income += amount * confidence * 0.6
        adjusted = max(0.0, income)
        if state.soft_signals:
            trace.append(f"Message/image signals conservatively adjusted confirmed monthly income from {state.confirmed_monthly_income:.2f} to {adjusted:.2f}.")
        return adjusted

    @staticmethod
    def _due_now_pending(state):
        if not state.pending_payment_items:
            return state.pending_payments
        return sum(p.amount for p in state.pending_payment_items if p.due_date <= state.as_of)

    @staticmethod
    def _installments_allowed(profile, expense):
        return (expense.installment_available and expense.installment_months and
                profile.payment_preference == PaymentPreference.OK_WITH_INSTALLMENTS and
                "no_debt" not in profile.active_financial_goals)

    @staticmethod
    def _installment_plan(state, expense):
        months = expense.installment_months or 0
        if months < 1:
            return []
        amount = expense.installment_monthly_amount or expense.cost / months
        if amount <= 0 or amount * months + 1e-6 < expense.cost:
            return []
        plan, paid = [], 0.0
        for index in range(months):
            remaining = expense.cost - paid
            payment = min(amount, remaining)
            paid += payment
            plan.append((state.as_of + timedelta(days=30 * index), payment, f"Installment {index + 1} of {months}"))
            if paid >= expense.cost:
                break
        return plan if paid + 1e-6 >= expense.cost else []

    def _plan_is_safe(self, profile, state, plan):
        floor = max(state.essential_reserve, profile.preferred_minimum_balance)
        if state.current_balance - floor - self._due_now_pending(state) < -1e-6:
            return False
        horizon = max([state.as_of + timedelta(days=365)] + [d for d, _ in plan])
        events = {}
        for d, amount in plan:
            events.setdefault(d, [0.0, 0.0])[0] += amount
        for payment in state.pending_payment_items:
            if state.as_of < payment.due_date <= horizon:
                events.setdefault(payment.due_date, [0.0, 0.0])[0] += payment.amount
        forecast_income = state.confirmed_monthly_income
        for signal in state.soft_signals:
            amount = max(0.0, float(signal.get("amount", 0)))
            confidence = min(1.0, max(0.0, float(signal.get("confidence", 0.5))))
            if signal.get("type") in ("income_delay", "income_loss", "unexpected_expense"):
                forecast_income -= amount * max(confidence, 0.7)
            elif signal.get("type") in ("bonus", "extra_income", "raise"):
                forecast_income += amount * confidence * 0.6
        forecast_income = max(0.0, forecast_income) * (1 - INCOME_STABILITY_BUFFER[profile.income_stability])
        income_date = state.next_income_date or (state.as_of + timedelta(days=30))
        while income_date <= horizon:
            events.setdefault(income_date, [0.0, 0.0])[1] += forecast_income
            income_date += timedelta(days=30)
        recurring_date = state.as_of + timedelta(days=30)
        while recurring_date <= horizon:
            events.setdefault(recurring_date, [0.0, 0.0])[0] += state.recurring_monthly_expenses
            recurring_date += timedelta(days=30)
        balance = state.current_balance
        # Known due-now payments are charged before planned payments.
        if state.pending_payment_items:
            balance -= sum(p.amount for p in state.pending_payment_items if p.due_date <= state.as_of)
        else:
            balance -= state.pending_payments
        for event_date in sorted(events):
            if event_date <= state.as_of:
                continue
            debits, credits = events[event_date]
            balance -= debits
            if balance + 1e-6 < floor:
                return False
            balance += credits
        return True

    def _find_safe_full_payment_date(self, profile, state, cost, initial):
        for month in range(1, 25):
            candidate = state.as_of + timedelta(days=30 * month)
            plan = []
            if initial:
                plan.append((state.as_of, initial))
            plan.append((candidate, max(0.0, cost - initial)))
            if self._plan_is_safe(profile, state, plan):
                return candidate
        return None

    @staticmethod
    def _suggest_spending_changes(state, gap):
        changes, freed = [], 0.0
        for item in sorted((x for x in state.itemized_expenses if x.flexible and x.amount > 0), key=lambda x: x.amount, reverse=True):
            if freed >= gap:
                break
            amount = min(item.amount, gap - freed)
            changes.append({"expense_name": item.name, "current_amount": item.amount,
                            "action": "pause" if amount >= item.amount else "reduce",
                            "monthly_savings": round(amount, 2)})
            freed += amount
        return changes

    def _result(self, verdict, confidence, surplus, safe_limit, trace, state, expense, plan, safe_today, alternative=None, wait_date=None):
        entries = [{"date": d.isoformat(), "amount": round(a, 2), "label": label} for d, a, label in plan]
        full_date = plan[-1][0] if plan else None
        return DecisionResult(verdict, confidence, surplus, safe_limit, trace, wait_date, alternative,
                              max(0.0, state.current_balance - max(state.essential_reserve, 0.0)),
                              safe_today if verdict == Verdict.PAY_PARTIALLY else None,
                              entries, full_date, [])
