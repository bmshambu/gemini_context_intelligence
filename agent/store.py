"""Two-tier memory on Vertex AI Agent Engine Memory Bank.

Tier PERSONA (permanent): who the shopper is — country, language, gender, age,
interests (one "profile" record). No expiry.

Tier TASK (temporary, <= 3 days): the in-progress order (which of the 5 checkout
steps they're on, chosen product, address, payment). Written with a TTL so Memory
Bank destroys an abandoned order automatically after 3 days.

Both tiers live in the SAME bank, separated by scope {app_name, user_id, tier}.
A dedup `key` (slug) inside the fact means updating a record replaces it
(keep-latest per key) instead of piling up.

Verified API (aiplatform 1.148.1 / adk 1.31.1 / genai 1.75.0 — matches
google/adk/memory/vertex_ai_memory_bank_service.py):
    vertexai.Client(project, location).agent_engines.memories
        .create(name="reasoningEngines/<ID>", fact, scope, config={ttl, wait_for_completion})
        .retrieve(name=..., scope, simple_retrieval_params={})   # sync iterator
        .delete(name="reasoningEngines/<ID>/memories/<mid>")

Guarded: without USE_MEMORY_BANK + AGENT_ENGINE_ID (or on any error) it falls back
to an in-process mock so the agent runs locally.
"""
from __future__ import annotations

import json
import os
import re
import time

APP_NAME = os.getenv("MEMORY_APP_NAME", "shopping_companion")
TIER_PERSONA = "persona"
TIER_TASK = "task"
TIER_ALERT = "alert"                       # proactive alerts (price drops) awaiting the shopper
TIERS = (TIER_PERSONA, TIER_TASK, TIER_ALERT)
TASK_TTL_SECONDS = int(os.getenv("TASK_TTL_SECONDS", str(3 * 24 * 3600)))  # 3 days

_FACT_PREFIX = "CTXMEM1 "


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60] or "item"


# ── config ───────────────────────────────────────────────────────────────────
def _use_memory_bank() -> bool:
    return (
        os.getenv("USE_MEMORY_BANK", "").lower() in ("1", "true", "yes")
        and bool(os.getenv("AGENT_ENGINE_ID"))
    )


def _project() -> str | None:
    return os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_CLOUD_PROJECT")


def _location() -> str:
    return os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")


def _engine_name() -> str:
    return "reasoningEngines/" + os.environ["AGENT_ENGINE_ID"].split("/")[-1]


def _relative_name(full: str) -> str:
    i = full.find("reasoningEngines/")
    return full[i:] if i >= 0 else full


# ── Memory Bank I/O (guarded) ────────────────────────────────────────────────
def _client():
    import vertexai
    return vertexai.Client(project=_project(), location=_location())


def _scope(user_id: str, tier: str) -> dict:
    return {"app_name": APP_NAME, "user_id": user_id, "tier": tier}


def _iter_memories(client, user_id: str, tier: str):
    for rm in client.agent_engines.memories.retrieve(
        name=_engine_name(),
        scope=_scope(user_id, tier),
        simple_retrieval_params={},  # deterministic scope-only match
    ):
        m = getattr(rm, "memory", None)
        if m and (getattr(m, "fact", "") or "").startswith(_FACT_PREFIX):
            yield m


def _parse(fact: str) -> dict | None:
    try:
        return json.loads(fact[len(_FACT_PREFIX):])
    except ValueError:
        return None


def _bank_recall(user_id: str, tier: str) -> list[dict]:
    try:
        out = []
        for m in _iter_memories(_client(), user_id, tier):
            rec = _parse(m.fact)
            if rec is not None:
                rec["_update_time"] = getattr(m, "update_time", None)
                out.append(rec)
        out.sort(key=lambda r: r.get("_update_time") or 0, reverse=True)
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[store] recall({tier}) failed, using mock: {exc}")
        return _mock_recall(user_id, tier)


def _bank_remember(user_id, tier, key, record, ttl_seconds) -> bool:
    try:
        client = _client()
        old = [
            _relative_name(m.name)
            for m in _iter_memories(client, user_id, tier)
            if getattr(m, "name", None) and (_parse(m.fact) or {}).get("key") == key
        ]
        config: dict = {"wait_for_completion": True}
        if ttl_seconds:
            config["ttl"] = f"{int(ttl_seconds)}s"
        client.agent_engines.memories.create(
            name=_engine_name(),
            fact=_FACT_PREFIX + json.dumps(record),
            scope=_scope(user_id, tier),
            config=config,
        )
        for nm in old:
            try:
                client.agent_engines.memories.delete(name=nm)
            except Exception as e:  # noqa: BLE001
                print(f"[store] cleanup delete failed for {nm}: {e}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[store] remember({tier}) failed (mock only): {exc}")
        return False


def _bank_delete_where(user_id, tier, predicate) -> bool:
    try:
        client = _client()
        names = [
            _relative_name(m.name)
            for m in _iter_memories(client, user_id, tier)
            if getattr(m, "name", None) and predicate(_parse(m.fact) or {})
        ]
        for nm in names:
            try:
                client.agent_engines.memories.delete(name=nm)
            except Exception as e:  # noqa: BLE001
                print(f"[store] delete failed for {nm}: {e}")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[store] delete_where({tier}) failed (mock only): {exc}")
        return False


# ── Mock fallback (in-process) ───────────────────────────────────────────────
# _MOCK[(user_id, tier)] = {key: record}; task records carry _created for TTL.
_MOCK: dict[tuple[str, str], dict[str, dict]] = {}


def _mock_recall(user_id: str, tier: str) -> list[dict]:
    records = list(_MOCK.get((user_id or "*", tier), {}).values())
    if tier == TIER_TASK:  # honour the 3-day TTL in the mock too
        now = time.time()
        records = [r for r in records if now - r.get("_created", now) <= TASK_TTL_SECONDS]
    records.sort(key=lambda r: r.get("_created", 0), reverse=True)
    return records


def _mock_remember(user_id, tier, key, record) -> None:
    bucket = _MOCK.setdefault((user_id or "*", tier), {})
    if tier == TIER_TASK:
        record = {**record, "_created": time.time()}
    bucket[key] = record


# ── public API ───────────────────────────────────────────────────────────────
def remember(user_id, tier, key, record, ttl_seconds=None) -> None:
    """Upsert one record (keep-latest per `key`) in the given tier."""
    record = {**record, "key": key}
    if _use_memory_bank() and user_id:
        if _bank_remember(user_id, tier, key, record, ttl_seconds):
            return
    _mock_remember(user_id or "*", tier, key, record)


def recall(user_id, tier) -> list[dict]:
    """All records in a tier, newest first."""
    if _use_memory_bank() and user_id:
        return _bank_recall(user_id, tier)
    return _mock_recall(user_id or "*", tier)


def forget(user_id, tier, key) -> None:
    """Delete the record(s) with this key in one tier."""
    if _use_memory_bank() and user_id:
        if _bank_delete_where(user_id, tier, lambda r: r.get("key") == key):
            return
    _MOCK.get((user_id or "*", tier), {}).pop(key, None)


def clear(user_id) -> None:
    """Erase BOTH tiers for this user — permanent AND temporary memory."""
    if _use_memory_bank() and user_id:
        ok = all(_bank_delete_where(user_id, t, lambda r: True) for t in TIERS)
        if ok:
            return
    for t in TIERS:
        _MOCK.pop((user_id or "*", t), None)
