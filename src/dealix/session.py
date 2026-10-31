"""
Session Layer (Redis-backed)
-------------------------------
Purpose: let a demo look like a real chat ("can I afford this laptop?")
instead of a Postman collection. The user's UserProfile and FinancialState
are stored per user_id in Redis; each turn only needs to send the NEW
message/image, not the whole financial picture again.

Design choices:
  - Profile and state are stored as JSON blobs under simple keys
    (session:{user_id}:profile, session:{user_id}:state). Good enough for a
    hackathon; a real product would normalize this into proper tables.
  - Soft signals and itemized expenses ACCUMULATE across turns (that's the
    whole point — "boss said bonus delayed" in turn 2 should still be known
    in turn 5), but soft_signals decay by relevance rather than growing
    forever: anything older than SOFT_SIGNAL_TTL_DAYS is dropped on read,
    since "bonus delayed" from six weeks ago probably isn't still true.
  - Conversation history (last N turns) is stored separately so you can
    show a transcript in the demo UI without bloating the financial state.

This module wraps decision_engine + llm_layer + agent_response into a
single per-user "ask" call, which is what your FastAPI /chat route should
call directly.
"""

import json
import time
from dataclasses import asdict
from typing import Optional

import redis

from .decision_engine import (
    UserProfile,
    FinancialState,
    ItemizedExpense,
    ExpenseRequest,
    RiskTolerance,
    PaymentPreference,
    IncomeStability,
    ExpenseCategory,
)
from .agent_response import build_agent_response
from .llm_layer import (
    extract_from_message,
    extract_from_image,
    build_itemized_expenses_from_extraction,
    build_expense_from_extraction,
)

SOFT_SIGNAL_TTL_DAYS = 30
CONVERSATION_HISTORY_LIMIT = 20


class SessionStore:
    def __init__(self, redis_client: Optional[redis.Redis] = None):
        self.r = redis_client or redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

    # -- keys ---------------------------------------------------------
    def _profile_key(self, user_id: str) -> str:
        return f"session:{user_id}:profile"

    def _state_key(self, user_id: str) -> str:
        return f"session:{user_id}:state"

    def _history_key(self, user_id: str) -> str:
        return f"session:{user_id}:history"

    # -- profile --------------------------------------------------------
    def save_profile(self, profile: UserProfile) -> None:
        data = asdict(profile)
        self.r.set(self._profile_key(profile.user_id), json.dumps(data))

    def load_profile(self, user_id: str) -> Optional[UserProfile]:
        raw = self.r.get(self._profile_key(user_id))
        if not raw:
            return None
        d = json.loads(raw)
        return UserProfile(
            user_id=d["user_id"],
            risk_tolerance=RiskTolerance(d["risk_tolerance"]),
            payment_preference=PaymentPreference(d["payment_preference"]),
            income_stability=IncomeStability(d["income_stability"]),
            flexible_expense_willingness=d["flexible_expense_willingness"],
            category_priorities=d.get("category_priorities", {}),
            active_financial_goals=d.get("active_financial_goals", []),
        )

    # -- financial state --------------------------------------------------
    def save_state(self, user_id: str, state: FinancialState) -> None:
        d = asdict(state)
        d["as_of"] = state.as_of.isoformat()
        # attach a timestamp to each soft signal for later TTL filtering
        now = time.time()
        for sig in d.get("soft_signals", []):
            sig.setdefault("_recorded_at", now)
        self.r.set(self._state_key(user_id), json.dumps(d))

    def load_state(self, user_id: str) -> Optional[FinancialState]:
        from datetime import date as date_cls

        raw = self.r.get(self._state_key(user_id))
        if not raw:
            return None
        d = json.loads(raw)

        # drop stale soft signals
        cutoff = time.time() - SOFT_SIGNAL_TTL_DAYS * 86400
        fresh_signals = [
            s for s in d.get("soft_signals", [])
            if s.get("_recorded_at", 0) >= cutoff
        ]

        return FinancialState(
            current_balance=d["current_balance"],
            confirmed_monthly_income=d["confirmed_monthly_income"],
            recurring_monthly_expenses=d["recurring_monthly_expenses"],
            pending_payments=d["pending_payments"],
            essential_reserve=d["essential_reserve"],
            as_of=date_cls.today(),   # always "today" on read, not whenever it was saved
            soft_signals=fresh_signals,
            itemized_expenses=[
                ItemizedExpense(name=i["name"], amount=i["amount"], flexible=i["flexible"], category=i["category"])
                for i in d.get("itemized_expenses", [])
            ],
        )

    # -- conversation history (for demo transcript display) ---------------
    def append_history(self, user_id: str, role: str, content: str) -> None:
        key = self._history_key(user_id)
        entry = json.dumps({"role": role, "content": content, "ts": time.time()})
        self.r.rpush(key, entry)
        self.r.ltrim(key, -CONVERSATION_HISTORY_LIMIT, -1)

    def get_history(self, user_id: str) -> list:
        raw = self.r.lrange(self._history_key(user_id), 0, -1)
        return [json.loads(x) for x in raw]


