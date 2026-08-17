"""Proactive alerts (e.g. price drops) awaiting the shopper — the "surface on
return" half of surrounding awareness.

A background watcher writes alerts here; the agent's opener reads them on the
shopper's next visit, announces them, then clears them. Stored in the alert tier,
keyed per product so multiple drops don't collide, with a short TTL so a
never-returning shopper's alerts self-clean.
"""
from __future__ import annotations

import os
import time

from . import store

_ALERT_TTL_SECONDS = int(os.getenv("ALERT_TTL_SECONDS", str(24 * 3600)))  # 1 day


def add_alert(user_id, product_id, product_name, old_price, new_price, drop_pct=None) -> dict:
    """Record a price-drop alert for the shopper (called by the background watcher)."""
    rec = {
        "product_id": product_id,
        "product_name": product_name,
        "old_price": old_price,
        "new_price": new_price,
        "drop_pct": drop_pct,
        "ts": time.time(),
    }
    store.remember(user_id, store.TIER_ALERT, f"alert:{product_id}", rec,
                   ttl_seconds=_ALERT_TTL_SECONDS)
    return rec


def get_alerts(user_id) -> list[dict]:
    """Pending alerts for the shopper, newest first."""
    return [r for r in store.recall(user_id, store.TIER_ALERT)
            if str(r.get("key", "")).startswith("alert:")]


def clear_alerts(user_id) -> None:
    """Remove pending alerts once they've been surfaced."""
    for r in get_alerts(user_id):
        store.forget(user_id, store.TIER_ALERT, r["key"])
