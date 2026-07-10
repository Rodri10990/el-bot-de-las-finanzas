#!/bin/bash
echo "=========================================================="
echo "   Research & Advisory Bot - GCP Deployment Script"
echo "=========================================================="

PROJECT_ID=$(gcloud config get-value project)
REGION="us-central1"
BUCKET_NAME="${PROJECT_ID}-trading-portfolio"
FUNCTION_NAME="research-adviser"

echo "[INFO] Extracting credentials from active paper-trading-bot..."
GEMINI_API_KEY=$(gcloud functions describe paper-trading-bot --gen2 --region="$REGION" --format="value(serviceConfig.environmentVariables.GEMINI_API_KEY)" 2>/dev/null)
TELEGRAM_BOT_TOKEN=$(gcloud functions describe paper-trading-bot --gen2 --region="$REGION" --format="value(serviceConfig.environmentVariables.TELEGRAM_BOT_TOKEN)" 2>/dev/null)
TELEGRAM_CHAT_ID=$(gcloud functions describe paper-trading-bot --gen2 --region="$REGION" --format="value(serviceConfig.environmentVariables.TELEGRAM_CHAT_ID)" 2>/dev/null)

if [ -z "$GEMINI_API_KEY" ] || [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "[WARNING] Could not automatically extract credentials from paper-trading-bot."
    read -sp "Enter your Google AI Studio API Key: " GEMINI_API_KEY
    echo ""
    read -p "Enter your Telegram Bot Token: " TELEGRAM_BOT_TOKEN
    read -p "Enter your Telegram Chat ID: " TELEGRAM_CHAT_ID
fi

echo "Deploying weekly Research & Advisory Bot (research-adviser)..."

gcloud functions deploy "$FUNCTION_NAME" \
  --gen2 \
  --runtime=python310 \
  --region="$REGION" \
  --trigger-http \
  --entry-point=handle_research_cycle \
  --memory=256Mi \
  --cpu=1 \
  --timeout=180 \
  --set-env-vars="GEMINI_API_KEY=$GEMINI_API_KEY,BUCKET_NAME=$BUCKET_NAME,TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN,TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID" \
  --no-allow-unauthenticated
