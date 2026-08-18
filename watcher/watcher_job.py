"""Background price watcher — Cloud Run Job entrypoint (surrounding awareness).

One invocation = one scan. Cloud Scheduler runs it on a cron (e.g. hourly), entirely
in GCP. Each run finds the shopper's watched item, computes the (dropped) price, and
writes an alert to Memory Bank — which the deployed GE agent surfaces on the
shopper's next visit. The shopper's laptop is never involved.

Self-contained: talks to Memory Bank directly via memory_client (no agent import).

Config (env vars, set on the Cloud Run Job by deploy_watcher.sh):
    AGENT_ENGINE_ID, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION
    MEMORY_APP_NAME  — MUST match the agent's (default 'shopping_companion')
    WATCH_USER       — shopper email(s) to watch, comma-separated
    DROP_PCT         — simulated drop percentage (default 20)
    WATCH_PRODUCT / WATCH_OLD_PRICE — optional: watch a fixed item at a fixed price
                       instead of reading the shopper's cart (handy for a scripted demo)
"""
import os

import memory_client as mc


def scan_user(user_id: str, drop_pct: float, product: str = "", old_price: str = "") -> None:
    factor = 1 - drop_pct / 100
    if product and old_price:  # fixed configured item
        pid = product.lower().replace(" ", "-")
        new_price = mc.scale_price(old_price, factor)
        mc.write_alert(user_id, pid, product, old_price, new_price, drop_pct)
        print(f"[watcher] {user_id}: {product} {old_price} -> {new_price} (-{drop_pct:.0f}%). Alert written.")
        return

    order = mc.get_order(user_id)  # else watch their cart item
    if not order or not order.get("product_id"):
        print(f"[watcher] {user_id}: no cart item to watch")
        return
    old = order.get("price_display", "")
    new_price = mc.scale_price(old, factor)
    mc.write_alert(user_id, order["product_id"], order.get("product_name", "your item"),
                   old, new_price, drop_pct)
    print(f"[watcher] {user_id}: {order.get('product_name')} {old} -> {new_price} "
          f"(-{drop_pct:.0f}%). Alert written.")


def main() -> None:
    users = [u.strip() for u in os.getenv("WATCH_USER", "").split(",") if u.strip()]
    if not users:
        # No fixed user → discover real GE shoppers (with carts) from Memory Bank.
        users = mc.list_watch_users()
        print(f"[watcher] WATCH_USER not set — watching {len(users)} shopper(s) "
              f"discovered from Memory Bank: {users}")
    if not users:
        print("[watcher] No shoppers to watch (no carts in Memory Bank).")
        return
    drop_pct = float(os.getenv("DROP_PCT", "20"))
    product = os.getenv("WATCH_PRODUCT", "")
    old_price = os.getenv("WATCH_OLD_PRICE", "")
    for u in users:
        try:
            scan_user(u, drop_pct, product, old_price)
        except Exception as exc:  # noqa: BLE001 — one bad user shouldn't fail the run
            print(f"[watcher] {u}: error {exc}")


if __name__ == "__main__":
    main()
