#!/usr/bin/env bash
set -euo pipefail

PROJECT=${PROJECT:-memory-glasses-2611}
REGION=${REGION:-us-west1}
R2_ACCOUNT_ID=${R2_ACCOUNT_ID:?set R2_ACCOUNT_ID}
R2_BUCKET=${R2_BUCKET:-memory-glasses}
SERVICE=memory-server
JOB=memory-worker
REPO=memory
IDENTITY="memory-server@${PROJECT}.iam.gserviceaccount.com"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/server:latest"
SECRETS=(OPENAI_API_KEY API_TOKEN DATABASE_URL R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)
SECRET_FLAGS=$(for NAME in "${SECRETS[@]}"; do printf '%s=%s:latest,' "$NAME" "$NAME"; done)
SECRET_FLAGS=${SECRET_FLAGS%,}
SETTINGS="R2_ACCOUNT_ID=${R2_ACCOUNT_ID},R2_BUCKET=${R2_BUCKET}"

cd "$(dirname "$0")/.."

echo "Turning on services"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudscheduler.googleapis.com \
  secretmanager.googleapis.com iam.googleapis.com --project "$PROJECT" >/dev/null

echo "Server identity and secret access"
if ! gcloud iam service-accounts describe "$IDENTITY" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create memory-server --display-name "Memory Glasses server" --project "$PROJECT" >/dev/null
fi

for NAME in "${SECRETS[@]}"; do
  gcloud secrets add-iam-policy-binding "$NAME" --member "serviceAccount:${IDENTITY}" \
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
gcloud run jobs deploy "$JOB" --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --service-account "$IDENTITY" --command python,-m,memory.worker \
  --cpu 1 --memory 2Gi --task-timeout 3600 --max-retries 0 \
  --set-env-vars "$SETTINGS" --set-secrets "$SECRET_FLAGS" >/dev/null

gcloud run jobs add-iam-policy-binding "$JOB" --region "$REGION" --project "$PROJECT" \
  --member "serviceAccount:${IDENTITY}" --role roles/run.developer >/dev/null

echo "Server"
gcloud run deploy "$SERVICE" --image "$IMAGE" --region "$REGION" --project "$PROJECT" \
  --service-account "$IDENTITY" --allow-unauthenticated \
  --cpu 1 --memory 1Gi --min-instances 0 --max-instances 2 --concurrency 20 --timeout 300 \
  --set-env-vars "${SETTINGS},WORKER_JOB=${JOB},WORKER_REGION=${REGION}" --set-secrets "$SECRET_FLAGS" >/dev/null

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format "value(status.url)")

echo "Checking for stopped glasses recordings every minute"
TOKEN=$(gcloud secrets versions access latest --secret API_TOKEN --project "$PROJECT")
SCHEDULE=(--location "$REGION" --project "$PROJECT" --schedule "* * * * *" --uri "${URL}/glasses/check"
          --http-method POST --attempt-deadline 60s)

if gcloud scheduler jobs describe glasses-check --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud scheduler jobs update http glasses-check "${SCHEDULE[@]}" --update-headers "Authorization=Bearer ${TOKEN}" >/dev/null
else
  gcloud scheduler jobs create http glasses-check "${SCHEDULE[@]}" --headers "Authorization=Bearer ${TOKEN}" >/dev/null
fi
unset TOKEN

echo "Deployed: ${URL}"
