# Price Watcher — background job (surrounding awareness)

The **background half** of surrounding awareness, kept in its own folder so it
**deploys separately** from the GE agent (the agent has its own CI/CD; this doesn't
touch it). It runs in **GCP** — the shopper's laptop is never involved.

```
Cloud Scheduler (hourly)  →  Cloud Run Job (this folder)  →  writes price-drop alert
                                                                    │  to Memory Bank
        deployed GE agent surfaces it on the shopper's return ──────┘
```

## Self-contained by design

This folder does **not** import the agent package. It talks to Memory Bank directly
via `memory_client.py`, so the Docker build context is just this folder.

> ⚠️ **Shared contract.** `memory_client.py` mirrors a few constants from the agent's
> `agent/store.py` + `agent/alerts.py` — the fact prefix (`CTXMEM1 `), the scope keys
> (`app_name`, `user_id`, `tier`), the tier names (`task`, `alert`), and the
> order/alert record shapes. If you change those in the agent, change them here too,
> or the agent won't see the alerts. `MEMORY_APP_NAME` **must match** the agent's
> (default `shopping_companion`).

## Files

- `watcher_job.py` — entrypoint; one scan per run (reads the cart item, or a fixed
  configured item, and writes the alert)
- `memory_client.py` — minimal Memory Bank read/write (the shared contract)
- `Dockerfile` / `requirements-watcher.txt` — lean image (Memory Bank only, no ADK)
- `deploy_watcher.sh` — `gcloud run jobs deploy` + `gcloud scheduler` + IAM notes

## Deploy (run from this folder)

Config comes from env vars. Easiest: copy the template and fill it in — the deploy
script sources it automatically:

```bash
cp watcher.env.example watcher.env    # then edit watcher.env (gitignored)
bash deploy_watcher.sh
```

Or pass them inline instead of using the file:

```bash
PROJECT_ID=your-proj REGION=us-central1 \
AGENT_ENGINE_ID=projects/.../reasoningEngines/... WATCH_USER=you@yourco.com \
bash deploy_watcher.sh
```

IAM to grant once (the script prints these): the **job** SA needs
`roles/aiplatform.user`; the **scheduler** SA needs `roles/run.invoker` on the job.

Run once for a live demo (immediate alert):

```bash
gcloud run jobs execute price-watcher --project your-proj --region us-central1
```

Then open the GE agent and say "hi" — it leads with the price drop.

## Config (env vars on the job)

| Var | Meaning |
|---|---|
| `AGENT_ENGINE_ID` | the agent's `reasoningEngines/...` (whose Memory Bank to write) |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | project + region |
| `MEMORY_APP_NAME` | must match the agent (default `shopping_companion`) |
| `WATCH_USER` | shopper email(s), comma-separated |
| `DROP_PCT` | simulated drop % (default 20) |
| `WATCH_PRODUCT` / `WATCH_OLD_PRICE` | optional: watch a fixed item at a fixed price instead of the cart |

## Local test (optional)

```bash
pip install -r requirements-watcher.txt
AGENT_ENGINE_ID=... GOOGLE_CLOUD_PROJECT=... WATCH_USER=you@yourco.com \
  WATCH_PRODUCT="Smartwatch (Fitness+)" WATCH_OLD_PRICE="₹12,367" \
  python watcher_job.py
```
