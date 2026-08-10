"""Create-or-update the Context Intelligence agent on Vertex AI Agent Engine.

Selects .env.{dev|prod} via DEPLOYMENT_ENVIRONMENT (default dev), stages to the
configured GCS bucket, and matches an existing deployment by display name so
redeploys keep the same resource name (GE registration survives updates).

Local aiplatform/adk versions MUST match agent_requirements below — AdkApp pickles
the agent locally and unpickles it in the container.
"""
import os

import vertexai
from dotenv import load_dotenv
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp

environmentsuffix = os.getenv("DEPLOYMENT_ENVIRONMENT", "dev").lower()
load_dotenv(dotenv_path=f".env.{environmentsuffix}")

PROJECT_ID                  = os.getenv("GCP_CLOUD_PROJECT", "")
GOOGLE_CLOUD_STAGING_BUCKET = os.getenv("GOOGLE_CLOUD_STAGING_BUCKET", "")
GOOGLE_CLOUD_LOCATION       = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
AGENT_DISPLAY_NAME          = os.getenv("AGENT_DISPLAY_NAME", "Context Intelligence Agent")
AGENT_DESCRIPTION           = os.getenv(
    "AGENT_DESCRIPTION",
    "Persona-memory learning companion (two-tier Memory Bank, markdown).",
)

# Any local var prefixed AGENT_VAR_ is pushed onto the deployed agent (prefix
# stripped) — e.g. AGENT_VAR_USE_MEMORY_BANK, AGENT_VAR_AGENT_ENGINE_ID.
agent_env_vars: dict = {}
for key, value in os.environ.items():
    if key.startswith("AGENT_VAR_"):
        agent_env_vars[key.removeprefix("AGENT_VAR_")] = str(value)

# Agent Engine RESERVES (and auto-injects) some env var names; setting them via
# env_vars fails with FAILED_PRECONDITION. store.py reads the injected values.
_RESERVED_ENV = {
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_CLOUD_REGION",
    "GOOGLE_APPLICATION_CREDENTIALS", "PORT", "K_SERVICE", "K_REVISION", "K_CONFIGURATION",
}
dropped = sorted(k for k in agent_env_vars if k in _RESERVED_ENV)
for k in dropped:
    agent_env_vars.pop(k)
if dropped:
    print(f"Skipping reserved env vars (Agent Engine injects these itself): {dropped}")
print(f"Env vars for the agent ({len(agent_env_vars)}): {sorted(agent_env_vars.keys())}")

print(f"\nDeploying '{AGENT_DISPLAY_NAME}' to project '{PROJECT_ID}'...")

from agent.agent import root_agent  # noqa: E402 — import after env is set up

vertexai.init(
    project=PROJECT_ID, location=GOOGLE_CLOUD_LOCATION,
    staging_bucket=GOOGLE_CLOUD_STAGING_BUCKET,
)

app = AdkApp(agent=root_agent, enable_tracing=True)

agent = next(
    (a for a in agent_engines.list() if a.display_name == AGENT_DISPLAY_NAME),
    None,
)
print(f"Found existing agent: {agent.resource_name}" if agent
      else "No existing agent found. Creating a new one...")

agent_requirements = [
    "google-cloud-aiplatform[agent_engines,adk]==1.148.1",
    "google-adk==1.31.1",
    "a2a-sdk>=0.3.4,<0.4",
    "python-dotenv>=1.0.0",
    "google-cloud-secret-manager",
    "google-cloud-storage",
    "cloudpickle",
    "pydantic",
]

if agent:
    print("Updating the existing agent...")
    remote_app = agent_engines.update(
        resource_name=agent.resource_name,
        display_name=agent.display_name,
        agent_engine=app,
        description=AGENT_DESCRIPTION,
        requirements=agent_requirements,
        extra_packages=["./agent"],
        env_vars=agent_env_vars,
    )
    print(f"=======> Success! Updated: {remote_app.resource_name}")
else:
    print("Creating a new agent...")
    remote_app = agent_engines.create(
        agent_engine=app,
        requirements=agent_requirements,
        display_name=AGENT_DISPLAY_NAME,
        description=AGENT_DESCRIPTION,
        extra_packages=["./agent"],
        env_vars=agent_env_vars,
    )
    print(f"=======> Success! Deployed: {remote_app.resource_name}")
