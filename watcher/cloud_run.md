# Watcher on Cloud Run Job + Cloud Scheduler — steps (literal, no variables)

The exact commands we ran, in order, to deploy the price watcher as a **Cloud Run
Job** + **Cloud Scheduler** — everything in Cloud Shell, no `export`. Covers ONLY the
watcher (assumes the agent is already deployed with Memory Bank on).

> These use our demo project's values. **For a different project, replace in every
> command below:**
> - **project ID** `context-aware-poc`
> - **project NUMBER** `892483915520` (in the service-account emails)
> - **region** `us-central1`
> - **engine id** `3702057299989233664`
> - **WATCH_USER** `shopper@test.com`
>
> Get your project number with:
> `gcloud projects describe context-aware-poc --format='value(projectNumber)'`
> (`context-aware-poc` = project ID; `892483915520` = project number — both appear below.)

---

## 1. Enable the APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com aiplatform.googleapis.com
```

## 2. Give the Compute service account permission

(New projects give it almost no roles — this fixes the build's source read AND the
job writing Memory Bank at runtime.)

```bash
gcloud projects add-iam-policy-binding context-aware-poc --member="serviceAccount:892483915520-compute@developer.gserviceaccount.com" --role="roles/editor"
```

## 3. Deploy the watcher as a Cloud Run Job

```bash
cd watcher
```

```bash
gcloud run jobs deploy price-watcher --source . --region us-central1 --set-env-vars "AGENT_ENGINE_ID=projects/892483915520/locations/us-central1/reasoningEngines/3702057299989233664,GOOGLE_CLOUD_PROJECT=context-aware-poc,GOOGLE_CLOUD_LOCATION=us-central1,MEMORY_APP_NAME=shopping_companion,WATCH_USER=shopper@test.com,DROP_PCT=20,WATCH_PRODUCT=Smartwatch,WATCH_OLD_PRICE=₹12367"
```

Answer **Y** if it asks to enable APIs or create the `cloud-run-source-deploy` repo.

## 4. Run it once to test

```bash
gcloud run jobs execute price-watcher --region us-central1 --wait
```

Check the log line if you want:

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="price-watcher"' --limit=30 --freshness=15m --format="value(timestamp, textPayload)"
```

## 5. Let Cloud Scheduler invoke the job

```bash
gcloud iam service-accounts add-iam-policy-binding 892483915520-compute@developer.gserviceaccount.com --member="serviceAccount:service-892483915520@gcp-sa-cloudscheduler.iam.gserviceaccount.com" --role="roles/iam.serviceAccountTokenCreator"
```

## 6. Create the hourly schedule

```bash
gcloud scheduler jobs create http price-watcher-hourly --location us-central1 --schedule "0 * * * *" --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/context-aware-poc/jobs/price-watcher:run" --http-method POST --oauth-service-account-email "892483915520-compute@developer.gserviceaccount.com"
```

## 7. Test the scheduler now (don't wait an hour)

```bash
gcloud scheduler jobs run price-watcher-hourly --location us-central1
```

Wait ~1 minute, then confirm the execution completed:

```bash
gcloud run jobs executions list --job price-watcher --region us-central1 --limit 3
```

Look for the top row `COMPLETE: 1/1`.

---

## Watch the real cart instead of the fixed item

Steps 3's `WATCH_PRODUCT` / `WATCH_OLD_PRICE` are a fixed demo item. To make the
watcher read the shopper's **actual cart**, remove them:

```bash
gcloud run jobs update price-watcher --region us-central1 --remove-env-vars WATCH_PRODUCT,WATCH_OLD_PRICE
```

## Change a setting later (e.g. drop % or user)

```bash
gcloud run jobs update price-watcher --region us-central1 --set-env-vars "DROP_PCT=30,WATCH_USER=other@yourco.com"
```

---

## Troubleshooting (what we hit)

- **`... API has not been used / SERVICE_DISABLED`** → enable it (step 1), wait 1–2 min.
- **Build: `... compute@... does not have storage.objects.get`** → step 2 grant.
- **Scheduler create: "App Engine app required"** → `gcloud app create --region=us-central1`, then retry step 6.
- **Alert doesn't show after a run** → it's written ~1 min after the trigger and is
  **cleared after being shown once**. Trigger, wait ~1 min, then open the chat.

## Cleanup (stop cost)

```bash
gcloud scheduler jobs delete price-watcher-hourly --location us-central1 --quiet
```

```bash
gcloud run jobs delete price-watcher --region us-central1 --quiet
```

## Reminders

- **`MEMORY_APP_NAME` must match the agent's** (default `shopping_companion`), or the
  agent won't see the alerts.
- The demo used a dummy `WATCH_USER`. In real GE, the shopper's email is their login
  identity (`user_id`), already stored in Memory Bank — a production watcher enumerates
  those users instead of a fixed email.
