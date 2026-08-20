# Watches — Cloud Tasks + Handler deploy runbook

Deploys the **chat-driven watches** background side: a **Cloud Tasks queue** + the
**watch handler** (a Cloud Run *service* that Cloud Tasks calls). No Cloud Scheduler —
the agent enqueues the first check from chat; each check re-enqueues the next. See
[../DESIGN_watches.md](../DESIGN_watches.md).

Replace inline: project ID `YOUR_PROJECT`, number `YOUR_NUMBER`, region `us-central1`,
engine id `YOUR_ENGINE_ID`. Run from the `watch_handler/` folder in Cloud Shell.

## 1. Enable APIs

```bash
gcloud services enable run.googleapis.com cloudtasks.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com aiplatform.googleapis.com
```

## 2. Create the Cloud Tasks queue

```bash
gcloud tasks queues create watch-checks --location us-central1
```

## 3. Deploy the handler (Cloud Run service)

```bash
gcloud run deploy watch-handler \
  --source . \
  --region us-central1 \
  --no-allow-unauthenticated \
  --set-env-vars "AGENT_ENGINE_ID=projects/YOUR_NUMBER/locations/us-central1/reasoningEngines/YOUR_ENGINE_ID,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT,GOOGLE_CLOUD_LOCATION=us-central1,MEMORY_APP_NAME=shopping_companion,TASKS_QUEUE=watch-checks,TASKS_LOCATION=us-central1,SIM_DROP_PCT=20,WATCH_INTERVAL_SECONDS=60,WATCH_TTL_SECONDS=259200"
```

Grab the URL it prints (e.g. `https://watch-handler-xxxx-uc.a.run.app`) → that's
`WATCH_HANDLER_URL`. Set it on the handler itself too (it needs it to re-enqueue):

```bash
gcloud run services update watch-handler --region us-central1 \
  --set-env-vars "WATCH_HANDLER_URL=https://watch-handler-xxxx-uc.a.run.app,TASKS_INVOKER_SA=YOUR_NUMBER-compute@developer.gserviceaccount.com"
```

## 4. IAM

```bash
# handler's SA (default compute) → Memory Bank read/write + enqueue next checks
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="serviceAccount:YOUR_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="serviceAccount:YOUR_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/cloudtasks.enqueuer"
# Cloud Tasks invokes the handler as that SA (OIDC) → it must be allowed to call the service
gcloud run services add-iam-policy-binding watch-handler --region us-central1 \
  --member="serviceAccount:YOUR_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"
```

(In a corporate project you may hit the same Cloud Build SA + conditional-IAM prompts
as the watcher — see `../watcher/cloud_run_v2.md` §2.)

## 5. Wire the agent (so chat can enqueue), then redeploy it

Add these to the agent's `.env.dev` as `AGENT_VAR_…` and redeploy the agent:

```
AGENT_VAR_WATCH_HANDLER_URL=https://watch-handler-xxxx-uc.a.run.app
AGENT_VAR_TASKS_QUEUE=watch-checks
AGENT_VAR_TASKS_LOCATION=us-central1
AGENT_VAR_TASKS_INVOKER_SA=YOUR_NUMBER-compute@developer.gserviceaccount.com
AGENT_VAR_WATCH_INTERVAL_SECONDS=60        # demo (prod: 3600)
AGENT_VAR_WATCH_TTL_SECONDS=259200         # 3 days
AGENT_VAR_MAX_WATCHES_PER_USER=10
```

The agent's own SA also needs `roles/cloudtasks.enqueuer` (grant it like §4 to the
agent engine's service account).

## 6. Test from chat

1. *"Watch the Yoga Mat and alert me if it drops 15%."* → agent confirms + sets it up.
2. Wait ~1 min (interval=60s; the simulated 20% drop meets 15% on the first check).
3. *"Hi"* → the agent leads with the price drop.
4. *"What am I watching?"* / *"stop watching the yoga mat"* → list / terminate.

## Env reference

| Var | Handler | Agent | Meaning |
|---|---|---|---|
| `AGENT_ENGINE_ID` | ✓ | ✓ | which Memory Bank |
| `MEMORY_APP_NAME` | ✓ | ✓ | must match (default `shopping_companion`) |
| `WATCH_HANDLER_URL` | ✓ | ✓ | the handler URL tasks POST to |
| `TASKS_QUEUE` / `TASKS_LOCATION` | ✓ | ✓ | the queue |
| `TASKS_INVOKER_SA` | ✓ | ✓ | SA for OIDC auth to the handler |
| `WATCH_INTERVAL_SECONDS` | ✓ | ✓ | re-check cadence (demo 60 / prod 3600) |
| `SIM_DROP_PCT` | ✓ | — | simulated drop % (demo) |
| `WATCH_TTL_SECONDS` | ✓ | ✓ | watch expiry (3 days) |
| `MAX_WATCHES_PER_USER` | — | ✓ | cap (10) |

## Cleanup

```bash
gcloud run services delete watch-handler --region us-central1 --quiet
gcloud tasks queues delete watch-checks --location us-central1 --quiet
```
