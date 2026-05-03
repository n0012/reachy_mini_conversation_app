#!/usr/bin/env bash
# One-time GCP setup for Reachy Mini: creates a service account with the
# minimum IAM roles needed for Gemini Live + Vertex AI Memory Bank.
#
# Usage:
#   export PROJECT_ID=my-gcp-project
#   export REASONING_ENGINE_ID=1234567890123456789
#   bash setup_gcp.sh
#
# Output: ./reachy-mini-sa-key.json  (paste into the setup wizard)

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID env var}"
REASONING_ENGINE_ID="${REASONING_ENGINE_ID:?Set REASONING_ENGINE_ID env var}"
SA_NAME="reachy-mini"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_FILE="./reachy-mini-sa-key.json"

echo "Project:  $PROJECT_ID"
echo "SA:       $SA_EMAIL"
echo "Engine:   $REASONING_ENGINE_ID"
echo ""

# Enable required APIs
echo "Enabling APIs..."
gcloud services enable aiplatform.googleapis.com --project="$PROJECT_ID"

# Create service account (ok if already exists)
echo "Creating service account..."
gcloud iam service-accounts create "$SA_NAME" \
  --display-name="Reachy Mini" \
  --project="$PROJECT_ID" 2>/dev/null || echo "  (already exists)"

# Grant roles
echo "Granting IAM roles..."

# Vertex AI User — required for Gemini Live API calls and Agent Engine memory ops
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/aiplatform.user" \
  --condition=None \
  --quiet

# Download key
echo "Downloading service account key to $KEY_FILE..."
gcloud iam service-accounts keys create "$KEY_FILE" \
  --iam-account="$SA_EMAIL" \
  --project="$PROJECT_ID"

echo ""
echo "Done. Paste the contents of $KEY_FILE into the Reachy Mini setup wizard."
echo ""
echo "  cat $KEY_FILE"
