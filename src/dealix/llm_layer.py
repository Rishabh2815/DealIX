"""
LLM Layer for the Financial Affordability Agent
-------------------------------------------------
Two narrow, single-purpose LLM responsibilities. Neither one does math or
makes the affordability call — that stays in decision_engine.py.

  1. EXTRACTION   : unstructured text/image  ->  structured fields
                     (soft_signals, expense details, user statements)
  2. EXPLANATION  : DecisionResult (already computed)  ->  natural language
                     answer, personalized to the user's tone/context

Why split it this way:
  - Extraction errors are visible and correctable (you can log/inspect the
    JSON before it touches the decision engine).
  - The explanation prompt never has access to raw balance arithmetic to
    get wrong — it's handed the *already correct* numbers and just explains
    them. This is the difference between "an LLM wrapper" and "an agent
    with a reliable core," which is exactly what judges are trained to
    distinguish between.

Swap ANTHROPIC calls for whichever SDK you're using in the actual build;
the prompts/schemas are the part worth keeping.
"""

import json
import base64
from typing import Optional
import anthropic

from .decision_engine import (
    DecisionResult,
    UserProfile,
    FinancialState,
    ExpenseRequest,
    ExpenseCategory,
)

client = anthropic.Anthropic()
MODEL = "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# 1. EXTRACTION — text messages -> soft signals / expense details
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """You extract structured financial facts from a user's message.
You NEVER make affordability judgments or recommendations — only extraction.

Respond with ONLY a JSON object, no prose, no markdown fences, matching this schema:

{
  "expense": {
    "item_name": string or null,
    "cost": number or null,
    "category": one of ["essential", "semi_essential", "discretionary"] or null,
    "installment_available": boolean or null,
    "installment_months": integer or null,
    "installment_monthly_amount": number or null,
    "urgency": one of ["normal", "urgent", "can_defer"] or null
  },
  "soft_signals": [
    {
      "type": one of ["income_delay", "income_loss", "unexpected_expense", "bonus", "extra_income", "raise"],
      "amount": number,
      "confidence": number between 0 and 1,
      "source_snippet": string  // the exact phrase that implied this
    }
  ]
}

Rules:
- If a field isn't mentioned, use null (for expense fields) or omit it (for signals).
- confidence should reflect how explicit/certain the statement was.
  "my boss said the bonus might be delayed" -> confidence ~0.5
  "my salary got delayed, confirmed by HR" -> confidence ~0.9
- Never invent numbers that weren't stated or clearly implied.
- If the message contains no relevant financial information, return
  {"expense": {}, "soft_signals": []}.
"""


def extract_from_message(message_text: str) -> dict:
    """Structured extraction from a free-text user message."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": message_text}],
    )
    return _parse_json_response(response)


def extract_from_image(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    """
    Structured extraction from a receipt / bank statement / payment app
    screenshot. Uses the same schema as extract_from_message, plus a
    couple of image-specific fields.
    """
    image_system_prompt = EXTRACTION_SYSTEM_PROMPT + """

This input is an IMAGE (receipt, bank statement, or payment screenshot).
Also look for:
- Recurring payments visible in a statement (subscriptions, EMIs) -> treat
  amounts as part of "recurring_expenses_hint" (add this key at the top
  level of the JSON, a number or null, alongside "expense" and "soft_signals").
- If the image is unreadable or irrelevant, return the same empty-schema
  response and add "extraction_warning": "<why>" at the top level.
