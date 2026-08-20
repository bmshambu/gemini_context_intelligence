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

from . import alerts, catalogue, order as order_mod, preferences, store, tasks_client, trail, watches

# Demo-reset phrases (deterministic — reliable for a live demo, not LLM-judged).
_CLEAR_PHRASES = (
    "clear memory", "reset memory", "clear my memory", "reset demo", "clear demo",
    "forget everything", "clear everything", "start fresh demo", "wipe memory",
)

# Clear-cart phrases (deterministic) — wipe only the in-progress order (task tier),
# keep the profile. Covers "start over" / "fresh start" so a fresh start empties the cart.
_CLEAR_CART_PHRASES = (
    "clear my cart", "clear cart", "clear the cart", "empty cart", "empty my cart",
    "reset cart", "reset my cart", "new cart", "new order", "start a new order",
    "start over", "start fresh", "fresh start", "remove everything from my cart",
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
            f"interests={', '.join(p.get('interests') or []) or 'none'}")


def _order_summary_data(order: dict) -> str:
    return (f"- 📦 {order.get('product_name')} — {order.get('price_display')}\n"
            f"- 🏠 Ship to: {order.get('address')}\n"
            f"- 💳 Payment: {order.get('payment_method')}")


def _shopping_context(uid, first_turn: bool, shopping_for: dict | None = None,
                      alert_list: list[dict] | None = None) -> str:
    """Build the per-turn [Context] note: the exact data + what to do this turn.

    The LLM turns this into a fresh, natural, localized message. It must keep the
    data verbatim — that's what keeps prices/products/facts correct.

    `shopping_for` (transient, from session state) means the shopper is buying for
    someone else — recommendations are filtered by that person's gender/age instead.
    `alert_list` are pending price-drop alerts to lead the reply with.
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
                      "country, preferred language, and any interests; then call set_preferences. "
                      "Do NOT ask for their gender or age.")
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

    # Surrounding awareness: lead with any price-drop alerts a background watcher
    # left while the shopper was away.
    if alert_list:
        drops = "\n".join(
            f"- {a['product_name']}: was {a['old_price']}, now **{a['new_price']}**"
            for a in alert_list)
        L.insert(0, "PRICE ALERT — while the shopper was away, prices dropped on items they were "
                    "watching. LEAD your reply with this good news (celebrate it 🎉), then continue "
                    "the flow. Keep the prices EXACTLY as given:\n" + drops)

    L.append(f"Convey all of the above in your OWN natural words, in {lang} — vary your phrasing "
             "so it never feels like a fixed template. But keep every product name, price, currency "
             "symbol and order detail EXACTLY as given; never invent products, prices, or facts. "
             "Never mention tools.")
    return "[Context]\n" + "\n".join(L)


def _inject_context(callback_context: CallbackContext, llm_request: LlmRequest):
    first_turn = not callback_context.state.get("greeted")
    callback_context.state["greeted"] = True
    shopping_for = callback_context.state.get("shopping_for")
    uid = _uid(callback_context)
    # Pending price-drop alerts are surfaced until delivered, then cleared in
    # _after_model (robust to tool-call sub-turns).
    alert_list = alerts.get_alerts(uid)
    if alert_list:
        callback_context.state["_alerts_pending"] = True
    # Remember the products shown this turn (in order) so a "number" pick can be
    # resolved deterministically in _after_model even if the LLM misses select_product.
    prefs = preferences.get_preferences(uid)
    order = order_mod.get_order(uid)
    active = order if order and order.get("status") == "shopping" else None
    if preferences.is_complete(prefs) and (not active or active.get("step", 1) <= 1):
        if shopping_for:
            recs = catalogue.recommend(prefs, gender=shopping_for.get("gender", ""),
                                       age_group=shopping_for.get("age_group", "any"),
                                       interests=shopping_for.get("interests", []))
        else:
            recs = catalogue.recommend(prefs)
        callback_context.state["last_recs"] = [p["id"] for p in recs]

    notes = [_shopping_context(uid, first_turn, shopping_for, alert_list)]
    # On return with a cart, hand the LLM the recent conversation trail so it can
    # RECONCILE a possibly-stale cart against what the shopper actually asked for —
    # the catch-all for picks that both the tool call and the deterministic catch missed.
    if first_turn and active:
        msgs = trail.get_messages(uid)
        if msgs:
            notes.append(
                "[Recent intent] The shopper's recent messages (oldest→newest): "
                + "  ·  ".join(msgs) + f".  Their cart currently holds "
                f"'{active.get('product_name')}'. If those messages clearly show they moved "
                "to a DIFFERENT product than what's in the cart, briefly confirm and call "
                "select_product to correct it before continuing. Otherwise ignore this.")
    llm_request.append_instructions(notes)
    return None


# ── tools ────────────────────────────────────────────────────────────────────
def set_preferences(country: str = "", language: str = "", interests: list[str] = None,
                    currency: str = "", name: str = "", tool_context: ToolContext = None) -> dict:
    """Save or UPDATE the shopper's PERMANENT profile. Use at onboarding AND anytime
    they change a single preference later — pass ONLY the field(s) that changed; the
    rest is kept. Never collect the shopper's gender or age.

    Args:
        country: sets the display currency (unless `currency` overrides it).
        language: how you communicate with them.
        interests: the FULL desired interests list when changing interests.
        currency: an explicit currency code (e.g. "USD", "INR") to show prices in,
            independent of country — use when they say "show prices in USD".
        name: the shopper's preferred name / nickname to address them by — save it
            when they give one (e.g. after you offer a shorter nickname at onboarding).
    """
    rec = preferences.set_preferences(
        _uid(tool_context),
        country=country or None, language=language or None,
        interests=interests, currency=currency or None, name=name or None,
    )
    return {"status": "saved", "currency": rec["currency_code"]}


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
            tool_context.state["shopping_for"] = None  # ADK State has no .pop()
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


# ── watch tools (chat-driven long-running price watches) ─────────────────────
_CONDITION_LABEL = {
    "pct_drop": "a price drop of {pct}%",
    "target_price": "the price reaching {target}",
    "any_drop": "any price drop",
    "back_in_stock": "the item coming back in stock",
}


def _build_condition(condition_type: str, pct: str, target: str) -> dict | None:
    c = (condition_type or "").strip().lower()
    if c in ("pct_drop", "percent", "percentage") and str(pct).strip():
        try:
            return {"type": "pct_drop", "pct": float(pct)}
        except ValueError:
            return None
    if c in ("target_price", "target", "under") and str(target).strip():
        try:
            return {"type": "target_price", "target": float(target)}
        except ValueError:
            return None
    if c in ("any_drop", "any", "drop"):
        return {"type": "any_drop"}
    if c in ("back_in_stock", "stock", "restock", "in_stock"):
        return {"type": "back_in_stock"}
    return None


def create_watch(product: str, condition_type: str = "pct_drop", pct: str = "",
                 target: str = "", tool_context: ToolContext = None) -> dict:
    """Set up a background WATCH on a product's price. Confirm the parameters with the
    shopper first (offer the menu: % drop / target price / any drop / back in stock).
    The watch runs in the background and alerts them when they next return.

    Args:
        product: product name to watch, e.g. "Yoga Mat".
        condition_type: pct_drop | target_price | any_drop | back_in_stock.
        pct: for pct_drop, the percentage as a number, e.g. "15".
        target: for target_price, the price threshold (number in their currency), e.g. "2500".
    """
    uid = _uid(tool_context)
    prefs = preferences.get_preferences(uid)
    p = catalogue.get(product)
    if not p:
        return {"status": "not_found", "product": product}
    existing = watches.get_watch(uid, p["id"])
    if not existing and watches.count_active(uid) >= watches.MAX_WATCHES_PER_USER:
        return {"status": "limit_reached", "max": watches.MAX_WATCHES_PER_USER}
    condition = _build_condition(condition_type, pct, target)
    if not condition:
        return {"status": "need_condition",
                "options": ["pct_drop (needs pct)", "target_price (needs target)",
                            "any_drop", "back_in_stock"]}
    price_display = catalogue.format_price(p["price_usd"], prefs)
    rec = watches.create_watch(uid, p["id"], p["name"], price_display, condition)
    task = tasks_client.enqueue_check(uid, p["id"], rec["interval_seconds"])
    if task:
        watches.update_watch(uid, p["id"], pending_task_name=task)
    return {"status": "watching", "product": p["name"], "price": price_display,
            "condition": condition}


def list_watches(tool_context: ToolContext = None) -> dict:
    """List the shopper's current watches (what they're watching and its status)."""
    ws = watches.list_watches(_uid(tool_context))
    return {"count": len(ws), "watches": [
        {"product": w["product_name"], "price": w.get("price_display"),
         "condition": w.get("condition"), "status": w.get("status")} for w in ws]}


def pause_watch(product: str, tool_context: ToolContext = None) -> dict:
    """Pause a watch — it stops checking until resumed."""
    uid = _uid(tool_context)
    p = catalogue.get(product)
    w = watches.get_watch(uid, p["id"]) if p else None
    if not w:
        return {"status": "not_found", "product": product}
    tasks_client.delete_task(w.get("pending_task_name"))
    watches.update_watch(uid, p["id"], status="paused", pending_task_name=None)
    return {"status": "paused", "product": p["name"]}


def resume_watch(product: str, tool_context: ToolContext = None) -> dict:
    """Resume a paused watch — restarts its background checks."""
    uid = _uid(tool_context)
    p = catalogue.get(product)
    w = watches.get_watch(uid, p["id"]) if p else None
    if not w:
        return {"status": "not_found", "product": product}
    watches.update_watch(uid, p["id"], status="active")
    task = tasks_client.enqueue_check(uid, p["id"], w.get("interval_seconds") or watches.DEFAULT_INTERVAL)
    watches.update_watch(uid, p["id"], pending_task_name=task)
    return {"status": "resumed", "product": p["name"]}


def stop_watch(product: str, tool_context: ToolContext = None) -> dict:
    """Stop (terminate) a watch entirely and remove it."""
    uid = _uid(tool_context)
    p = catalogue.get(product)
    w = watches.get_watch(uid, p["id"]) if p else None
    if not w:
        return {"status": "not_found", "product": product}
    tasks_client.delete_task(w.get("pending_task_name"))
    watches.delete_watch(uid, p["id"])
    return {"status": "stopped", "product": p["name"]}


def update_watch(product: str, condition_type: str = "", pct: str = "", target: str = "",
                 tool_context: ToolContext = None) -> dict:
    """Change a watch's alert condition (e.g. from 15% to 10%, or to a target price)."""
    uid = _uid(tool_context)
    p = catalogue.get(product)
    w = watches.get_watch(uid, p["id"]) if p else None
    if not w:
        return {"status": "not_found", "product": product}
    cond = _build_condition(condition_type, pct, target) if condition_type else None
    if cond:
        watches.update_watch(uid, p["id"], condition=cond)
    return {"status": "updated", "product": p["name"], "condition": cond or w.get("condition")}


