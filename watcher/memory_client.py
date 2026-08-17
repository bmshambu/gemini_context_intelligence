"""Minimal Vertex AI Agent Engine Memory Bank client for the price watcher.

Self-contained — it does NOT import the agent package, so this folder builds and
deploys on its own (separate from the agent's CI/CD).

⚠️ SHARED CONTRACT with the agent's `agent/store.py` + `agent/alerts.py`: the fact
prefix, scope keys, tier names, and the order/alert record shapes below MUST match
the agent's, or the agent won't see the alerts this watcher writes. Keep in sync.
"""
import json
import os
import re
import time

# ── contract (must match agent/store.py) ─────────────────────────────────────
FACT_PREFIX = "CTXMEM1 "
TIER_TASK = "task"
TIER_ALERT = "alert"
APP_NAME = os.getenv("MEMORY_APP_NAME", "shopping_companion")
ALERT_TTL_SECONDS = int(os.getenv("ALERT_TTL_SECONDS", str(24 * 3600)))


def _client():
    import vertexai
    return vertexai.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                           location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))


def _engine_name() -> str:
    return "reasoningEngines/" + os.environ["AGENT_ENGINE_ID"].split("/")[-1]


def _scope(user_id: str, tier: str) -> dict:
    return {"app_name": APP_NAME, "user_id": user_id, "tier": tier}


def _relative(name: str) -> str:
    i = name.find("reasoningEngines/")
    return name[i:] if i >= 0 else name


def _iter(client, user_id, tier):
    for rm in client.agent_engines.memories.retrieve(
            name=_engine_name(), scope=_scope(user_id, tier), simple_retrieval_params={}):
        m = getattr(rm, "memory", None)
        fact = (getattr(m, "fact", "") or "") if m else ""
        if fact.startswith(FACT_PREFIX):
            try:
                yield m, json.loads(fact[len(FACT_PREFIX):])
            except ValueError:
                continue


def get_order(user_id: str) -> dict | None:
    """Read the shopper's in-progress order (task tier, key 'order'), newest first."""
    latest, latest_ts = None, -1.0
    for m, rec in _iter(_client(), user_id, TIER_TASK):
        if rec.get("key") != "order":
            continue
        ut = getattr(m, "update_time", None)
        ts = ut.timestamp() if ut is not None else 0.0
        if ts >= latest_ts:
            latest, latest_ts = rec, ts
    return latest


def write_alert(user_id, product_id, product_name, old_price, new_price, drop_pct) -> None:
    """Write a price-drop alert (alert tier, key 'alert:<product_id>'), keep-latest."""
    client = _client()
    key = f"alert:{product_id}"
    old_names = [_relative(m.name) for m, rec in _iter(client, user_id, TIER_ALERT)
                 if rec.get("key") == key and getattr(m, "name", None)]
    rec = {"key": key, "product_id": product_id, "product_name": product_name,
           "old_price": old_price, "new_price": new_price, "drop_pct": drop_pct,
           "ts": time.time()}
    client.agent_engines.memories.create(
        name=_engine_name(), fact=FACT_PREFIX + json.dumps(rec),
        scope=_scope(user_id, TIER_ALERT),
        config={"wait_for_completion": True, "ttl": f"{ALERT_TTL_SECONDS}s"})
    for nm in old_names:
        try:
            client.agent_engines.memories.delete(name=nm)
        except Exception as e:  # noqa: BLE001
            print(f"[watcher] cleanup delete failed for {nm}: {e}")


def scale_price(display: str, factor: float) -> str:
    """Scale the number inside a currency string, keeping its symbol/format.
    '₹12,367' * 0.8 → '₹9,894';  '$149' * 0.8 → '$119'."""
    m = re.search(r"[\d,]+(?:\.\d+)?", display or "")
    if not m:
        return display
    num = float(m.group(0).replace(",", ""))
    return f"{display[:m.start()]}{round(num * factor):,}{display[m.end():]}"
