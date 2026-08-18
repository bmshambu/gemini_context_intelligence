"""Offline tests for the shopping companion — no LLM, no network, no GCP.

Mock mode (USE_MEMORY_BANK unset). Covers: preferences + currency (permanent),
the 5-step order + 3-day TTL (temporary), preference-based recommendations,
the branching opener, the tools, and the demo clear-memory reset.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import (  # noqa: E402
    agent, alerts, catalogue, order as order_mod, preferences, store,
)


def setup_function(_fn=None):
    store._MOCK.clear()


# ── preferences (permanent) + currency ───────────────────────────────────────
def test_preferences_persist_and_map_currency():
    u = "in@x.com"
    preferences.set_preferences(u, country="India", language="Hindi",
                                interests=["fitness", "cooking"])
    p = preferences.get_preferences(u)
    assert p["currency_code"] == "INR" and p["currency_symbol"] == "₹"
    assert preferences.is_complete(p)                    # country is enough now
    assert "gender" not in p and "age" not in p          # not collected for the shopper

    u2 = "us@x.com"
    preferences.set_preferences(u2, country="USA")
    assert preferences.get_preferences(u2)["currency_code"] == "USD"


def test_partial_update_merges_and_currency_override():
    u = "upd@x.com"
    preferences.set_preferences(u, country="India", language="Hindi", interests=["tech"])
    # later: "show prices in USD" — currency only, everything else kept
    preferences.set_preferences(u, currency="USD")
    p = preferences.get_preferences(u)
    assert p["currency_code"] == "USD" and p["currency_symbol"] == "$"
    assert p["country"] == "India"        # country NOT wiped
    assert p["language"] == "Hindi" and p["interests"] == ["tech"]

    # changing country (no currency arg) recomputes currency from it
    preferences.set_preferences(u, country="United Kingdom")
    p = preferences.get_preferences(u)
    assert p["currency_code"] == "GBP" and p["country"] == "United Kingdom"
    assert p["interests"] == ["tech"]     # still kept


def test_recommendations_rank_by_interest_and_currency():
    prefs = preferences.set_preferences("r@x.com", country="India", interests=["fitness"])
    picks = catalogue.recommend(prefs, limit=6)
    assert picks, "should recommend something"
    # ranked by the shopper's interests (no demographic filter on the shopper)
    assert picks[0]["matched_interests"]
    # price formatted in INR (₹, x83)
    assert "₹" in catalogue.recommend_markdown(prefs)


# ── order (temporary, 5 steps, 3-day TTL) ────────────────────────────────────
def test_order_steps_advance_and_resume_detects_in_progress():
    u = "o@x.com"
    order_mod.set_product(u, "p02", "Running Shoes", "$89")
    assert order_mod.get_order(u)["step"] == 2
    assert order_mod.is_in_progress(order_mod.get_order(u))  # left at step 2 = resumable
    order_mod.set_address(u, "1 Main St")
    order_mod.set_payment(u, "card")
    assert order_mod.get_order(u)["step"] == 4
    rec = order_mod.confirm(u)
    assert rec["step"] == 5 and rec["status"] == "confirmed" and rec["order_id"]


def test_confirmed_order_is_cleared_so_it_never_resumes():
    # Bug 1: a completed order must not show as "step 2/5" next time.
    u = "done@x.com"
    order_mod.set_product(u, "p02", "Running Shoes", "$89")
    order_mod.set_address(u, "1 Main St")
    order_mod.set_payment(u, "card")
    order_mod.confirm(u)
    assert order_mod.get_order(u) is None                     # temp memory wiped
    assert not order_mod.is_in_progress(order_mod.get_order(u))  # nothing to resume


def test_order_expires_after_3_days():
    u = "exp@x.com"
    order_mod.set_product(u, "p02", "Running Shoes", "$89")
    store._MOCK[(u, store.TIER_TASK)]["order"]["_created"] = time.time() - (store.TASK_TTL_SECONDS + 10)
    assert order_mod.get_order(u) is None


# ── injected context (deterministic data the LLM conveys) ────────────────────
def test_shopping_context_carries_right_data_per_state():
    # new user → onboarding guidance
    assert "ONBOARDING" in agent._shopping_context("new@x.com", first_turn=True)

    # ready to shop → step-1 picks with exact currency-converted prices
    preferences.set_preferences("s@x.com", country="India", interests=["tech"])
    ctx = agent._shopping_context("s@x.com", first_turn=True)
    assert "STEP 1" in ctx and "₹" in ctx and "Smartwatch (Fitness+)" in ctx

    # in-progress order → resume data (exact product + step)
    order_mod.set_product("s@x.com", "p01", "Headphones", "₹16,517")
    ctx = agent._shopping_context("s@x.com", first_turn=True)
    assert "RESUME" in ctx and "Headphones" in ctx and "₹16,517" in ctx

    # mid-flow (not first turn) at step 2 → ask for address, no resume banner
    ctx = agent._shopping_context("s@x.com", first_turn=False)
    assert "STEP 2" in ctx and "RESUME" not in ctx

    # language is threaded into the delivery instruction
    preferences.set_preferences("h@x.com", country="India", language="Hindi")
    assert "in Hindi" in agent._shopping_context("h@x.com", first_turn=True)


def test_name_derived_from_email_and_injected():
    assert agent._name_from_uid("rahul.sharma@acme.com") == "Rahul"
    assert agent._name_from_uid("bmshambu134@gmail.com") == ""  # digits → no odd greeting
    preferences.set_preferences("rahul.sharma@acme.com", country="India")
    ctx = agent._shopping_context("rahul.sharma@acme.com", first_turn=True)
    assert "preferred name is Rahul" in ctx


def test_long_email_name_triggers_nickname_prompt_at_onboarding():
    long_uid = "shambulingaiahbm@kpmg.com"          # email name > 5 chars, no profile yet
    ctx = agent._shopping_context(long_uid, first_turn=True)
    assert "ONBOARDING" in ctx and "nickname" in ctx.lower()

    short_uid = "sam@x.com"                          # 'Sam' <= 5 chars → no nickname prompt
    ctx = agent._shopping_context(short_uid, first_turn=True)
    assert "nickname" not in ctx.lower()

    # email name not derivable (digits) → ask what to call them
    digits_uid = "bmshambu134@gmail.com"
    ctx = agent._shopping_context(digits_uid, first_turn=True)
    assert "like to be called" in ctx.lower()


def test_saved_nickname_overrides_email_name_and_stops_prompt():
    uid = "shambulingaiahbm@kpmg.com"
    preferences.set_preferences(uid, country="India", name="Shammi")
    assert preferences.get_preferences(uid)["name"] == "Shammi"
    ctx = agent._shopping_context(uid, first_turn=True)
    assert "preferred name is Shammi" in ctx        # nickname used
    assert "nickname" not in ctx.lower()             # no longer asked (profile complete + saved)


# ── tools + demo reset ───────────────────────────────────────────────────────
class _State:
    """Mimics ADK's session State: get / [] / in — but NO .pop()/.clear() (which
    the deployed State lacks), so tests catch use of dict-only methods."""
    def __init__(self):
        self._d = {}

    def get(self, k, default=None):
        return self._d.get(k, default)

    def __getitem__(self, k):
        return self._d[k]

    def __setitem__(self, k, v):
        self._d[k] = v

    def __contains__(self, k):
        return k in self._d


class _TC:
    def __init__(self, uid="tool@x.com"):
        self.state = _State()
        self.user_id = uid


def test_tools_run_full_flow():
    tc = _TC()
    agent.set_preferences(country="India", language="Hindi", interests=["beauty"], tool_context=tc)
    assert "₹" in agent.recommend_products(tool_context=tc)["picks_markdown"]
    assert agent.select_product("Skincare Gift Set", tool_context=tc)["status"] == "selected"
    assert agent.set_shipping_address("42 MG Road, Bengaluru", tool_context=tc)["status"] == "saved"
    assert agent.set_payment_method("cash", tool_context=tc)["payment_method"] == "cash on delivery"
    out = agent.confirm_order(tool_context=tc)
    assert out["status"] == "confirmed" and out["order_id"]


def test_browse_for_someone_else_filters_by_their_demographics():
    tc = _TC("dad@x.com")
    agent.set_preferences(country="USA", interests=["fitness"], tool_context=tc)
    # shopping for a kid → filtered by the kid's age, not dad's
    md = agent.browse_for(recipient="my kid", age="8", tool_context=tc)["picks_markdown"]
    assert "Picks for **my kid**" in md and "Building Blocks" in md   # a kids' item surfaces
    assert tc.state["shopping_for"]["age_group"] == "kid"
    # step-1 context uses the recipient filter
    ctx = agent._shopping_context("dad@x.com", first_turn=False, shopping_for=tc.state["shopping_for"])
    assert "buying for my kid" in ctx and "Building Blocks" in ctx
    # shopping for wife → women/any products (female↔women normalization surfaces
    # the women's Skincare Gift Set)
    wife_md = agent.browse_for(recipient="my wife", gender="female", age="35", tool_context=tc)["picks_markdown"]
    assert tc.state["shopping_for"]["gender"] == "female"
    assert "Skincare Gift Set" in wife_md
    # recipient's OWN interests rank their picks (⭐), not the shopper's
    son_md = agent.browse_for(recipient="my son", gender="male", age="15",
                              interests=["gaming"], tool_context=tc)["picks_markdown"]
    first_line = son_md.splitlines()[1]              # line after the "Picks for" header
    assert "⭐" in first_line and "gaming" in first_line.lower()

    # switch back to self clears the override (set to None, not removed)
    agent.browse_for(recipient="myself", tool_context=tc)
    assert not tc.state.get("shopping_for")


def test_address_and_payment_remembered_and_suggested_next_order():
    tc = _TC("ret@x.com")
    agent.set_preferences(country="India", tool_context=tc)
    agent.select_product("Smartwatch (Fitness+)", tool_context=tc)          # step 2
    agent.set_shipping_address("42 MG Road, Bengaluru", tool_context=tc)    # saves default
    agent.set_payment_method("card", tool_context=tc)                       # saves default
    agent.confirm_order(tool_context=tc)                                    # clears order

    p = preferences.get_preferences("ret@x.com")
    assert p["default_address"] == "42 MG Road, Bengaluru" and p["default_payment"] == "card"

    # next order: steps 2 & 3 proactively suggest the usual address + payment
    agent.select_product("Yoga Mat (Eco Cork)", tool_context=tc)            # new order, step 2
    ctx2 = agent._shopping_context("ret@x.com", first_turn=False)
    assert "usually ship to" in ctx2 and "42 MG Road" in ctx2
    agent.set_shipping_address("42 MG Road, Bengaluru", tool_context=tc)    # step 3
    ctx3 = agent._shopping_context("ret@x.com", first_turn=False)
    assert "usually pay by card" in ctx3


def test_price_drop_alert_surfaces_on_return_then_clears():
    uid = "back@x.com"
    preferences.set_preferences(uid, country="India", interests=["tech"])
    # background watcher wrote a drop while the shopper was away
    alerts.add_alert(uid, "p08", "Smartwatch (Fitness+)", "₹12,367", "₹9,894", drop_pct=20)
    pending = alerts.get_alerts(uid)
    assert pending and pending[0]["new_price"] == "₹9,894"
    # opener leads with the alert (exact prices preserved)
    ctx = agent._shopping_context(uid, first_turn=True, alert_list=pending)
    assert "PRICE ALERT" in ctx and "Smartwatch (Fitness+)" in ctx and "₹9,894" in ctx
    # once surfaced, clearing removes it so it isn't shown again
    alerts.clear_alerts(uid)
    assert alerts.get_alerts(uid) == []


def test_after_model_clears_alert_without_dict_state_methods():
    # Regression: deployed ADK State has no .pop()/.clear(); _after_model must not use them.
    from google.genai import types as gt
    uid = "clr2@x.com"
    preferences.set_preferences(uid, country="India")
    alerts.add_alert(uid, "p08", "Smartwatch (Fitness+)", "₹12,367", "₹9,894")

    class _CC:
        def __init__(self):
            self.state = _State()
            self.state["_alerts_pending"] = True
            self.user_id = uid
            self.user_content = gt.Content(role="user", parts=[gt.Part(text="hi")])

    class _Resp:
        partial = False
        content = gt.Content(role="model", parts=[gt.Part(text="Welcome back!")])

    cc, resp = _CC(), _Resp()
    agent._after_model(cc, resp)                       # must NOT raise AttributeError
    assert alerts.get_alerts(uid) == []                # alert cleared once delivered
    assert cc.state.get("_alerts_pending") is False


def test_order_write_is_stamped_and_latest_wins():
    u = "stamp@x.com"
    order_mod.set_product(u, "p04", "Building Blocks", "$29")
    order_mod.set_product(u, "p03", "Yoga Mat", "$39")   # newer selection replaces it
    o = order_mod.get_order(u)
    assert o["product_name"] == "Yoga Mat"               # latest wins, not stale
    assert o.get("_saved_at")                            # every write is timestamped
    assert order_mod._ts({"_saved_at": 2.0}) > order_mod._ts({"_saved_at": 1.0})


def test_clear_cart_empties_order_keeps_profile():
    tc = _TC("cart@x.com")
    preferences.set_preferences("cart@x.com", country="India", interests=["fitness"])
    order_mod.set_product("cart@x.com", "p03", "Yoga Mat", "₹3,237")
    assert order_mod.get_order("cart@x.com") is not None
    agent.clear_cart(tool_context=tc)
    assert order_mod.get_order("cart@x.com") is None                 # cart gone
    assert preferences.get_preferences("cart@x.com") is not None     # profile kept


def test_clear_cart_phrases_detected_but_distinct_from_memory_reset():
    assert agent._wants_clear_cart("please clear my cart")
    assert agent._wants_clear_cart("let's start over")
    assert agent._wants_clear_cart("fresh start")
    assert not agent._wants_clear_cart("show me the smartwatch")
    # full memory reset is a different intent, not a cart-clear
    assert agent._wants_clear("clear memory") and not agent._wants_clear_cart("clear memory")


def test_clear_memory_wipes_all_tiers():
    u = "clr@x.com"
    preferences.set_preferences(u, country="USA")
    order_mod.set_product(u, "p01", "Headphones", "$199")
    alerts.add_alert(u, "p01", "Headphones", "$199", "$159")
    store.clear(u)
    assert preferences.get_preferences(u) is None    # permanent gone
    assert order_mod.get_order(u) is None             # temporary gone
    assert alerts.get_alerts(u) == []                 # alerts gone


def test_wants_clear_matches_demo_phrases():
    assert agent._wants_clear("please clear memory")
    assert agent._wants_clear("Reset demo")
    assert not agent._wants_clear("what would you like to buy")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            setup_function()
            fn()
            print(f"  ok  {name}")
    print("All shopping-companion tests passed.")
