#!/usr/bin/env bash
# Set Gemini Enterprise starter prompts — the clickable suggestion chips shown
# BEFORE the shopper types anything. Clicking one sends it as a normal message, so
# it flows through the agent's opener (onboarding / resume / recommendations).
#
# These are GE ASSISTANT CONFIG, not agent code — they live on the GE app, not the
# deployed Agent Engine. Keep the text in sync with starter_prompt.json.
#
# Prereqs: gcloud auth login with access to the GE app's project.
# Fill in these three values from your GE app (Admin console -> app details):
PROJECT_ID="${PROJECT_ID:-YOUR_PROJECT_ID}"
LOCATION="${LOCATION:-global}"          # GE apps are usually in "global"
ENGINE_ID="${ENGINE_ID:-YOUR_APP_ID}"   # the GE app / engine id

ENDPOINT_HOST="discoveryengine.googleapis.com"
[ "$LOCATION" != "global" ] && ENDPOINT_HOST="${LOCATION}-discoveryengine.googleapis.com"

URL="https://${ENDPOINT_HOST}/v1/projects/${PROJECT_ID}/locations/${LOCATION}/collections/default_collection/engines/${ENGINE_ID}/assistants/default_assistant?updateMask=starterPrompts"

curl -sS -X PATCH \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  "${URL}" \
  -d '{
    "starterPrompts": [
      { "text": "Hi, let'\''s start shopping" },
      { "text": "Set up my shopping profile" },
      { "text": "Show me some recommendations" },
      { "text": "Resume my order" }
    ]
  }'
