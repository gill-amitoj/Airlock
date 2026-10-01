#!/usr/bin/env bash
# Deploy the engine to Azure using infra/main.bicep.
#
# Prerequisites: `az login`, and the airlock-api / airlock-worker images
# published (public) on GHCR by the CI workflow.
#
# The admin key for creating/running workflows is in infra/secrets.local.
#
# Usage:
#   ./infra/deploy.sh                 # deploys the `latest` images
#   IMAGE_TAG=<git-sha> ./infra/deploy.sh
#   LOCATION=francecentral ./infra/deploy.sh

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-airlock-rg}"
LOCATION="${LOCATION:-canadacentral}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
SECRETS_FILE="$(dirname "$0")/secrets.local"

# Generated once and reused, so redeploys keep the same database password.
# Hex only: the password is embedded in a connection URL.
if [[ ! -f "$SECRETS_FILE" ]]; then
  echo "Generating secrets in $SECRETS_FILE (git-ignored)"
  umask 077
  cat > "$SECRETS_FILE" <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -hex 32)
API_KEY=$(openssl rand -hex 32)
EOF
fi
# Secrets files from before the admin key existed get one appended.
if ! grep -q '^API_KEY=' "$SECRETS_FILE"; then
  echo "API_KEY=$(openssl rand -hex 32)" >> "$SECRETS_FILE"
fi
# shellcheck source=/dev/null
source "$SECRETS_FILE"

echo "Registering resource providers (first run only takes a minute)"
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforPostgreSQL; do
  az provider register --namespace "$ns" --wait --output none
done

echo "Creating resource group $RESOURCE_GROUP in $LOCATION"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none

echo "Deploying (PostgreSQL creation takes 5-10 minutes)"
az deployment group create \
  --resource-group "$RESOURCE_GROUP" \
  --template-file "$(dirname "$0")/main.bicep" \
  --parameters imageTag="$IMAGE_TAG" \
               postgresPassword="$POSTGRES_PASSWORD" \
               secretKey="$SECRET_KEY" \
               apiKey="$API_KEY" \
  --query properties.outputs.url.value \
  --output tsv
