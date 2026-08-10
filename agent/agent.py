"""Shopping Companion — persona-memory e-commerce agent for Gemini Enterprise.

One GE agent, two memory tiers, a 5-step checkout:

  onboarding → capture country/language/gender/age/interests  (PERMANENT memory)
  step 1  What would you like to buy?   (recommendations filtered by the profile)
  step 2  Shipping address
  step 3  Payment method (cash on delivery / card)
  step 4  Order summary → type "confirm"   (dummy — no real payment)
  step 5  Done                                                 (order = 3-DAY memory)

Leave at step 2/3 and come back → the opener offers to resume where you left off.

Design: **deterministic data, LLM delivery.** The code computes the exact facts —
prices (converted to the shopper's currency), the interest-filtered picks, the
resume state — and injects them each turn; the LLM conveys them in its own fresh
words, in the shopper's language, but is told to keep every name/price/fact exact.
Nothing user-visible is a fixed template, yet no number is ever invented.

Memory lives in the deployed Agent Engine's Memory Bank via store.py (profile =
permanent, order = 3-day TTL), with a mock fallback for local runs. Typing
"clear memory" (a DEMO utility) wipes BOTH tiers.
"""
import os

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import ToolContext

from . import catalogue, order as order_mod, preferences, store

# Demo-reset phrases (deterministic — reliable for a live demo, not LLM-judged).
_CLEAR_PHRASES = (
    "clear memory", "reset memory", "clear my memory", "reset demo", "clear demo",
    "forget everything", "clear everything", "start fresh demo", "wipe memory",
)


# ── identity ─────────────────────────────────────────────────────────────────
def _uid(ctx) -> str | None:
    for getter in (lambda: ctx.user_id, lambda: ctx.session.user_id):
        try:
            v = getter()
            if v:
                return v
        except Exception:  # noqa: BLE001
            continue
    return None


# ── the deterministic facts, handed to the LLM to convey ─────────────────────
def _prefs_summary(p: dict) -> str:
    return (f"country={p.get('country')}, currency={p.get('currency_code')} "
            f"({p.get('currency_symbol')}), language={p.get('language')}, "
            f"gender={p.get('gender')}, age={p.get('age')} ({p.get('age_group')}), "
            f"interests={', '.join(p.get('interests') or []) or 'none'}")


def _order_summary_data(order: dict) -> str:
    return (f"- {order.get('product_name')} — {order.get('price_display')}\n"
            f"- Ship to: {order.get('address')}\n"
            f"- Payment: {order.get('payment_method')}")


def _shopping_context(uid, first_turn: bool) -> str:
    """Build the per-turn [Context] note: the exact data + what to do this turn.

    The LLM turns this into a fresh, natural, localized message. It must keep the
    data verbatim — that's what keeps prices/products/facts correct.
    """
    prefs = preferences.get_preferences(uid)
    order = order_mod.get_order(uid)
    active = order if order and order.get("status") == "shopping" else None
    lang = (prefs or {}).get("language", "English")

    L: list[str] = []
    if first_turn:
        L.append("This is the shopper's first message this session — open with a warm, fresh greeting.")

    if not preferences.is_complete(prefs):
        L.append("ONBOARDING: no saved profile yet. In one message, invite them to share their "
                 "country, preferred language, gender, age, and any interests; then call set_preferences.")
    else:
        L.append("Saved profile (never re-ask these): " + _prefs_summary(prefs) + ".")
        if active and order_mod.is_in_progress(active) and first_turn:
            L.append(
                f"RESUME: they have an order in progress — '{active.get('product_name')}' at "
                f"{active.get('price_display')}, at step {active.get('step')} of 5 "
                f"({order_mod.STEP_NAME.get(active.get('step'))}); "
                f"address={active.get('address') or 'not given'}, "
                f"payment={active.get('payment_method') or 'not chosen'}. "
                "Warmly offer to continue where they left off, or start over.")
        else:
            step = active.get("step", 1) if active else 1
            if step <= 1:
                L.append("STEP 1 — present these EXACT picks (keep every product name, price and ⭐ "
                         "exactly as written; include all of them; invite a choice by number or name):\n"
                         + catalogue.recommend_markdown(prefs))
            elif step == 2:
                L.append("STEP 2 — ask for their shipping address, then call set_shipping_address.")
            elif step == 3:
                L.append("STEP 3 — ask whether they'll pay by cash on delivery or card, then call set_payment_method.")
            elif step == 4:
                L.append("STEP 4 — present this order summary EXACTLY, then ask them to type 'confirm':\n"
                         + _order_summary_data(active))
        if order and order.get("status") == "confirmed" and order.get("order_id"):
            L.append(f"The last order is CONFIRMED (id {order.get('order_id')}) — if they just "
                     "confirmed, congratulate them (demo only, no real payment was taken).")

    L.append(f"Convey all of the above in your OWN natural words, in {lang} — vary your phrasing "
             "so it never feels like a fixed template. But keep every product name, price, currency "
             "symbol and order detail EXACTLY as given; never invent products, prices, or facts. "
             "Never mention tools.")
    return "[Context]\n" + "\n".join(L)


def _inject_context(callback_context: CallbackContext, llm_request: LlmRequest):
    first_turn = not callback_context.state.get("greeted")
    callback_context.state["greeted"] = True
    llm_request.append_instructions([_shopping_context(_uid(callback_context), first_turn)])
    return None


