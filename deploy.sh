#!/bin/bash

# Cloud Trading Bot Deployment Script
# Follow these commands to deploy your bot 24/7 on Google Cloud Platform!

# Terminate on error
set -e

echo "=========================================================="
echo "   Paper Trading Bot - GCP Serverless Deployment Guide"
echo "=========================================================="

# 1. Configuration variables
PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
BUCKET_NAME="${PROJECT_ID}-trading-portfolio"
FUNCTION_NAME="paper-trading-bot"
SCHEDULER_JOB_NAME="daily-trading-cycle-trigger"

echo "Using active GCP Project ID: $PROJECT_ID"
echo "Target Deployment Region:   $REGION"
echo "Target Cloud Storage Bucket: gs://$BUCKET_NAME"
echo ""

# Read Google AI Studio API Key and Telegram credentials
echo "[INFO] Retrieving credentials..."
EXISTING_GEMINI_API_KEY=$(gcloud functions describe "$FUNCTION_NAME" --gen2 --region="$REGION" --format="value(serviceConfig.environmentVariables.GEMINI_API_KEY)" 2>/dev/null || true)
EXISTING_TELEGRAM_BOT_TOKEN=$(gcloud functions describe "$FUNCTION_NAME" --gen2 --region="$REGION" --format="value(serviceConfig.environmentVariables.TELEGRAM_BOT_TOKEN)" 2>/dev/null || true)
EXISTING_TELEGRAM_CHAT_ID=$(gcloud functions describe "$FUNCTION_NAME" --gen2 --region="$REGION" --format="value(serviceConfig.environmentVariables.TELEGRAM_CHAT_ID)" 2>/dev/null || true)

GEMINI_API_KEY=$EXISTING_GEMINI_API_KEY
if [ -z "$GEMINI_API_KEY" ]; then
    read -sp "Enter your Google AI Studio API Key: " GEMINI_API_KEY
    echo ""
fi

TELEGRAM_BOT_TOKEN=$EXISTING_TELEGRAM_BOT_TOKEN
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    read -p "Enter your Telegram Bot Token: " TELEGRAM_BOT_TOKEN
fi

TELEGRAM_CHAT_ID=$EXISTING_TELEGRAM_CHAT_ID
if [ -z "$TELEGRAM_CHAT_ID" ]; then
    read -p "Enter your Telegram Chat ID: " TELEGRAM_CHAT_ID
fi

# 2. Enable Required APIs
echo "[INFO] Enabling necessary Google Cloud APIs..."
gcloud services enable \
    cloudfunctions.googleapis.com \
    cloudbuild.googleapis.com \
    cloudscheduler.googleapis.com \
    storage-api.googleapis.com \
    run.googleapis.com \
    artifactregistry.googleapis.com


# 3. Create Cloud Storage Bucket
echo "[INFO] Creating Google Cloud Storage Bucket to host portfolio database..."
if gsutil ls -b "gs://$BUCKET_NAME" >/dev/null 2>&1; then
    echo "Bucket gs://$BUCKET_NAME already exists."
else
    gsutil mb -l "$REGION" "gs://$BUCKET_NAME"
    echo "Bucket created successfully."
fi

# Initialize portfolio.json on the bucket if not already there
echo "[INFO] Initializing portfolio.json on Cloud Storage..."
cat <<EOF > temp_portfolio.json
{
  "cash": 10000.0,
  "holdings": {
    "BTC-USD": 0.0,
    "ETH-USD": 0.0,
    "TSLA": 0.0,
    "NVDA": 0.0
  },
  "history": []
}
EOF

# Copy file to GCS if not present
if gsutil ls "gs://$BUCKET_NAME/portfolio.json" >/dev/null 2>&1; then
    echo "Active portfolio.json already exists on gs://$BUCKET_NAME. Skipping initialization."
else
    gsutil cp temp_portfolio.json "gs://$BUCKET_NAME/portfolio.json"
    echo "portfolio.json initialized on GCS."
fi
rm temp_portfolio.json

# 4. Deploy Cloud Function (2nd Gen for better performance and resource scaling)
echo "[INFO] Deploying Cloud Function to GCP (using Python runtime)..."
gcloud functions deploy "$FUNCTION_NAME" \
    --gen2 \
    --runtime=python310 \
    --region="$REGION" \
    --trigger-http \
    --entry-point=handle_trading_cycle \
    --memory=256Mi \
    --cpu=1 \
    --timeout=180 \
    --set-env-vars="GEMINI_API_KEY=$GEMINI_API_KEY,BUCKET_NAME=$BUCKET_NAME,TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN,TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID" \
    --set-secrets="ALPACA_API_KEY_ID=alpaca-key-id:latest,ALPACA_SECRET_KEY=alpaca-secret-key:latest" \
    --no-allow-unauthenticated

# Retrieve Function URL
FUNCTION_URL=$(gcloud functions describe "$FUNCTION_NAME" --gen2 --region="$REGION" --format="value(serviceConfig.uri)")
echo "[SUCCESS] Cloud Function successfully deployed at URL: $FUNCTION_URL"
echo ""

# 5. Create Service Account and Cloud Scheduler Trigger (Secure Authentication)
echo "[INFO] Setting up daily Cloud Scheduler trigger..."
SERVICE_ACCOUNT_NAME="trading-bot-invoker"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Create service account if not exists
if gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" >/dev/null 2>&1; then
    echo "Service account $SERVICE_ACCOUNT_EMAIL already exists."
else
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --description="Service account to trigger the paper trading Cloud Function" \
        --display-name="Trading Bot Invoker"
    echo "Service account created successfully."
fi

# Grant Invoker permissions to the service account
gcloud run services add-iam-policy-binding "$FUNCTION_NAME" \
    --region="$REGION" \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/run.invoker"

# Create Cloud Scheduler job triggering the Cloud Function daily
if gcloud scheduler jobs describe "$SCHEDULER_JOB_NAME" --location="$REGION" >/dev/null 2>&1; then
    echo "Cloud Scheduler job $SCHEDULER_JOB_NAME already exists. Updating..."
    gcloud scheduler jobs update http "$SCHEDULER_JOB_NAME" \
        --location="$REGION" \
        --schedule="0 15 * * *" \
        --uri="$FUNCTION_URL" \
        --http-method=POST \
        --oidc-service-account-email="$SERVICE_ACCOUNT_EMAIL"
else
    gcloud scheduler jobs create http "$SCHEDULER_JOB_NAME" \
        --location="$REGION" \
        --schedule="0 15 * * *" \
        --uri="$FUNCTION_URL" \
        --http-method=POST \
        --oidc-service-account-email="$SERVICE_ACCOUNT_EMAIL"
    echo "Daily Cloud Scheduler trigger created."
fi

echo ""
echo "=========================================================="
echo "🎉 DEPLOYMENT COMPLETE!"
echo "Your paper trading bot is now running 24/7 in the cloud."
echo "It will run daily at 15:00 UTC."
echo "You can check GCS gs://$BUCKET_NAME to see portfolio.json and history.csv."
echo "=========================================================="
