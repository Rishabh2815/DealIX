from datetime import date, timedelta

from dealix.decision_engine import (DecisionEngine, ExpenseCategory, ExpenseRequest, FinancialState,
                                    ItemizedExpense, PaymentPreference, PendingPayment, UserProfile, Verdict)
from dealix.agent_response import build_agent_response


TODAY = date(2025, 1, 1)


def profile(**kwargs):
    values = {"user_id": "test", "payment_preference": PaymentPreference.OK_WITH_INSTALLMENTS}
    values.update(kwargs)
    return UserProfile(**values)


def state(**kwargs):
    defaults = dict(current_balance=10000, confirmed_monthly_income=5000,
                    recurring_monthly_expenses=2000, pending_payments=500,
                    essential_reserve=3000, as_of=TODAY)
    defaults.update(kwargs)
    return FinancialState(**defaults)


def test_full_pay_preserves_reserve_and_returns_contract():
    result = build_agent_response(profile(), state(current_balance=50000),
                                  ExpenseRequest("Phone", 1000, ExpenseCategory.SEMI_ESSENTIAL))
    assert result.affordability_status == "affordable_now"
    assert result.amount_safe_to_pay == 1000
    assert result.payment_plan[0]["date"] == TODAY.isoformat()
    assert result.earliest_date_for_full_payment == TODAY.isoformat()


def test_pending_bill_before_installment_makes_whole_plan_unsafe():
    s = state(current_balance=6000, confirmed_monthly_income=0,
              recurring_monthly_expenses=0, pending_payments=0,
              essential_reserve=3000,
              pending_payment_items=[PendingPayment(2500, TODAY + timedelta(days=10), "rent")])
    exp = ExpenseRequest("Laptop", 2000, ExpenseCategory.SEMI_ESSENTIAL,
                         installment_available=True, installment_months=2,
                         installment_monthly_amount=1000)
    result = DecisionEngine().decide(profile(), s, exp)
    assert result.verdict == Verdict.NOT_PROCEED


def test_installments_must_repay_full_price_and_respect_user_no_credit():
    s = state(current_balance=10000)
    bad_offer = ExpenseRequest("Laptop", 3000, ExpenseCategory.SEMI_ESSENTIAL,
                               installment_available=True, installment_months=3,
                               installment_monthly_amount=500)
    assert DecisionEngine().decide(profile(), s, bad_offer).verdict != Verdict.USE_INSTALLMENTS
    good_offer = ExpenseRequest("Laptop", 3000, ExpenseCategory.SEMI_ESSENTIAL,
                                installment_available=True, installment_months=3,
                                installment_monthly_amount=1000)
    no_credit = profile(payment_preference=PaymentPreference.AVOID_CREDIT)
    assert DecisionEngine().decide(no_credit, s, good_offer).verdict != Verdict.USE_INSTALLMENTS


def test_minimum_balance_and_income_signal_are_respected():
    p = profile(preferred_minimum_balance=9000)
    s = state(current_balance=10000, confirmed_monthly_income=10000,
              recurring_monthly_expenses=0, pending_payments=0, essential_reserve=1000,
              soft_signals=[{"type": "income_delay", "amount": 9000, "confidence": .9}])
    result = DecisionEngine().decide(p, s, ExpenseRequest("TV", 1500, ExpenseCategory.SEMI_ESSENTIAL))
    assert result.verdict != Verdict.PAY_IN_FULL
    assert result.safe_spend_limit <= 1000


def test_wait_date_includes_forecast_and_plan_is_dated():
    s = state(current_balance=4000, confirmed_monthly_income=5000,
              recurring_monthly_expenses=1000, pending_payments=0, essential_reserve=3000)
    result = DecisionEngine().decide(profile(payment_preference=PaymentPreference.PAY_FULL_ONLY), s,
                                     ExpenseRequest("Laptop", 5000, ExpenseCategory.SEMI_ESSENTIAL))
    assert result.verdict == Verdict.WAIT
    assert result.earliest_date_for_full_payment is not None
    assert result.payment_plan[-1]["amount"] == 5000
    assert result.payment_plan[-1]["date"] == result.earliest_date_for_full_payment.isoformat()


def test_future_forecast_uses_income_reduced_by_message_signals():
    s = state(current_balance=5000, confirmed_monthly_income=5000,
              recurring_monthly_expenses=0, pending_payments=0, essential_reserve=3000,
              soft_signals=[{"type": "income_loss", "amount": 5000, "confidence": 1.0}])
    exp = ExpenseRequest("Laptop", 4000, ExpenseCategory.SEMI_ESSENTIAL,
                         installment_available=True, installment_months=4,
                         installment_monthly_amount=1000)
    result = DecisionEngine().decide(profile(), s, exp)
    assert result.verdict != Verdict.USE_INSTALLMENTS


def test_due_bills_are_paid_before_same_day_income_is_counted():
    s = state(current_balance=5000, confirmed_monthly_income=5000,
              recurring_monthly_expenses=0, pending_payments=0, essential_reserve=3000,
              pending_payment_items=[PendingPayment(2500, TODAY + timedelta(days=30), "rent")])
    exp = ExpenseRequest("Laptop", 4000, ExpenseCategory.SEMI_ESSENTIAL,
                         installment_available=True, installment_months=2,
                         installment_monthly_amount=2000)
    result = DecisionEngine().decide(profile(), s, exp)
    assert result.verdict != Verdict.USE_INSTALLMENTS


def test_flexible_reductions_never_suggest_essentials():
    s = state(current_balance=3000, confirmed_monthly_income=0,
              recurring_monthly_expenses=0, pending_payments=0, essential_reserve=3000,
              itemized_expenses=[ItemizedExpense("Rent", 1000, False),
                                 ItemizedExpense("Dining", 200, True)])
    result = DecisionEngine().decide(profile(), s, ExpenseRequest("TV", 1000))
    assert result.verdict == Verdict.NOT_PROCEED
    assert [x["expense_name"] for x in result.spending_changes_needed] == ["Dining"]
