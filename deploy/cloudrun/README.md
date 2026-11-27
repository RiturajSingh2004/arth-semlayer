# Deploying ARTH: Semantic Layer on Google Cloud Run

Production deployment recipe combining:
1. **Cloud Run Service (`--no-allow-unauthenticated`)** — hosts the Ryoku Governance Console (`semlayer serve --web`) behind Cloud Run IAM / IAP login.
2. **Secret Manager** — injects `GEMINI_API_KEY` at runtime without baking secrets into images or `.env` files.
3. **Cloud Run Job + Cloud Scheduler** — runs `semlayer canary --log-json` hourly to probe certified metrics with `LIMIT 0` over BigQuery MCP (`execute_sql_readonly`) and automatically suspend broken definitions.
4. **Cloud Logging & Cloud Monitoring** — captures single-line structured JSON (`severity`, `event`, `metric`, `role`, `reason`) from `stderr` for refusal/suspension alerting.

## Prerequisites
- `gcloud` CLI authenticated (`gcloud auth login`)
- BigQuery MCP enabled on your project:
  ```bash
  export PROJECT_ID="YOUR_PROJECT_ID"
  export REGION="us-central1"
  export DATASET="jaffle_shop"

  gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    cloudscheduler.googleapis.com \
    bigquery.googleapis.com \
    iamcredentials.googleapis.com \
    --project="${PROJECT_ID}"

  gcloud beta services mcp enable bigquery.googleapis.com --project="${PROJECT_ID}"
  ```

## One-Shot Deployment
Store your AI Studio Gemini key in Secret Manager once, then run `deploy/cloudrun/deploy.sh`:
```bash
printf "%s" "${GEMINI_API_KEY}" | gcloud secrets create gemini-api-key \
  --data-file=- --project="${PROJECT_ID}"

bash deploy/cloudrun/deploy.sh
```

## Accessing the IAM-Protected Governance Console
Because the service deploys with `--no-allow-unauthenticated`, unauthenticated public requests are blocked at Google's edge. Open a local authenticated proxy:
```bash
gcloud run services proxy arth-semlayer-console --region="${REGION}" --project="${PROJECT_ID}"
# Open http://127.0.0.1:8080
```

## Cloud Monitoring Alert Policy Filter (Optional)
Create a log-based alert in Cloud Monitoring using this Cloud Logging query to page owners whenever `canary` suspends a broken metric or an agent hits repeated RBAC refusals:
```text
resource.type=("cloud_run_revision" OR "cloud_run_job")
jsonPayload.component="arth-semlayer"
(jsonPayload.event="audit.suspend" OR jsonPayload.event="usage.refused")
```