"""
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        system=image_system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64_image},
                    },
                    {"type": "text", "text": "Extract the structured financial data from this image."},
                ],
            }
        ],
    )
    return _parse_json_response(response)


def _parse_json_response(response) -> dict:
    text = "".join(block.text for block in response.content if hasattr(block, "text"))
    cleaned = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # fail safe: treat as no extractable info rather than crashing the pipeline
        return {"expense": {}, "soft_signals": [], "extraction_warning": "failed_to_parse"}


def merge_extraction_into_state(state: FinancialState, extraction: dict) -> FinancialState:
    """Fold extracted soft signals into an existing FinancialState."""
    new_signals = extraction.get("soft_signals", [])
    state.soft_signals = state.soft_signals + new_signals
    return state


def build_expense_from_extraction(extraction: dict, fallback: ExpenseRequest) -> ExpenseRequest:
    """Overlay extracted expense fields onto a fallback/default ExpenseRequest."""
    exp = extraction.get("expense", {}) or {}
    return ExpenseRequest(
        item_name=exp.get("item_name") or fallback.item_name,
        cost=exp.get("cost") if exp.get("cost") is not None else fallback.cost,
        category=ExpenseCategory(exp["category"]) if exp.get("category") else fallback.category,
        installment_available=exp.get("installment_available", fallback.installment_available),
        installment_months=exp.get("installment_months") or fallback.installment_months,
        installment_monthly_amount=exp.get("installment_monthly_amount") or fallback.installment_monthly_amount,
        urgency=exp.get("urgency") or fallback.urgency,
    )


# ---------------------------------------------------------------------------
# 2. EXPLANATION — DecisionResult -> personalized natural-language answer
# ---------------------------------------------------------------------------

EXPLANATION_SYSTEM_PROMPT = """You explain a financial affordability decision to a user in a warm,
direct, non-judgmental tone. The decision and all numbers have ALREADY been
computed correctly by a separate system — your job is ONLY to explain them
clearly and personally. Do not recompute, second-guess, or change the verdict
or any number. Do not invent numbers not given to you.

Structure your answer as:
1. A direct one-line verdict (can they buy it or not, and how)
2. The key reason, referencing their specific situation (not generic advice)
3. If relevant, the concrete alternative (installment plan, wait date, partial amount)

Keep it to 3-5 sentences. Speak to the user directly ("you"), not about them.
Do not mention "the system" or "the engine" or sound like you're reading a report.
"""


def explain_decision(
    result: DecisionResult,
    profile: UserProfile,
    expense: ExpenseRequest,
) -> str:
    context = {
        "item": expense.item_name,
        "cost": expense.cost,
        "verdict": result.verdict.value,
        "confidence": result.confidence,
        "disposable_income": result.disposable_income,
        "safe_spend_limit": result.safe_spend_limit,
        "suggested_alternative": result.suggested_alternative,
        "suggested_wait_until": str(result.suggested_wait_until) if result.suggested_wait_until else None,
        "reasoning_trace": result.reasoning_trace,
        "user_risk_tolerance": profile.risk_tolerance.value,
        "user_payment_preference": profile.payment_preference.value,
        "user_goals": profile.active_financial_goals,
    }

    response = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=EXPLANATION_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Here is the computed decision context:\n{json.dumps(context, indent=2)}\n\n"
                           f"Write the user-facing explanation now.",
            }
        ],
    )
    return "".join(block.text for block in response.content if hasattr(block, "text"))


# ---------------------------------------------------------------------------
# End-to-end glue (for reference — your FastAPI route will look like this)
# ---------------------------------------------------------------------------

def handle_user_query(
    profile: UserProfile,
    state: FinancialState,
    fallback_expense: ExpenseRequest,
    message_text: Optional[str] = None,
    image_bytes: Optional[bytes] = None,
):
    from .decision_engine import DecisionEngine

    extraction = {"expense": {}, "soft_signals": []}
    if message_text:
        extraction = extract_from_message(message_text)
    if image_bytes:
        image_extraction = extract_from_image(image_bytes)
        extraction["soft_signals"] = extraction.get("soft_signals", []) + image_extraction.get("soft_signals", [])
        # prefer image-extracted expense fields if message didn't have them
        if not extraction.get("expense"):
            extraction["expense"] = image_extraction.get("expense", {})

    state = merge_extraction_into_state(state, extraction)
    expense = build_expense_from_extraction(extraction, fallback_expense)

    engine = DecisionEngine()
    result = engine.decide(profile, state, expense)

    explanation = explain_decision(result, profile, expense)

    return {
        "verdict": result.verdict.value,
        "explanation": explanation,
        "reasoning_trace": result.reasoning_trace,   # expose this in the UI
        "suggested_alternative": result.suggested_alternative,
    }