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


def _name_from_uid(uid: str | None) -> str:
    """Best-effort first name from the GE identity (usually an email). Returns ''
    when it isn't cleanly derivable (e.g. contains digits) so we don't greet with
    something odd."""
    if not uid:
        return ""
    parts = uid.split("@")[0].replace(".", " ").replace("_", " ").split()
    return parts[0].capitalize() if parts and parts[0].isalpha() else ""


# ── the deterministic facts, handed to the LLM to convey ─────────────────────
def _prefs_summary(p: dict) -> str:
    return (f"country={p.get('country')}, currency={p.get('currency_code')} "
            f"({p.get('currency_symbol')}), language={p.get('language')}, "
            f"gender={p.get('gender')}, age={p.get('age')} ({p.get('age_group')}), "
            f"interests={', '.join(p.get('interests') or []) or 'none'}")


def _order_summary_data(order: dict) -> str:
    return (f"- 📦 {order.get('product_name')} — {order.get('price_display')}\n"
            f"- 🏠 Ship to: {order.get('address')}\n"
            f"- 💳 Payment: {order.get('payment_method')}")


def _shopping_context(uid, first_turn: bool, shopping_for: dict | None = None) -> str:
    """Build the per-turn [Context] note: the exact data + what to do this turn.

    The LLM turns this into a fresh, natural, localized message. It must keep the
    data verbatim — that's what keeps prices/products/facts correct.

    `shopping_for` (transient, from session state) means the shopper is buying for
    someone else — recommendations are filtered by that person's gender/age instead.
    """
    prefs = preferences.get_preferences(uid)
    order = order_mod.get_order(uid)
    active = order if order and order.get("status") == "shopping" else None
    lang = (prefs or {}).get("language", "English")

    L: list[str] = []
    email_name = _name_from_uid(uid)
    nickname = (prefs or {}).get("name")
    display_name = nickname or email_name
    if display_name:
        L.append(f"The shopper's preferred name is {display_name} — address them warmly by name "
                 "(naturally, not every line).")
    if first_turn:
        L.append("This is the shopper's first message this session — open with a warm, fresh greeting.")

    if not preferences.is_complete(prefs):
        onboarding = ("ONBOARDING: no saved profile yet. In one message, invite them to share their "
                      "country, preferred language, gender, age, and any interests; then call set_preferences.")
        # Proactive name handling at onboarding (stored in permanent memory):
        #  - no usable name from the email (e.g. it has digits) → ask what to call them
        #  - name is long → offer a shorter nickname
        if not nickname:
            if not email_name:
                onboarding += (" Also — I don't have a name for them yet, so warmly ask what they'd "
                               "like to be called, and save it via set_preferences(name=...).")
            elif len(email_name) > 5:
                onboarding += (f" Also — the name from their email ('{email_name}') is a bit long, so "
                               "warmly ask if they'd like you to use a shorter nickname, and save it via "
                               "set_preferences(name=...).")
        L.append(onboarding)
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
                if shopping_for:
                    label = shopping_for.get("label", "them")
                    recs = catalogue.recommend_markdown(
                        prefs, gender=shopping_for.get("gender", ""),
                        age_group=shopping_for.get("age_group", "any"),
                        interests=shopping_for.get("interests", []), for_label=label)
                    L.append(f"STEP 1 — the shopper is buying for {label} (NOT themselves), so these "
                             f"picks are filtered for {label}. Present them EXACTLY now (don't say "
                             "you'll fetch them); invite a choice by number or name. They can say "
                             "'for myself' to switch back to their own picks:\n" + recs)
                else:
                    L.append("STEP 1 — In THIS reply, present these EXACT picks now — actually show the "
                             "list, do NOT say you'll fetch/find them. Keep every product name, price and "
                             "⭐ exactly as written; include all of them; invite a choice by number or name. "
                             "If they say they're shopping for someone else (e.g. their kid or wife), use "
                             "browse_for:\n" + catalogue.recommend_markdown(prefs))
            elif step == 2:
                du = prefs.get("default_address")
                if du:
                    L.append(f"STEP 2 — they usually ship to '{du}'. PROACTIVELY offer to send it "
                             "there again (they can just confirm), or take a new address. Either way "
                             "call set_shipping_address before moving on.")
                else:
                    L.append("STEP 2 — ask for their shipping address; when they give it you MUST call "
                             "set_shipping_address to save it before moving on.")
            elif step == 3:
                dp = prefs.get("default_payment")
                if dp:
                    L.append(f"STEP 3 — they usually pay by {dp}. PROACTIVELY offer that as the "
                             "default (they can confirm or switch); then call set_payment_method.")
                else:
                    L.append("STEP 3 — ask cash on delivery or card; when they choose you MUST call "
                             "set_payment_method to save it before moving on.")
            elif step == 4:
                L.append("STEP 4 — present this order summary EXACTLY, then ask them to type 'confirm'; "
                         "when they confirm you MUST call confirm_order:\n"
                         + _order_summary_data(active))

    L.append(f"Convey all of the above in your OWN natural words, in {lang} — vary your phrasing "
             "so it never feels like a fixed template. But keep every product name, price, currency "
             "symbol and order detail EXACTLY as given; never invent products, prices, or facts. "
             "Never mention tools.")
    return "[Context]\n" + "\n".join(L)


