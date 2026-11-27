#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID to your GCP project ID}"
REGION="${REGION:-us-central1}"
DATASET="${DATASET:-jaffle_shop}"
SERVICE_NAME="${SERVICE_NAME:-arth-semlayer-console}"
CANARY_JOB="${CANARY_JOB:-arth-semlayer-canary}"
SA_NAME="${SA_NAME:-semlayer-runtime}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
IMAGE="gcr.io/${PROJECT_ID}/arth-semlayer:latest"

echo "==> Ensuring least-privilege runtime service account: ${SA_EMAIL}"
gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="ARTH SemLayer Read-Only Runtime" \
    --project="${PROJECT_ID}"

for ROLE in roles/bigquery.jobUser roles/bigquery.dataViewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="${ROLE}" --quiet >/dev/null
done

if gcloud secrets describe gemini-api-key --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud secrets add-iam-policy-binding gemini-api-key \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="${PROJECT_ID}" --quiet >/dev/null
  SECRET_FLAG="--set-secrets=GEMINI_API_KEY=gemini-api-key:latest"
else
  SECRET_FLAG=""
fi

echo "==> Building container image ${IMAGE}"
gcloud builds submit --tag "${IMAGE}" --project="${PROJECT_ID}" .

echo "==> Deploying IAM-protected Governance Console (${SERVICE_NAME})"
gcloud run deploy "${SERVICE_NAME}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --service-account="${SA_EMAIL}" \
  --no-allow-unauthenticated \
  --set-env-vars="SEMLAYER_WAREHOUSE=bigquery,SEMLAYER_BQ_PROJECT=${PROJECT_ID},SEMLAYER_BQ_DATASET=${DATASET},SEMLAYER_LOG_JSON=1" \
  ${SECRET_FLAG}

echo "==> Upserting Cloud Run Canary Job (${CANARY_JOB})"
if gcloud run jobs describe "${CANARY_JOB}" --region="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  VERB="update"
else
  VERB="create"
fi
gcloud run jobs "${VERB}" "${CANARY_JOB}" \
  --image="${IMAGE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --service-account="${SA_EMAIL}" \
  --set-env-vars="SEMLAYER_WAREHOUSE=bigquery,SEMLAYER_BQ_PROJECT=${PROJECT_ID},SEMLAYER_BQ_DATASET=${DATASET},SEMLAYER_LOG_JSON=1" \
  --command="uv" \
  --args="run,--no-dev,semlayer,--log-json,canary"

echo "==> Done. Run canary manually anytime with:"
echo "    gcloud run jobs execute ${CANARY_JOB} --region=${REGION} --project=${PROJECT_ID}"