# ---------------------------------------------------------------------------
# High-level "ask" — this is what your /chat FastAPI route calls
# ---------------------------------------------------------------------------

def ask(
    store: SessionStore,
    user_id: str,
    message: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
    image_media_type: str = "image/jpeg",
) -> dict:
    """
    One turn of conversation. Loads the user's stored profile/state,
    extracts anything new from this turn's message/image, updates and
    re-saves state, runs the decision engine, and returns the agent response.

    Raises ValueError if no profile/state exists yet for this user — call
    save_profile/save_state once at onboarding before using this.
    """
    profile = store.load_profile(user_id)
    state = store.load_state(user_id)
    if profile is None or state is None:
        raise ValueError(
            f"No stored profile/state for user_id={user_id!r}. "
            "Onboard the user first with store.save_profile(...) and store.save_state(...)."
        )

    if message:
        store.append_history(user_id, "user", message)

    extraction = {"expense": {}, "soft_signals": [], "itemized_expenses": []}
    if message:
        extraction = extract_from_message(message)
    if image_bytes:
        image_extraction = extract_from_image(image_bytes, image_media_type)
        extraction["soft_signals"] = extraction.get("soft_signals", []) + image_extraction.get("soft_signals", [])
        extraction["itemized_expenses"] = (
            extraction.get("itemized_expenses", []) + image_extraction.get("itemized_expenses", [])
        )
        if not extraction.get("expense"):
            extraction["expense"] = image_extraction.get("expense", {})

    # fold new soft signals + itemized expenses into state
    now = time.time()
    for sig in extraction.get("soft_signals", []):
        sig["_recorded_at"] = now
    state.soft_signals = state.soft_signals + extraction.get("soft_signals", [])

    new_items = build_itemized_expenses_from_extraction(extraction)
    if new_items:
        merged = {i.name.strip().lower(): i for i in state.itemized_expenses}
        for item in new_items:
            merged[item.name.strip().lower()] = item
        state.itemized_expenses = list(merged.values())

    # build the expense request for THIS turn — no persisted fallback since
    # each turn is a fresh purchase question
    empty_fallback = ExpenseRequest(item_name="unspecified", cost=0.0, category=ExpenseCategory.DISCRETIONARY)
    expense = build_expense_from_extraction(extraction, empty_fallback)

    store.save_state(user_id, state)

    if expense.cost <= 0:
        # nothing purchasable was extracted this turn — no decision to make
        reply = "I didn't catch a specific expense to evaluate — what are you looking to buy, and how much is it?"
        store.append_history(user_id, "assistant", reply)
        return {"reply": reply, "decision": None}

    response = build_agent_response(profile, state, expense, use_llm_explanation=True)
    store.append_history(user_id, "assistant", response.decision_explanation)

    return {"reply": response.decision_explanation, "decision": asdict(response)}


# ---------------------------------------------------------------------------
# Demo (uses fakeredis so it runs without a real Redis server)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import fakeredis
    from .decision_engine import DecisionEngine  # noqa: F401 (imported for parity with other demos)

    fake_client = fakeredis.FakeRedis(decode_responses=True)
    store = SessionStore(redis_client=fake_client)

    # --- onboarding (would normally happen once, e.g. at signup) ---
    profile = UserProfile(
        user_id="demo_user",
        risk_tolerance=RiskTolerance.MODERATE,
        payment_preference=PaymentPreference.OK_WITH_INSTALLMENTS,
        income_stability=IncomeStability.FIXED_SALARY,
        flexible_expense_willingness=0.6,
    )
    state = FinancialState(
        current_balance=60000,
        confirmed_monthly_income=60000,
        recurring_monthly_expenses=20000,
        pending_payments=5000,
        essential_reserve=15000,
        itemized_expenses=[
            ItemizedExpense(name="Netflix", amount=649, flexible=True, category="subscription"),
        ],
    )
    store.save_profile(profile)
    store.save_state("demo_user", state)

    # --- turn 1: a plain affordability question, no image, no LLM call needed to test the plumbing ---
    # (this DOES call the real LLM via extract_from_message / explain_decision if ANTHROPIC_API_KEY
    #  is set — this demo block will only fully run with a real key configured)
    print("Session/state plumbing is wired. To exercise a full turn end-to-end, set")
    print("ANTHROPIC_API_KEY and call:")
    print('  ask(store, "demo_user", message="Can I afford a 45000 rupee laptop, 6 installments of 8000?")')
    print()
    print("Stored profile round-trips as:", store.load_profile("demo_user"))
    print("Stored state round-trips as:", store.load_state("demo_user"))