def clear_cart(tool_context: ToolContext = None) -> dict:
    """Empty the shopper's in-progress cart / order (temporary memory only — keeps
    their profile). Call whenever they want to clear the cart, start over, or begin
    a fresh order."""
    uid = _uid(tool_context)
    order_mod.clear_order(uid)
    trail.clear(uid)  # fresh start — drop the intent trail too
    if tool_context:
        tool_context.state["shopping_for"] = None  # drop any gift-recipient lens
        tool_context.state["last_recs"] = []
    return {"status": "cart_cleared"}


def confirm_order(tool_context: ToolContext = None) -> dict:
    """Step 4 → 5: finalize the order (demo only — no real payment). Returns the
    order id to show the shopper."""
    uid = _uid(tool_context)
    rec = order_mod.confirm(uid)
    trail.clear(uid)  # order placed — the shopping trail is done
    if tool_context:  # next order defaults back to shopping for themselves
        tool_context.state["shopping_for"] = None  # ADK State has no .pop()
        tool_context.state["last_recs"] = []
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


def _wants_clear_cart(text: str) -> bool:
    t = (text or "").lower()
    return any(phrase in t for phrase in _CLEAR_CART_PHRASES)


def _maybe_identity(content, uid) -> None:
    if os.getenv("SHOW_IDENTITY", "").lower() not in ("1", "true", "yes"):
        return
    line = f"\n\n_🔎 identity → user_id: {uid or 'unknown'}_"
    for p in content.parts:
        if p.text is not None:
            p.text = (p.text or "").rstrip() + line
            return


