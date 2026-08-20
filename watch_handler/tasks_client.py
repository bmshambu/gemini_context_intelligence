"""Cloud Tasks re-enqueue for the watch handler (self-contained). Each check that
doesn't fire schedules the next one — the self-rescheduling chain. Guarded: no-ops if
Cloud Tasks isn't configured."""
import datetime
import json
import os


def enabled() -> bool:
    return bool(os.getenv("WATCH_HANDLER_URL") and os.getenv("TASKS_QUEUE")
               and os.getenv("GOOGLE_CLOUD_PROJECT"))


def enqueue_check(user_id, product_id, delay_seconds) -> str | None:
    if not enabled():
        print(f"[handler-tasks] (disabled) would re-enqueue {user_id}/{product_id} in {delay_seconds}s")
        return None
    try:
        from google.cloud import tasks_v2
        from google.protobuf import timestamp_pb2

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(os.getenv("GOOGLE_CLOUD_PROJECT"),
                                   os.getenv("TASKS_LOCATION", os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")),
                                   os.getenv("TASKS_QUEUE"))
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
        return client.create_task(parent=parent, task=task).name
    except Exception as exc:  # noqa: BLE001
        print(f"[handler-tasks] enqueue failed: {exc}")
        return None
