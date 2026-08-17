# Watcher on Cloud Run Job + Cloud Scheduler — runbook

Deploy the background price watcher as a **Cloud Run Job** triggered hourly by
**Cloud Scheduler**. Everything runs in GCP (laptop off). This covers ONLY the
watcher — it assumes your **agent is already deployed** to Agent Engine with Memory
Bank on, and you have its engine id.

Tested end-to-end on a fresh project (region `us-central1`). Run all commands from
**Cloud Shell** (browser terminal, pre-authenticated).

---

## 0. Prerequisites

- The **agent is deployed** with `USE_MEMORY_BANK=true`, and you know its resource name
  `projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID`.
- The **`watcher/` folder** is present (has `watcher_job.py`, `memory_client.py`,
  `Dockerfile`, `requirements-watcher.txt`).
- **`MEMORY_APP_NAME` must match the agent's** (agent default = `shopping_companion`).
  If they differ, the watcher writes alerts the agent can't see.

### Set reusable variables (edit the first four)

```bash
export PROJECT_ID=your-project-id
export REGION=us-central1
export AGENT_ENGINE_ID=projects/PROJECT_NUMBER/locations/us-central1/reasoningEngines/YOUR_ENGINE_ID
export WATCH_USER=you@yourco.com          # shopper email(s), comma-separated

export PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
export COMPUTE_SA=$PROJECT_NUMBER-compute@developer.gserviceaccount.com
gcloud config set project $PROJECT_ID
```

---

## 1. Enable the APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  aiplatform.googleapis.com
```

> First-time API enablement can log a transient `code 10 / concurrent policy changes`
> IAM error while Google provisions service agents. It self-heals — wait ~1 min.

---

## 2. Grant the Compute service account permissions

New projects ship the default Compute SA with almost no roles. It needs to (a) read
the build source, push the image, write logs, and (b) write Memory Bank at runtime.

**POC-quick (broad):**

```bash
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$COMPUTE_SA" \
  --role="roles/editor"
```

**Least-privilege (recommended for real use)** — grant these instead of Editor:

```bash
for ROLE in \
  roles/storage.objectViewer \
  roles/artifactregistry.writer \
  roles/logging.logWriter \
  roles/aiplatform.user \
  roles/run.invoker ; do
  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$COMPUTE_SA" --role="$ROLE"
done
```

---

## 3. Deploy the watcher as a Cloud Run Job

From the `watcher/` folder (Cloud Build turns it into a container — no Docker needed):

```bash
cd watcher

gcloud run jobs deploy price-watcher \
  --source . \
  --region $REGION \
  --set-env-vars "AGENT_ENGINE_ID=$AGENT_ENGINE_ID,GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,MEMORY_APP_NAME=shopping_companion,WATCH_USER=$WATCH_USER,DROP_PCT=20,WATCH_PRODUCT=Smartwatch,WATCH_OLD_PRICE=₹12367"
```

- Answer **Y** if prompted to enable APIs or create the `cloud-run-source-deploy`
  Artifact Registry repo.
- First build takes a few minutes → ends with `Job [price-watcher] has successfully been deployed.`

**`WATCH_PRODUCT` / `WATCH_OLD_PRICE` = a fixed demo item** (so you can test without a
real cart). To make the watcher read the shopper's **actual cart item** instead,
remove them:

```bash
gcloud run jobs update price-watcher --region $REGION \
  --remove-env-vars WATCH_PRODUCT,WATCH_OLD_PRICE
```

---

## 4. Run it once (manual test)

```bash
gcloud run jobs execute price-watcher --region $REGION --wait
```

First run is slow (~1–2 min: cold start + `aiplatform` import + Memory Bank write).
Success = `Execution [price-watcher-xxxxx] has completed successfully.`

Check logs if needed:

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="price-watcher"' \
  --limit=30 --freshness=15m --format="value(timestamp, textPayload)"
```

You want a line like: `[watcher] you@yourco.com: Smartwatch ₹12367 -> ₹9,894 (-20%). Alert written.`

---

## 5. Let Cloud Scheduler invoke the job

Grant the Scheduler service agent permission to mint tokens as the Compute SA:

```bash
gcloud iam service-accounts add-iam-policy-binding $COMPUTE_SA \
  --member="serviceAccount:service-$PROJECT_NUMBER@gcp-sa-cloudscheduler.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator"
```

---

## 6. Create the hourly schedule

```bash
gcloud scheduler jobs create http price-watcher-hourly \
  --location $REGION \
  --schedule "0 * * * *" \
  --uri "https://$REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/price-watcher:run" \
  --http-method POST \
  --oauth-service-account-email "$COMPUTE_SA"
```

`0 * * * *` = top of every hour. From now on the watcher runs automatically — no
manual trigger needed.

---

## 7. Test the scheduler immediately (don't wait an hour)

```bash
gcloud scheduler jobs run price-watcher-hourly --location $REGION
```

Wait ~1 min (Scheduler → Run API → cold start → write), confirm the execution:

```bash
gcloud run jobs executions list --job price-watcher --region $REGION --limit 3
```

Look for the top row `COMPLETE: 1/1`. The alert is now in Memory Bank; opening the
agent and saying "hi" will surface it.

---

## Config reference (env vars on the job)

| Var | Meaning |
|---|---|
| `AGENT_ENGINE_ID` | agent's `reasoningEngines/...` — whose Memory Bank to write |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` | project + region |
| `MEMORY_APP_NAME` | **must match the agent** (default `shopping_companion`) |
| `WATCH_USER` | shopper email(s), comma-separated |
| `DROP_PCT` | simulated drop % (default 20) |
| `WATCH_PRODUCT` / `WATCH_OLD_PRICE` | optional fixed item; omit to read the real cart |

Update env vars anytime:

```bash
gcloud run jobs update price-watcher --region $REGION \
  --set-env-vars "DROP_PCT=30,WATCH_USER=other@yourco.com"
```

---

## Troubleshooting (issues we actually hit)

| Symptom | Fix |
|---|---|
| `PERMISSION_DENIED: ... API has not been used` (SERVICE_DISABLED) | Enable that API: `gcloud services enable NAME` (step 1), wait 1–2 min. |
| Build: `... compute@... does not have storage.objects.get` | Compute SA missing roles — step 2. |
| Scheduler create: *"App Engine app required"* | `gcloud app create --region=$REGION` (region is permanent), then retry step 6. |
| Scheduler run → job doesn't fire / token error | Step 5 grant needs a minute to propagate; re-run `scheduler jobs run`. |
| Agent shows no alert after a run | Timing — the alert is written ~1 min after the trigger, and it's **cleared after being shown once**. Trigger, wait ~1 min, then view. |

---

## Cleanup (stop it / avoid credit use)

```bash
gcloud scheduler jobs delete price-watcher-hourly --location $REGION
gcloud run jobs delete price-watcher --region $REGION
```

---

## Demo tip

The agent clears each alert after showing it once, so trigger a fresh write right
before you present:

```bash
gcloud run jobs execute price-watcher --region $REGION   # then wait ~1 min, open the chat
```
