"""Memory Bank client for the watch handler (self-contained — no agent import).

Shares a contract with agent/store.py + agent/watches.py + agent/alerts.py: the fact
prefix, scope keys, tier names, and the watch/alert record shapes MUST match. Keep in
sync. The `_saved_at` stamp on writes is what lets the agent read the latest record.
"""
import json
import os
import re
import time

FACT_PREFIX = "CTXMEM1 "
TIER_ALERT = "alert"
TIER_WATCH = "watch"
APP_NAME = os.getenv("MEMORY_APP_NAME", "shopping_companion")
ALERT_TTL_SECONDS = int(os.getenv("ALERT_TTL_SECONDS", str(24 * 3600)))
WATCH_TTL_SECONDS = int(os.getenv("WATCH_TTL_SECONDS", str(3 * 24 * 3600)))


def _client():
    import vertexai
    return vertexai.Client(project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                           location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))


def _engine_name() -> str:
    return "reasoningEngines/" + os.environ["AGENT_ENGINE_ID"].split("/")[-1]


def _relative(name: str) -> str:
    i = name.find("reasoningEngines/")
    return name[i:] if i >= 0 else name


def _scope(user_id, tier):
    return {"app_name": APP_NAME, "user_id": user_id, "tier": tier}


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


def _write(user_id, tier, key, record, ttl):
    client = _client()
    old = [_relative(m.name) for m, rec in _iter(client, user_id, tier)
           if rec.get("key") == key and getattr(m, "name", None)]
    record = {**record, "key": key, "_saved_at": time.time()}
    client.agent_engines.memories.create(
        name=_engine_name(), fact=FACT_PREFIX + json.dumps(record),
        scope=_scope(user_id, tier), config={"wait_for_completion": True, "ttl": f"{ttl}s"})
    for nm in old:
        try:
            client.agent_engines.memories.delete(name=nm)
        except Exception as e:  # noqa: BLE001
            print(f"[handler] cleanup delete failed for {nm}: {e}")


def get_watch(user_id, product_id) -> dict | None:
    key = f"watch:{product_id}"
    latest, latest_ts = None, -1.0
    for m, rec in _iter(_client(), user_id, TIER_WATCH):
        if rec.get("key") != key:
            continue
        ts = float(rec.get("_saved_at") or 0)
        if ts >= latest_ts:
            latest, latest_ts = rec, ts
    return latest


def update_watch(user_id, product_id, changes: dict) -> None:
    rec = get_watch(user_id, product_id) or {"product_id": product_id}
    rec = {k: v for k, v in rec.items() if k not in ("_saved_at", "_update_time")}
    rec.update(changes)
    _write(user_id, TIER_WATCH, f"watch:{product_id}", rec, WATCH_TTL_SECONDS)


def write_alert(user_id, product_id, product_name, old_price, new_price, drop_pct) -> None:
    _write(user_id, TIER_ALERT, f"alert:{product_id}",
           {"product_id": product_id, "product_name": product_name,
            "old_price": old_price, "new_price": new_price, "drop_pct": drop_pct,
            "ts": time.time()}, ALERT_TTL_SECONDS)


def scale_price(display: str, factor: float) -> str:
    m = re.search(r"[\d,]+(?:\.\d+)?", display or "")
    if not m:
        return display
    num = float(m.group(0).replace(",", ""))
    return f"{display[:m.start()]}{round(num * factor):,}{display[m.end():]}"


def price_number(display: str) -> float:
    m = re.search(r"[\d,]+(?:\.\d+)?", display or "")
    return float(m.group(0).replace(",", "")) if m else 0.0
