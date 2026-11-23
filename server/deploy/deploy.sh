#!/usr/bin/env bash
set -euo pipefail

PROJECT=${PROJECT:-memory-glasses-2611}
REGION=${REGION:-us-west1}
R2_ACCOUNT_ID=${R2_ACCOUNT_ID:?set R2_ACCOUNT_ID}
R2_BUCKET=${R2_BUCKET:-memory-glasses}
TIME_ZONE=${TIME_ZONE:-America/Denver}
SERVER_SERVICE=memory-server
WORKER_JOB_NAME=memory-worker
REPO=memory
SERVICE_ACCOUNT="memory-server@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/server:latest"
SECRETS=(OPENAI_API_KEY API_TOKEN DATABASE_URL R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)
SECRET_FLAGS=$(for NAME in "${SECRETS[@]}"; do printf '%s=%s:latest,' "$NAME" "$NAME"; done)
SECRET_FLAGS=${SECRET_FLAGS%,}
ENV_VARS="R2_ACCOUNT_ID=${R2_ACCOUNT_ID},R2_BUCKET=${R2_BUCKET},TZ=${TIME_ZONE}"

cd "$(dirname "$0")/.."

echo "Turning on services"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iam.googleapis.com --project "$PROJECT" >/dev/null

echo "Server identity and secret access"
if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create memory-server --display-name "Memory Glasses server" --project "$PROJECT" >/dev/null
fi

for NAME in "${SECRETS[@]}"; do
  gcloud secrets add-iam-policy-binding "$NAME" --member "serviceAccount:${SERVICE_ACCOUNT}" \
    --role roles/secretmanager.secretAccessor --project "$PROJECT" >/dev/null
done

echo "Image repository, keeping only the newest image"
if ! gcloud artifacts repositories describe "$REPO" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION" --project "$PROJECT" >/dev/null
fi

POLICY=$(mktemp)
cat > "$POLICY" <<'JSON'
[
  {"name": "keep-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 1}},
  {"name": "delete-older", "action": {"type": "Delete"}, "condition": {"tagState": "any", "olderThan": "1d"}}
]
JSON
gcloud artifacts repositories set-cleanup-policies "$REPO" --location "$REGION" --policy "$POLICY" --no-dry-run \
  --project "$PROJECT" >/dev/null
rm -f "$POLICY"

echo "Building and pushing the image"
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null 2>&1
docker buildx build --platform linux/amd64 -t "$IMAGE" --push .

echo "Worker job"
gcloud run jobs deploy "$WORKER_JOB_NAME" --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --service-account "$SERVICE_ACCOUNT" --command python,-m,memory.worker \
  --cpu 1 --memory 2Gi --task-timeout 3600 --max-retries 0 \
  --set-env-vars "$ENV_VARS" --set-secrets "$SECRET_FLAGS" >/dev/null

gcloud run jobs add-iam-policy-binding "$WORKER_JOB_NAME" --region "$REGION" --project "$PROJECT" \
  --member "serviceAccount:${SERVICE_ACCOUNT}" --role roles/run.developer >/dev/null

echo "Server"
gcloud run deploy "$SERVER_SERVICE" --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --service-account "$SERVICE_ACCOUNT" --allow-unauthenticated \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 4 --concurrency 20 --timeout 300 \
  --set-env-vars "${ENV_VARS},WORKER_JOB=${WORKER_JOB_NAME},WORKER_REGION=${REGION}" --set-secrets "$SECRET_FLAGS" >/dev/null

URL=$(gcloud run services describe "$SERVER_SERVICE" --region "$REGION" --project "$PROJECT" --format "value(status.url)")

echo "Deployed: ${URL}"
