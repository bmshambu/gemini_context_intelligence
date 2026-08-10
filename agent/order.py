"""Temporary in-progress order — the 5-step checkout state (auto-expires in 3 days).

Steps:
    1. choose product      2. shipping address     3. payment method
    4. confirm order       5. done

One keep-latest "order" record in the task tier, written with a 3-day TTL so an
abandoned cart clears itself. When the shopper leaves at step 2 or 3 and returns,
the resume hint is built from this record.
"""
from __future__ import annotations

import time

from . import store

_ORDER_KEY = "order"

STEP_NAME = {
    1: "choose a product",
    2: "enter shipping address",
    3: "choose payment method",
    4: "confirm the order",
    5: "done",
}


def get_order(user_id) -> dict | None:
    for r in store.recall(user_id, store.TIER_TASK):
        if r.get("key") == _ORDER_KEY:
            return r
    return None


def _save(user_id, rec) -> dict:
    store.remember(user_id, store.TIER_TASK, _ORDER_KEY, rec, ttl_seconds=store.TASK_TTL_SECONDS)
    return rec


def start_or_get(user_id) -> dict:
    return get_order(user_id) or {"step": 1, "status": "shopping"}


def set_product(user_id, product_id, product_name, price_display) -> dict:
    rec = start_or_get(user_id)
    rec.update({"product_id": product_id, "product_name": product_name,
                "price_display": price_display, "step": 2, "status": "shopping"})
    return _save(user_id, rec)


def set_address(user_id, address) -> dict:
    rec = start_or_get(user_id)
    rec.update({"address": address, "step": 3})
    return _save(user_id, rec)


def set_payment(user_id, method) -> dict:
    rec = start_or_get(user_id)
    rec.update({"payment_method": method, "step": 4})
    return _save(user_id, rec)


def confirm(user_id) -> dict:
    rec = start_or_get(user_id)
    rec.update({"step": 5, "status": "confirmed",
                "order_id": f"ORD-{int(time.time()) % 1000000:06d}"})
    return _save(user_id, rec)


def clear_order(user_id) -> None:
    store.forget(user_id, store.TIER_TASK, _ORDER_KEY)


def is_in_progress(order: dict | None) -> bool:
    """True when there's a cart mid-checkout (past product choice, not yet confirmed)."""
    return bool(order and order.get("status") == "shopping" and order.get("step", 1) in (2, 3, 4))
