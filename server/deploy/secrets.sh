#!/usr/bin/env bash
set -euo pipefail

PROJECT=${PROJECT:-memory-glasses-2611}
NAMES=(OPENAI_API_KEY API_TOKEN DATABASE_URL R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)

cd "$(dirname "$0")/.."
gcloud services enable secretmanager.googleapis.com --project "$PROJECT" >/dev/null

for NAME in "${NAMES[@]}"; do
  VALUE=$(grep -E "^${NAME}=" .env | head -1 | cut -d= -f2- | sed -E "s/^[\"']//; s/[\"']\$//" || true)

  if [ -z "$VALUE" ]; then
    echo "server/.env has no $NAME"
    exit 1
  fi

  if ! gcloud secrets describe "$NAME" --project "$PROJECT" >/dev/null 2>&1; then
    gcloud secrets create "$NAME" --replication-policy=automatic --project "$PROJECT" >/dev/null
  fi

  OLD=$(gcloud secrets versions list "$NAME" --filter="state=ENABLED" --format="value(name)" --project "$PROJECT")
  printf '%s' "$VALUE" | gcloud secrets versions add "$NAME" --data-file=- --project "$PROJECT" >/dev/null

  for VERSION in $OLD; do
    gcloud secrets versions destroy "$VERSION" --secret "$NAME" --project "$PROJECT" --quiet >/dev/null
  done

  echo "saved $NAME"
done
