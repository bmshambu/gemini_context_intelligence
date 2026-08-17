#!/usr/bin/env bash
# Deploy the background price watcher as a Cloud Run Job + schedule it hourly with
# Cloud Scheduler. SELF-CONTAINED — run from THIS folder; independent of the agent's
# CI/CD. It writes alerts to the SAME Memory Bank the deployed GE agent reads.
#
# Prereqs: gcloud auth login; enable APIs (run, cloudscheduler, aiplatform,
# cloudbuild, artifactregistry).
set -euo pipefail

# Values come from the environment (see below). For convenience, drop them in a
# local, gitignored watcher.env (copy watcher.env.example) and this sources it:
[ -f ./watcher.env ] && source ./watcher.env

PROJECT_ID="${PROJECT_ID:-your-project-id}"
REGION="${REGION:-us-central1}"
# Full resource path from your agent deploy output:
AGENT_ENGINE_ID="${AGENT_ENGINE_ID:-projects/PROJECT_NUMBER/locations/us-central1/reasoningEngines/ENGINE_ID}"
WATCH_USER="${WATCH_USER:-you@yourco.com}"          # shopper email(s), comma-separated
DROP_PCT="${DROP_PCT:-20}"
JOB_NAME="${JOB_NAME:-price-watcher}"
SCHEDULE="${SCHEDULE:-0 * * * *}"                    # hourly
# MUST match the agent's memory app name (agent default = shopping_companion):
MEMORY_APP_NAME="${MEMORY_APP_NAME:-shopping_companion}"

# Service account the JOB runs as — needs roles/aiplatform.user (Memory Bank).
JOB_SA="${JOB_SA:-price-watcher@${PROJECT_ID}.iam.gserviceaccount.com}"
# Service account Scheduler uses to invoke the job — needs roles/run.invoker.
SCHED_SA="${SCHED_SA:-$JOB_SA}"

echo "== 1) Build & deploy the Cloud Run Job (context = this folder) =="
gcloud run jobs deploy "$JOB_NAME" \
  --source . \
  --project "$PROJECT_ID" --region "$REGION" \
  --service-account "$JOB_SA" \
  --set-env-vars "AGENT_ENGINE_ID=${AGENT_ENGINE_ID},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${REGION},MEMORY_APP_NAME=${MEMORY_APP_NAME},WATCH_USER=${WATCH_USER},DROP_PCT=${DROP_PCT}"

echo "== 2) Schedule it hourly (Cloud Scheduler -> Cloud Run Jobs run API) =="
gcloud scheduler jobs create http "${JOB_NAME}-schedule" \
  --project "$PROJECT_ID" --location "$REGION" \
  --schedule "$SCHEDULE" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${JOB_NAME}:run" \
  --http-method POST \
  --oauth-service-account-email "$SCHED_SA" \
  || echo "(scheduler job may already exist — use 'gcloud scheduler jobs update http' to change it)"

cat <<EOF

Done. IAM you may need to grant once:
  gcloud projects add-iam-policy-binding $PROJECT_ID \\
    --member="serviceAccount:$JOB_SA" --role="roles/aiplatform.user"
  gcloud run jobs add-iam-policy-binding $JOB_NAME --project $PROJECT_ID --region $REGION \\
    --member="serviceAccount:$SCHED_SA" --role="roles/run.invoker"

Run it once now (immediate alert — great for a live demo):
  gcloud run jobs execute $JOB_NAME --project $PROJECT_ID --region $REGION
EOF
