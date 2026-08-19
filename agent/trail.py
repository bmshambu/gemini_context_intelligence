"""Deterministic conversation trail — a rolling window of the shopper's recent
messages, stored in Memory Bank (task tier, key 'trail').

It's a verbatim safety record: if a product pick is missed by BOTH the LLM tool
call AND the deterministic catch, the shopper's actual words still survive here, so
on their next visit the agent can reconcile the (possibly stale) cart against what
they really asked for. Written each turn, non-blocking; kept to the last few
messages; expires with the task tier (3 days).
"""
from __future__ import annotations

from . import store

_TRAIL_KEY = "trail"
_MAX_MESSAGES = 8
_MAX_LEN = 200


def get_messages(user_id) -> list[str]:
    """The shopper's recent messages (oldest → newest), newest trail record wins."""
    cands = [r for r in store.recall(user_id, store.TIER_TASK) if r.get("key") == _TRAIL_KEY]
    if not cands:
        return []
    cands.sort(key=lambda r: float(r.get("_saved_at") or 0), reverse=True)
    return list(cands[0].get("messages") or [])


def append_message(user_id, text) -> None:
    """Append the shopper's latest message to the trail (deterministic, non-blocking)."""
    text = (text or "").strip()[:_MAX_LEN]
    if not text:
        return
    msgs = get_messages(user_id)
    if msgs and msgs[-1] == text:
        return  # skip consecutive duplicates (tool sub-turns, resends)
    msgs = (msgs + [text])[-_MAX_MESSAGES:]
    store.remember(user_id, store.TIER_TASK, _TRAIL_KEY, {"messages": msgs},
                   ttl_seconds=store.TASK_TTL_SECONDS, wait_for_completion=False)


def clear(user_id) -> None:
    store.forget(user_id, store.TIER_TASK, _TRAIL_KEY)
