"""Watch handler — Cloud Run service invoked by a Cloud Task (one check per call).

Flow per call (body: {user_id, product_id}):
  read the watch → if not active/expired, stop → check the (simulated) price against
  the condition → if met: write an alert + mark triggered (one-shot, no re-enqueue);
  else: re-enqueue the next check (the self-rescheduling chain). No Cloud Scheduler.

Price is SIMULATED for the demo: current = base * (1 - SIM_DROP_PCT/100), so most
conditions fire on the first check. Swap in a real price feed for production.
"""
import os
import time

from flask import Flask, request

import memory_client as mc
import tasks_client as tc
from conditions import condition_met

app = Flask(__name__)

SIM_DROP_PCT = float(os.getenv("SIM_DROP_PCT", "20"))


@app.post("/")
def handle():
    data = request.get_json(silent=True) or {}
    uid, pid = data.get("user_id"), data.get("product_id")
    if not uid or not pid:
        return ("missing user_id/product_id", 400)

    w = mc.get_watch(uid, pid)
    if not w:
        return ("no such watch", 200)
    if w.get("status") != "active":
        return (f"watch not active (status={w.get('status')})", 200)
    if w.get("expires_at") and time.time() > w["expires_at"]:
        mc.update_watch(uid, pid, {"status": "expired"})
        return ("watch expired", 200)

    base = mc.price_number(w.get("price_display", ""))
    current = round(base * (1 - SIM_DROP_PCT / 100))

    if condition_met(w.get("condition", {}), base, current):
        new_display = mc.scale_price(w.get("price_display", ""), 1 - SIM_DROP_PCT / 100)
        mc.write_alert(uid, pid, w.get("product_name", "your item"),
                       w.get("price_display", ""), new_display, SIM_DROP_PCT)
        mc.update_watch(uid, pid, {"status": "triggered", "last_checked_at": time.time()})
        print(f"[handler] {uid}: {w.get('product_name')} met condition -> alert; watch triggered")
        return ("triggered", 200)

    # not met → keep watching: re-enqueue the next check
    task = tc.enqueue_check(uid, pid, int(w.get("interval_seconds", 3600)))
    mc.update_watch(uid, pid, {"last_checked_at": time.time(), "pending_task_name": task})
    print(f"[handler] {uid}: {w.get('product_name')} not met -> re-enqueued")
    return ("rechecked", 200)


@app.get("/health")
def health():
    return ("ok", 200)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
