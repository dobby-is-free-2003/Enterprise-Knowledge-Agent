#!/usr/bin/env bash
# Deploy to Cloud Run from source. NOT TESTED against a live project (no GCP access when this was written).
# Free tier: Cloud Run has a monthly free allowance; Cloud Build and Artifact Registry have small free quotas. Check current pricing.
# Usage: PROJECT_ID=my-project REGION=asia-south1 ./deploy/cloudrun.sh
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-eka}"
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
# Session secret lives in Secret Manager, not in the image or environment file.
if ! gcloud secrets describe eka-session-secret >/dev/null 2>&1; then
  python3 -c 'import secrets; print(secrets.token_urlsafe(48), end="")' | gcloud secrets create eka-session-secret --data-file=-
fi
# Optional: GEMINI_API_KEY secret (create it yourself: gcloud secrets create eka-gemini-key --data-file=-)
SECRETS="SESSION_SECRET=eka-session-secret:latest"
if gcloud secrets describe eka-gemini-key >/dev/null 2>&1; then SECRETS="$SECRETS,GEMINI_API_KEY=eka-gemini-key:latest"; fi
gcloud run deploy "$SERVICE" --source . --region "$REGION" --allow-unauthenticated \
  --min-instances 0 --max-instances 2 --memory 512Mi --cpu 1 \
  --set-env-vars "DEMO_AUTH=true,EKA_MODE=auto" --set-secrets "$SECRETS"
echo "Demo login is ON (synthetic data only). For real users set DEMO_AUTH=false and configure Google sign-in (docs/SETUP_GOOGLE.md)."