def _match_product(text: str, rec_ids: list[str]):
    """Resolve a shopper's typed pick to a catalogue product — a shown item whose base
    name appears in the message first, then any catalogue product it names. Returns a
    product dict or None. Ignores short/number-only text (handled as a number pick)."""
    t = (text or "").lower().strip()
    if len(t) < 4 or not any(c.isalpha() for c in t):
        return None
    for pid in rec_ids or []:
        p = catalogue.get(pid)
        if not p:
            continue
        base = p["name"].split("(")[0].strip().lower()  # "Yoga Mat (Eco Cork)" -> "yoga mat"
        if base and base in t:
            return p
    return catalogue.get(text)


def _catch_product_selection(callback_context, uid) -> None:
    """Deterministic safety net: if the shopper clearly names/numbers a product while
    choosing, write the cart in code — so a missed select_product tool call can't leave
    a stale item. The cart stays the reliable source of truth."""
    prefs = preferences.get_preferences(uid)
    if not preferences.is_complete(prefs):
        return
    order = order_mod.get_order(uid)
    if order and order.get("status") == "confirmed":
        return
    step = (order or {}).get("step", 1)
    text = _latest_user_text(callback_context).strip()
    if not text:
        return

    product = None
    if step <= 1:  # number pick maps to the products shown this turn
        token = text.lower().replace("number", "").strip().lstrip("#").rstrip(".").strip()
        if token.isdigit():
            recs = callback_context.state.get("last_recs") or []
            i = int(token) - 1
            if 0 <= i < len(recs):
                product = catalogue.get(recs[i])
    if product is None and step <= 2:  # concise name pick
        product = _match_product(text, callback_context.state.get("last_recs") or [])
    if not product:
        return
    if order and order.get("product_id") == product["id"]:
        return  # already the current item — nothing to fix

    price = catalogue.format_price(product["price_usd"], prefs)
    order_mod.set_product(uid, product["id"], product["name"], price)
    print(f"[catch] cart set to {product['name']} for {uid!r}")


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
        # ADK State has no .clear()/.pop() — reset the flags we set, by assignment.
        callback_context.state["greeted"] = False
        callback_context.state["shopping_for"] = None
        callback_context.state["_alerts_pending"] = False
        _set_text(content, "🧹 **Memory cleared** — your profile and any in-progress order are "
                           "gone. Say hello to start fresh.")
        _maybe_identity(content, uid)
        return llm_response

    # Clear-cart — deterministic so "clear my cart" / "start over" / "fresh start"
    # reliably empties the in-progress order (task memory) while keeping the profile.
    if _wants_clear_cart(_latest_user_text(callback_context)):
        order_mod.clear_order(uid)
        trail.clear(uid)
        callback_context.state["shopping_for"] = None
        callback_context.state["last_recs"] = []
        _set_text(content, "🛒 **Cart cleared** — fresh start! What would you like to shop for?")
        _maybe_identity(content, uid)
        return llm_response

    # Deterministic product-catch — keep the cart in sync with the shopper's pick even
    # if the LLM missed calling select_product (the reported stale-cart bug).
    _catch_product_selection(callback_context, uid)

    # Record the shopper's message in the conversation trail (deterministic safety net,
    # reconciled on return) — after the catch, so the catch acts on the current turn.
    trail.append_message(uid, _latest_user_text(callback_context))

    # A pending price-drop alert was just delivered as text → clear it so a returning
    # shopper sees it once, not on every turn.
    if callback_context.state.get("_alerts_pending", False):
        callback_context.state["_alerts_pending"] = False
        alerts.clear_alerts(uid)

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
        "Privacy: never ask the shopper for their OWN gender or age — you don't need them. "
        "(You may ask a GIFT recipient's age/gender only if the shopper is buying for someone "
        "else and offers it.)\n"
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
        "set_shipping_address; set_payment_method; confirm_order; clear_cart (empty the "
        "in-progress order when they want to clear their cart or start a new one).\n"
        "\n"
        "WATCHES (background price monitoring). When the shopper asks you to watch/track a "
        "product's price, first CONFIRM and offer the menu — you can alert them on: a % price "
        "drop (e.g. 15%), a target price (e.g. under ₹2,500), any drop, or when it's back in "
        "stock — and note you'll tell them when they next return. If they ask for something you "
        "can't watch, say what you CAN watch and steer them there. Once the parameters are clear, "
        "call create_watch. Also handle: list_watches ('what am I watching?'), pause_watch, "
        "resume_watch, stop_watch ('stop watching X'), and update_watch (change the threshold). "
        "Never mention tools.\n"
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
           set_shipping_address, set_payment_method, confirm_order, clear_cart,
           create_watch, list_watches, pause_watch, resume_watch, stop_watch, update_watch],
    before_model_callback=_inject_context,
    after_model_callback=_after_model,
)
