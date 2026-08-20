"""Cloud Tasks client — the TRIGGER layer for watches.

The agent enqueues a one-off check task when a watch is created/resumed; the handler
re-enqueues the next check. There is NO Cloud Scheduler — each watch is its own
self-rescheduling chain (see DESIGN_watches.md).

Guarded: if Cloud Tasks isn't configured (or on any error) enqueue/delete no-op and
return None, so the agent runs locally and tests pass without GCP. Configure via env:
    WATCH_HANDLER_URL   — the Cloud Run handler URL that a task POSTs to
    TASKS_QUEUE         — Cloud Tasks queue id
    TASKS_LOCATION      — queue region (default GOOGLE_CLOUD_LOCATION or us-central1)
    GOOGLE_CLOUD_PROJECT
    TASKS_INVOKER_SA    — service account email for OIDC auth to the handler
"""
from __future__ import annotations

import json
import os


def enabled() -> bool:
    return bool(os.getenv("WATCH_HANDLER_URL") and os.getenv("TASKS_QUEUE")
               and (os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_CLOUD_PROJECT")))


def _location() -> str:
    return os.getenv("TASKS_LOCATION") or os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")


def _project() -> str:
    return os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_CLOUD_PROJECT")


def enqueue_check(user_id, product_id, delay_seconds) -> str | None:
    """Schedule a single price check for this watch at now + delay_seconds. Returns
    the created task's name (for later deletion), or None if Cloud Tasks is disabled."""
    if not enabled():
        print(f"[tasks] (disabled) would enqueue check for {user_id}/{product_id} in {delay_seconds}s")
        return None
    try:
        import datetime
        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(_project(), _location(), os.getenv("TASKS_QUEUE"))
        when = timestamp_pb2.Timestamp()
        when.FromDatetime(datetime.datetime.now(datetime.timezone.utc)
                          + datetime.timedelta(seconds=int(delay_seconds)))
        task = {
            "schedule_time": when,
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": os.getenv("WATCH_HANDLER_URL"),
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"user_id": user_id, "product_id": product_id}).encode(),
            },
        }
        sa = os.getenv("TASKS_INVOKER_SA")
        if sa:
            task["http_request"]["oidc_token"] = {"service_account_email": sa}
        created = client.create_task(parent=parent, task=task)
        return created.name
    except Exception as exc:  # noqa: BLE001 — never break the chat turn
        print(f"[tasks] enqueue failed: {exc}")
        return None


def delete_task(task_name) -> None:
    """Cancel a pending check (used when a watch is stopped)."""
    if not task_name or not enabled():
        return
    try:
        from google.cloud import tasks_v2
        tasks_v2.CloudTasksClient().delete_task(name=task_name)
    except Exception as exc:  # noqa: BLE001
        print(f"[tasks] delete failed for {task_name}: {exc}")