def _inject_context(callback_context: CallbackContext, llm_request: LlmRequest):
    first_turn = not callback_context.state.get("greeted")
    callback_context.state["greeted"] = True
    shopping_for = callback_context.state.get("shopping_for")
    llm_request.append_instructions(
        [_shopping_context(_uid(callback_context), first_turn, shopping_for)])
    return None


# ── tools ────────────────────────────────────────────────────────────────────
def set_preferences(country: str = "", language: str = "", gender: str = "",
                    age: str = "", interests: list[str] = None, currency: str = "",
                    name: str = "", tool_context: ToolContext = None) -> dict:
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
        name: the shopper's preferred name / nickname to address them by — save it
            when they give one (e.g. after you offer a shorter nickname at onboarding).
    """
    rec = preferences.set_preferences(
        _uid(tool_context),
        country=country or None, language=language or None, gender=gender or None,
        age=age or None, interests=interests, currency=currency or None, name=name or None,
    )
    return {"status": "saved", "currency": rec["currency_code"], "age_group": rec["age_group"]}


def recommend_products(tool_context: ToolContext = None) -> dict:
    """Return the current EXACT recommendation lines (filtered by profile, priced in
    the shopper's currency) for you to present. Keep names/prices verbatim."""
    prefs = preferences.get_preferences(_uid(tool_context))
    sf = tool_context.state.get("shopping_for") if tool_context else None
    if sf:
        return {"picks_markdown": catalogue.recommend_markdown(
            prefs, gender=sf.get("gender", ""), age_group=sf.get("age_group", "any"),
            interests=sf.get("interests", []), for_label=sf.get("label", "them"))}
    return {"picks_markdown": catalogue.recommend_markdown(prefs)}


def browse_for(recipient: str = "", gender: str = "", age: str = "",
               interests: list[str] = None, tool_context: ToolContext = None) -> dict:
    """Show recommendations for SOMEONE ELSE the shopper is buying for (e.g. their
    kid or wife), filtered by THAT person's gender/age instead of the shopper's own
    profile. Call this whenever the shopper says they're shopping for another person.
    Pass recipient='myself' to switch back to the shopper's own picks.

    Args:
        recipient: who they're shopping for, e.g. "my daughter", "my wife", "my kid".
        gender: that person's gender (male/female) if known; leave blank if not.
        age: that person's age (a number) if known; leave blank if not.
        interests: the RECIPIENT's interests if the shopper mentions them (e.g.
            "my son loves gaming" → ["gaming"]). These rank the recipient's picks
            (⭐) — do NOT pass the shopper's own interests here.
    """
    uid = _uid(tool_context)
    prefs = preferences.get_preferences(uid)
    label = (recipient or "").strip()
    if label.lower().replace("for ", "") in ("me", "myself", "self", ""):
        if tool_context:
            tool_context.state.pop("shopping_for", None)
        return {"picks_markdown": catalogue.recommend_markdown(prefs), "shopping_for": "myself"}
    g = (gender or "").strip().lower()
    ag = preferences.age_group(age) if str(age).strip() else "any"
    ints = [i.strip() for i in (interests or []) if i and i.strip()]
    if tool_context:
        tool_context.state["shopping_for"] = {"label": label, "gender": g,
                                              "age_group": ag, "interests": ints}
    return {"picks_markdown": catalogue.recommend_markdown(
        prefs, gender=g, age_group=ag, interests=ints, for_label=label), "shopping_for": label}


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
    """Step 2 → 3: record the shipping address and advance to payment. Also
    remembers it as the shopper's usual address for future orders."""
    if not (address or "").strip():
        return {"status": "skipped", "reason": "empty address"}
    uid = _uid(tool_context)
    order_mod.set_address(uid, address.strip())
    preferences.save_defaults(uid, address=address.strip())  # remember for next time
    return {"status": "saved", "next": "payment method"}


def set_payment_method(method: str, tool_context: ToolContext = None) -> dict:
    """Step 3 → 4: record payment method ('cash on delivery' or 'card') and advance
    to the order summary. Also remembers it as the shopper's usual payment method."""
    m = (method or "").strip().lower()
    norm = "cash on delivery" if ("cash" in m or "cod" in m or "delivery" in m) else "card"
    uid = _uid(tool_context)
    order_mod.set_payment(uid, norm)
    preferences.save_defaults(uid, payment=norm)  # remember for next time
    return {"status": "saved", "payment_method": norm, "next": "confirm"}


def confirm_order(tool_context: ToolContext = None) -> dict:
    """Step 4 → 5: finalize the order (demo only — no real payment). Returns the
    order id to show the shopper."""
    rec = order_mod.confirm(_uid(tool_context))
    if tool_context:  # next order defaults back to shopping for themselves
        tool_context.state.pop("shopping_for", None)
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
        "Reply in clear markdown, in the shopper's preferred language, and address the shopper "
        "by name when the [Context] provides one.\n"
        "\n"
        "Each turn a [Context] note gives you the shopper's saved profile, the exact "
        "recommendation/order data to convey, and what to do this step. Speak it in your "
        "OWN words — sound fresh and human, never a fixed template — but keep every product "
        "name, price, currency symbol and order fact EXACTLY as given; never invent data, and "
        "never re-ask something already in the profile.\n"
        "\n"
        "Run the flow with your tools: set_preferences (onboarding AND any later single-field "
        "preference change — pass only what changed, use the currency arg for a currency change); "
        "browse_for (when they're shopping for someone ELSE like their kid or wife — pass that "
        "person's gender/age; recipient='myself' switches back); select_product; "
        "set_shipping_address; set_payment_method; confirm_order.\n"
        "\n"
        "Important behaviours: (a) after ANY preference change that affects recommendations, "
        "IMMEDIATELY show the updated picks from [Context] in the SAME reply — never say you'll "
        "'go find' them, just list them. (b) At each checkout step, actually CALL the matching "
        "tool to save the answer before moving on. (c) When confirm_order returns, congratulate "
        "the shopper and show the returned order id. Never mention tools.\n"
        "\n"
        "Style: keep it lively with a few tasteful, relevant emojis (e.g. 🛍️ 🛒 ✨ ✅ 💳 📦 🎉) — "
        "sprinkle, don't spam; one or two per message is plenty, and keep the product emojis "
        "already in the picks list."
    ),
    tools=[set_preferences, recommend_products, browse_for, select_product,
           set_shipping_address, set_payment_method, confirm_order],
    before_model_callback=_inject_context,
    after_model_callback=_after_model,
)
