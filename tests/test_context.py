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
    agent, catalogue, order as order_mod, preferences, store,
)


def setup_function(_fn=None):
    store._MOCK.clear()


# ── preferences (permanent) + currency ───────────────────────────────────────
def test_preferences_persist_and_map_currency():
    u = "in@x.com"
    preferences.set_preferences(u, country="India", language="Hindi", gender="female",
                                age="30", interests=["fitness", "cooking"])
    p = preferences.get_preferences(u)
    assert p["currency_code"] == "INR" and p["currency_symbol"] == "₹"
    assert p["age_group"] == "adult" and preferences.is_complete(p)

    u2 = "us@x.com"
    preferences.set_preferences(u2, country="USA", gender="male", age="10")
    p2 = preferences.get_preferences(u2)
    assert p2["currency_code"] == "USD" and p2["age_group"] == "kid"


def test_partial_update_merges_and_currency_override():
    u = "upd@x.com"
    preferences.set_preferences(u, country="India", language="Hindi", gender="male",
                                age="30", interests=["tech"])
    # later: "show prices in USD" — currency only, everything else kept
    preferences.set_preferences(u, currency="USD")
    p = preferences.get_preferences(u)
    assert p["currency_code"] == "USD" and p["currency_symbol"] == "$"
    assert p["country"] == "India"        # country NOT wiped
    assert p["gender"] == "male" and p["age"] == "30" and p["interests"] == ["tech"]

    # changing country (no currency arg) recomputes currency from it
    preferences.set_preferences(u, country="United Kingdom")
    p = preferences.get_preferences(u)
    assert p["currency_code"] == "GBP" and p["country"] == "United Kingdom"
    assert p["interests"] == ["tech"]     # still kept


def test_recommendations_filter_by_profile_and_currency():
    prefs = preferences.set_preferences("r@x.com", country="India", gender="male",
                                        age="30", interests=["fitness"])
    picks = catalogue.recommend(prefs, limit=6)
    assert picks, "should recommend something"
    # no women-only products for a male shopper
    assert all(p["gender"] in ("any", "male") for p in picks)
    # interest match bubbles to the top and is flagged
    assert picks[0]["matched_interests"]
    # price formatted in INR (₹, x83)
    md = catalogue.recommend_markdown(prefs)
    assert "₹" in md


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
    preferences.set_preferences("s@x.com", country="India", gender="male", age="30",
                                interests=["tech"])
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
    preferences.set_preferences("h@x.com", country="India", language="Hindi",
                                gender="female", age="25")
    assert "in Hindi" in agent._shopping_context("h@x.com", first_turn=True)


def test_name_derived_from_email_and_injected():
    assert agent._name_from_uid("rahul.sharma@acme.com") == "Rahul"
    assert agent._name_from_uid("bmshambu134@gmail.com") == ""  # digits → no odd greeting
    preferences.set_preferences("rahul.sharma@acme.com", country="India", gender="male", age="30")
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
    preferences.set_preferences(uid, country="India", gender="male", age="30", name="Shammi")
    assert preferences.get_preferences(uid)["name"] == "Shammi"
    ctx = agent._shopping_context(uid, first_turn=True)
    assert "preferred name is Shammi" in ctx        # nickname used
    assert "nickname" not in ctx.lower()             # no longer asked (profile complete + saved)


# ── tools + demo reset ───────────────────────────────────────────────────────
class _TC:
    def __init__(self, uid="tool@x.com"):
        self.state = {}
        self.user_id = uid


def test_tools_run_full_flow():
    tc = _TC()
    agent.set_preferences(country="India", language="Hindi", gender="female", age="28",
                          interests=["beauty"], tool_context=tc)
    assert "₹" in agent.recommend_products(tool_context=tc)["picks_markdown"]
    assert agent.select_product("Skincare Gift Set", tool_context=tc)["status"] == "selected"
    assert agent.set_shipping_address("42 MG Road, Bengaluru", tool_context=tc)["status"] == "saved"
    assert agent.set_payment_method("cash", tool_context=tc)["payment_method"] == "cash on delivery"
    out = agent.confirm_order(tool_context=tc)
    assert out["status"] == "confirmed" and out["order_id"]


def test_address_and_payment_remembered_and_suggested_next_order():
    tc = _TC("ret@x.com")
    agent.set_preferences(country="India", gender="male", age="30", tool_context=tc)
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


def test_clear_memory_wipes_both_tiers():
    u = "clr@x.com"
    preferences.set_preferences(u, country="USA", gender="male", age="40")
    order_mod.set_product(u, "p01", "Headphones", "$199")
    store.clear(u)
    assert preferences.get_preferences(u) is None
    assert order_mod.get_order(u) is None


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
