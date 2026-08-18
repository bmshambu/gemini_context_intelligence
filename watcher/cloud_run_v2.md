# Watcher on Cloud Run Job + Cloud Scheduler — v2 (real GE users, corporate project)

Same watcher as [`cloud_run.md`](cloud_run.md), updated for a **real / corporate**
project. Two differences from v1:

1. **No hardcoded shopper.** The watcher **auto-discovers real GE users** (everyone
   with a cart) from Memory Bank — because GE stores each shopper's cart under their
   real login email (`user_id`). Leave `WATCH_USER` **unset**.
2. **Extra IAM** that locked-down corporate projects need (Cloud Build service
   account, act-as, and the conditional-policy prompt).

Literal commands, no variables. **Values below are our office example — replace:**
project ID `prj-us-bpg-spark-poc`, project NUMBER `901535160018`, region `us-central1`,
engine id `180647011664527360`. Get your project number:
`gcloud projects describe prj-us-bpg-spark-poc --format='value(projectNumber)'`.

> ⚠️ In a corporate project you may lack IAM-admin rights or hit org policy. If a
> grant is denied, your **platform/DevOps team** must run it (or build via your CI/CD).

---

## 1. Enable the APIs

```bash
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com aiplatform.googleapis.com
```

## 2. IAM grants

> If a grant prompts **"specify a condition"** (policies with conditional bindings),
> choose **`[2] None`** — an unconditional grant.

**2a. Compute service account — build + runtime access** (broad, POC-quick):

```bash
gcloud projects add-iam-policy-binding prj-us-bpg-spark-poc --member="serviceAccount:901535160018-compute@developer.gserviceaccount.com" --role="roles/editor"
```

If `roles/editor` is blocked by org policy, grant these instead:
`roles/storage.objectViewer`, `roles/artifactregistry.writer`, `roles/logging.logWriter`,
`roles/aiplatform.user`, `roles/run.invoker` (one `add-iam-policy-binding` each).

**2b. Cloud Build source-deploy needs the builder role + act-as** (the errors
"default service account is missing required IAM permissions" / "permission to act as
service account"):

```bash
gcloud projects add-iam-policy-binding prj-us-bpg-spark-poc --member="serviceAccount:901535160018-compute@developer.gserviceaccount.com" --role="roles/cloudbuild.builds.builder"
```
```bash
gcloud iam service-accounts add-iam-policy-binding 901535160018-compute@developer.gserviceaccount.com --member="user:YOUR_EMAIL@company.com" --role="roles/iam.serviceAccountUser"
```

## 3. Deploy the watcher (auto-discover real users)

From the `watcher/` folder. **No `WATCH_USER`** → it scans every shopper with a cart:

```bash
cd watcher
```
```bash
gcloud run jobs deploy price-watcher --source . --region us-central1 --set-env-vars "AGENT_ENGINE_ID=projects/901535160018/locations/us-central1/reasoningEngines/180647011664527360,GOOGLE_CLOUD_PROJECT=prj-us-bpg-spark-poc,GOOGLE_CLOUD_LOCATION=us-central1,MEMORY_APP_NAME=shopping_companion,DROP_PCT=20"
```

Answer **Y** to enable APIs / create the `cloud-run-source-deploy` repo if prompted.

> Requires the discovery code (`list_watch_users`) — it's in the repo. If your clone
> predates it, apply the patches in the Appendix first.

## 4. Run once + see who it found

```bash
gcloud run jobs execute price-watcher --region us-central1 --wait
```
```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="price-watcher"' --limit=20 --freshness=10m --format="value(textPayload)"
```

Look for: `watching N shopper(s) from Memory Bank: ['you@company.com', ...]` then a
`... -> ... Alert written.` line per user. Then say "hi" in GE → the drop surfaces.

## 5. Schedule it hourly

```bash
gcloud iam service-accounts add-iam-policy-binding 901535160018-compute@developer.gserviceaccount.com --member="serviceAccount:service-901535160018@gcp-sa-cloudscheduler.iam.gserviceaccount.com" --role="roles/iam.serviceAccountTokenCreator"
```
```bash
gcloud scheduler jobs create http price-watcher-hourly --location us-central1 --schedule "0 * * * *" --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/prj-us-bpg-spark-poc/jobs/price-watcher:run" --http-method POST --oauth-service-account-email "901535160018-compute@developer.gserviceaccount.com"
```

Test now (don't wait an hour):

```bash
gcloud scheduler jobs run price-watcher-hourly --location us-central1
```

---

## Who the watcher watches — 3 modes

| Mode | How | Use |
|---|---|---|
| **Discover all real users** (default) | leave `WATCH_USER` unset | production — watches every shopper with a cart |
| **One specific user** | `--set-env-vars WATCH_USER=you@company.com` | test/target one real account |
| **Fixed demo item** | add `WATCH_PRODUCT=Smartwatch,WATCH_OLD_PRICE=₹12367` | scripted demo, no real cart needed |

Switch modes anytime:

```bash
# target one real user
gcloud run jobs update price-watcher --region us-central1 --set-env-vars WATCH_USER=you@company.com
# back to discover-all
gcloud run jobs update price-watcher --region us-central1 --remove-env-vars WATCH_USER,WATCH_PRODUCT,WATCH_OLD_PRICE
```

---

## Troubleshooting (what we hit in the office project)

- **`... API has not been used / SERVICE_DISABLED`** → enable it (step 1), wait 1–2 min.
- **"specify a condition is required"** on an IAM grant → choose **`[2] None`**.
- **Build: `... compute@... does not have storage.objects.get`** → step 2a.
- **Build: "default service account is missing required IAM permissions" / "act as service account"** → step 2b.
- **`roles/editor` denied** → org policy; use the least-privilege list (2a) or ask DevOps.
- **Alert doesn't show** → written ~1 min after the run, and cleared after being shown once. Trigger, wait ~1 min, then open the chat.
- **`MEMORY_APP_NAME` mismatch** → the agent won't see the alerts. It must equal the agent's (default `shopping_companion`).

## Cleanup

```bash
gcloud scheduler jobs delete price-watcher-hourly --location us-central1 --quiet
```
```bash
gcloud run jobs delete price-watcher --region us-central1 --quiet
```

---

## Appendix — patch an older clone (only if `list_watch_users` is missing)

Run from the `watcher/` folder.

**Add discovery to `memory_client.py`:**

```bash
cat >> memory_client.py <<'EOF'


def list_watch_users():
    """Discover real GE shoppers (those with a cart) from Memory Bank, so we don't
    hardcode WATCH_USER. GE stores each shopper's cart under their real email
    (user_id); we read those emails back."""
    client = _client()
    users = set()
    for m in client.agent_engines.memories.list(name=_engine_name()):
        scope = getattr(m, "scope", None) or {}
        fact = getattr(m, "fact", "") or ""
        if (scope.get("app_name") == APP_NAME and scope.get("tier") == TIER_TASK
                and fact.startswith(FACT_PREFIX)):
            uid = scope.get("user_id")
            if uid:
                users.add(uid)
    return sorted(users)
EOF
```

**Make `main()` fall back to discovery** — in `watcher_job.py`, replace:

```python
    if not users:
        print("[watcher] WATCH_USER not set — nothing to do.")
        return
```

with:

```python
    if not users:
        users = mc.list_watch_users()
        print(f"[watcher] WATCH_USER not set — watching {len(users)} shopper(s) from Memory Bank: {users}")
    if not users:
        print("[watcher] No shoppers to watch (no carts in Memory Bank).")
        return
```

Then redeploy (step 3).