# ── tools ────────────────────────────────────────────────────────────────────
def set_preferences(country: str = "", language: str = "", gender: str = "",
                    age: str = "", interests: list[str] = None, currency: str = "",
                    tool_context: ToolContext = None) -> dict:
    """Save or UPDATE the shopper's PERMANENT profile. Use at onboarding (all
    fields) AND anytime they change a single preference later — pass ONLY the
    field(s) that changed; the rest is kept.

    Args:
        country: sets the display currency (unless `currency` overrides it).
        language: how you communicate with them.
        gender, age: used to filter recommendations.
        interests: the FULL desired interests list when changing interests.
        currency: an explicit currency code (e.g. "USD", "INR") to show prices in,
            independent of country — use when they say "show prices in USD".
    """
    rec = preferences.set_preferences(
        _uid(tool_context),
        country=country or None, language=language or None, gender=gender or None,
        age=age or None, interests=interests, currency=currency or None,
    )
    return {"status": "saved", "currency": rec["currency_code"], "age_group": rec["age_group"]}


def recommend_products(tool_context: ToolContext = None) -> dict:
    """Return the current EXACT recommendation lines (filtered by profile, priced in
    the shopper's currency) for you to present. Keep names/prices verbatim."""
    prefs = preferences.get_preferences(_uid(tool_context))
    return {"picks_markdown": catalogue.recommend_markdown(prefs)}


def select_product(product: str, tool_context: ToolContext = None) -> dict:
    """Step 1 → 2: record the chosen product (by name or number-mapped name) and
    advance to shipping address."""
    uid = _uid(tool_context)
    prefs = preferences.get_preferences(uid)
    p = catalogue.get(product)
    if not p:
        return {"status": "not_found", "product": product}
    price = catalogue.format_price(p["price_usd"], prefs)
    order_mod.set_product(uid, p["id"], p["name"], price)
    return {"status": "selected", "product": p["name"], "price": price, "next": "shipping address"}


def set_shipping_address(address: str, tool_context: ToolContext = None) -> dict:
    """Step 2 → 3: record the shipping address and advance to payment."""
    if not (address or "").strip():
        return {"status": "skipped", "reason": "empty address"}
    order_mod.set_address(_uid(tool_context), address.strip())
    return {"status": "saved", "next": "payment method"}


def set_payment_method(method: str, tool_context: ToolContext = None) -> dict:
    """Step 3 → 4: record payment method ('cash on delivery' or 'card') and advance
    to the order summary."""
    m = (method or "").strip().lower()
    norm = "cash on delivery" if ("cash" in m or "cod" in m or "delivery" in m) else "card"
    order_mod.set_payment(_uid(tool_context), norm)
    return {"status": "saved", "payment_method": norm, "next": "confirm"}


def confirm_order(tool_context: ToolContext = None) -> dict:
    """Step 4 → 5: finalize the order (demo only — no real payment). Returns the
    order id to show the shopper."""
    rec = order_mod.confirm(_uid(tool_context))
    return {"status": "confirmed", "order_id": rec.get("order_id"),
            "product": rec.get("product_name")}


# ── demo reset + identity (the only deterministic user-facing overrides) ──────
def _set_text(content, text: str) -> None:
    for p in content.parts:
        if p.text is not None:
            p.text = text
            return


def _latest_user_text(callback_context) -> str:
    uc = getattr(callback_context, "user_content", None)
    if uc and getattr(uc, "parts", None):
        return " ".join(p.text for p in uc.parts if getattr(p, "text", None)).strip()
    return ""


def _wants_clear(text: str) -> bool:
    t = (text or "").lower()
    return any(phrase in t for phrase in _CLEAR_PHRASES)


def _maybe_identity(content, uid) -> None:
    if os.getenv("SHOW_IDENTITY", "").lower() not in ("1", "true", "yes"):
        return
    line = f"\n\n_🔎 identity → user_id: {uid or 'unknown'}_"
    for p in content.parts:
        if p.text is not None:
            p.text = (p.text or "").rstrip() + line
            return


def _after_model(callback_context: CallbackContext, llm_response: LlmResponse):
    if llm_response.partial:
        return None
    content = llm_response.content
    if not content or not content.parts:
        return None
    has_text = any(p.text for p in content.parts if p.text)
    has_fc = any(p.function_call for p in content.parts if p.function_call)
    if not has_text or has_fc:  # never touch tool-call responses
        return None

    uid = _uid(callback_context)
    print(f"[identity] user_id={uid!r}")

    # DEMO reset only — deterministic so the reset is reliable. Everything else the
    # LLM conveys freshly from the injected [Context] data.
    if _wants_clear(_latest_user_text(callback_context)):
        store.clear(uid)
        callback_context.state.clear()
        _set_text(content, "🧹 **Memory cleared** — your profile and any in-progress order are "
                           "gone. Say hello to start fresh.")
    _maybe_identity(content, uid)
    return llm_response


root_agent = LlmAgent(
    name="shopping_companion_agent",
    model="gemini-2.5-flash",
    instruction=(
        "You are a warm, personable e-commerce shopping companion in Gemini Enterprise. "
        "Reply in clear markdown, in the shopper's preferred language.\n"
        "\n"
        "Each turn a [Context] note gives you the shopper's saved profile, the exact "
        "recommendation/order data to convey, and what to do this step. Speak it in your "
        "OWN words — sound fresh and human, never a fixed template — but keep every product "
        "name, price, currency symbol and order fact EXACTLY as given; never invent data, and "
        "never re-ask something already in the profile.\n"
        "\n"
        "Run the flow with your tools: set_preferences (onboarding AND any later single-field "
        "preference change — pass only what changed, use the currency arg for a currency "
        "change; re-show recommendations after a change that affects them); select_product; "
        "set_shipping_address; set_payment_method; confirm_order. Never mention tools."
    ),
    tools=[set_preferences, recommend_products, select_product,
           set_shipping_address, set_payment_method, confirm_order],
    before_model_callback=_inject_context,
    after_model_callback=_after_model,
)
