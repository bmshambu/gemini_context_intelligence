"""Chat-created price watches — the STATE layer (Memory Bank, watch tier).

The agent's 6 watch tools call these functions; the Cloud Tasks handler reads/updates
the SAME records (shared contract via memory_client). One watch per (user, product),
keyed 'watch:<product_id>'. See DESIGN_watches.md.

Lifecycle status: active | paused | stopped | triggered | expired.
Condition: {type: pct_drop|target_price|any_drop|back_in_stock, pct?, target?}.
Mode is one_shot (locked decision): after the first alert the watch stops.
"""
from __future__ import annotations

import os
import time

from . import store

DEFAULT_INTERVAL = int(os.getenv("WATCH_INTERVAL_SECONDS", "3600"))   # prod 1h; demo 60
DEFAULT_TTL = int(os.getenv("WATCH_TTL_SECONDS", str(3 * 24 * 3600)))  # 3 days
MAX_WATCHES_PER_USER = int(os.getenv("MAX_WATCHES_PER_USER", "10"))

_STATUSES = ("active", "paused", "stopped", "triggered", "expired")


def _key(product_id: str) -> str:
    return f"watch:{product_id}"


def get_watch(user_id, product_id) -> dict | None:
    k = _key(product_id)
    cands = [r for r in store.recall(user_id, store.TIER_WATCH) if r.get("key") == k]
    if not cands:
        return None
    cands.sort(key=lambda r: float(r.get("_saved_at") or 0), reverse=True)
    return cands[0]


def list_watches(user_id, active_only=False) -> list[dict]:
    now = time.time()
    out = []
    for r in store.recall(user_id, store.TIER_WATCH):
        if not str(r.get("key", "")).startswith("watch:"):
            continue
        if r.get("expires_at") and now > r["expires_at"] and r.get("status") == "active":
            r = {**r, "status": "expired"}
        if active_only and r.get("status") != "active":
            continue
        out.append(r)
    out.sort(key=lambda r: float(r.get("_saved_at") or 0), reverse=True)
    return out


def count_active(user_id) -> int:
    return len(list_watches(user_id, active_only=True))


def create_watch(user_id, product_id, product_name, price_display, condition,
                 interval_seconds=None, notify="surface_on_return") -> dict:
    now = time.time()
    rec = {
        "product_id": product_id,
        "product_name": product_name,
        "price_display": price_display,     # e.g. "₹3,237" — handler parses/scales it
        "condition": condition,             # {type, pct?/target?}
        "status": "active",
        "mode": "one_shot",
        "interval_seconds": int(interval_seconds or DEFAULT_INTERVAL),
        "expires_at": now + DEFAULT_TTL,
        "notify": notify,
        "created_at": now,
        "last_checked_at": None,
        "pending_task_name": None,
    }
    store.remember(user_id, store.TIER_WATCH, _key(product_id), rec, ttl_seconds=DEFAULT_TTL)
    return rec


def update_watch(user_id, product_id, **changes) -> dict | None:
    rec = get_watch(user_id, product_id)
    if not rec:
        return None
    rec = {k: v for k, v in rec.items() if k not in ("_saved_at", "_update_time", "_created")}
    for k, v in changes.items():
        if v is not None:
            rec[k] = v
    store.remember(user_id, store.TIER_WATCH, _key(product_id), rec,
                   ttl_seconds=store.TASK_TTL_SECONDS)
    return rec


def set_status(user_id, product_id, status) -> dict | None:
    if status not in _STATUSES:
        return None
    return update_watch(user_id, product_id, status=status)


def delete_watch(user_id, product_id) -> None:
    store.forget(user_id, store.TIER_WATCH, _key(product_id))
