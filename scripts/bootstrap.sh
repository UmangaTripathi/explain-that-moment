#!/usr/bin/env bash
# One-time setup: provisions Azure, wires GitHub Actions to Azure via OIDC,
# and stores the repo variables/secrets the workflows need.
#
# Prerequisites:
#   az login            (as YOUR user account, with Owner on the subscription)
#   gh auth login
#   GitHub repo already created (gh repo create)
#
# Usage:  bash scripts/bootstrap.sh          (YES=1 skips the confirmation prompts)
# Safe to re-run: every step is idempotent.

set -euo pipefail
export MSYS_NO_PATHCONV=1   # stop Git Bash on Windows from mangling /subscriptions/... paths

RG="${RG:-rg-explain-moment}"
LOCATION="${LOCATION:-eastus2}"
REPO="${REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"
APP_REG_NAME="gh-explain-that-moment"

clean() { tr -d '\r'; }   # az on Windows can emit CRLF
confirm() { [[ "${YES:-}" == "1" ]] && return 0; read -r -p "$1 [y/N] " ok; [[ "$ok" == "y" ]]; }

SUB_ID=$(az account show --query id -o tsv | clean)
TENANT_ID=$(az account show --query tenantId -o tsv | clean)
USER_TYPE=$(az account show --query user.type -o tsv | clean)
BUDGET_EMAIL="${BUDGET_EMAIL:-$(az account show --query user.name -o tsv | clean)}"

if [[ "$USER_TYPE" != "user" ]]; then
  echo "ERROR: az is logged in as a $USER_TYPE. Run 'az login' with your own account first." >&2
  exit 1
fi

echo "Subscription: $SUB_ID"
echo "Repo:         $REPO"
echo "Resource grp: $RG ($LOCATION)"
confirm "Continue?" || exit 1

echo "==> Registering resource providers"
for p in Microsoft.App Microsoft.CognitiveServices Microsoft.ContainerRegistry \
         Microsoft.OperationalInsights Microsoft.Web Microsoft.ManagedIdentity Microsoft.Consumption; do
  az provider register -n "$p" --wait -o none
done

echo "==> Creating resource group"
az group create -n "$RG" -l "$LOCATION" --tags project=explain-that-moment -o none

echo "==> Preview of infrastructure changes"
az deployment group what-if -g "$RG" -f infra/main.bicep -p budgetEmail="$BUDGET_EMAIL"
confirm "Apply these changes?" || exit 1

echo "==> Deploying infrastructure (5-10 min)"
az deployment group create -g "$RG" -n main -f infra/main.bicep \
  -p budgetEmail="$BUDGET_EMAIL" --query properties.outputs -o json > infra/outputs.json

out() { python -c "import json,sys; print(json.load(open('infra/outputs.json'))['$1']['value'])"; }
ACR_NAME=$(out acrName)
BACKEND_APP=$(out backendAppName)
BACKEND_URL=$(out backendUrl)
FRONTEND_NAME=$(out frontendName)
FRONTEND_URL=$(out frontendUrl)

echo "==> Creating GitHub OIDC identity ($APP_REG_NAME)"
APP_ID=$(az ad app list --display-name "$APP_REG_NAME" --query "[0].appId" -o tsv | clean)
if [[ -z "$APP_ID" ]]; then
  APP_ID=$(az ad app create --display-name "$APP_REG_NAME" --query appId -o tsv | clean)
fi
az ad sp show --id "$APP_ID" -o none 2>/dev/null || az ad sp create --id "$APP_ID" -o none
SP_ID=$(az ad sp show --id "$APP_ID" --query id -o tsv | clean)

# Jobs run in the "production" environment, so the OIDC subject is environment-scoped.
SUBJECT="repo:${REPO}:environment:production"
if ! az ad app federated-credential list --id "$APP_ID" --query "[].subject" -o tsv | clean | grep -qx "$SUBJECT"; then
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"github-production\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" -o none
fi

echo "==> Granting the pipeline access (scoped to $RG only)"
RG_SCOPE="/subscriptions/$SUB_ID/resourceGroups/$RG"
ACR_ID=$(az acr show -n "$ACR_NAME" --query id -o tsv | clean)
assign() {
  az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
    --role "$1" --scope "$2" -o none
}
assign "Contributor" "$RG_SCOPE"
assign "AcrPush" "$ACR_ID"
assign "Role Based Access Control Administrator" "$RG_SCOPE"   # lets infra.yml apply Bicep role assignments

echo "==> Configuring GitHub repo"
gh api -X PUT "repos/$REPO/environments/production" > /dev/null
gh variable set AZURE_CLIENT_ID       --repo "$REPO" --body "$APP_ID"
gh variable set AZURE_TENANT_ID       --repo "$REPO" --body "$TENANT_ID"
gh variable set AZURE_SUBSCRIPTION_ID --repo "$REPO" --body "$SUB_ID"
gh variable set AZURE_RG              --repo "$REPO" --body "$RG"
gh variable set ACR_NAME              --repo "$REPO" --body "$ACR_NAME"
gh variable set BACKEND_APP           --repo "$REPO" --body "$BACKEND_APP"
gh variable set BACKEND_URL           --repo "$REPO" --body "$BACKEND_URL"
az staticwebapp secrets list -n "$FRONTEND_NAME" -g "$RG" --query properties.apiKey -o tsv | clean \
  | gh secret set SWA_DEPLOYMENT_TOKEN --repo "$REPO"
printf '%s' "$BUDGET_EMAIL" | gh secret set BUDGET_EMAIL --repo "$REPO"

echo "==> Granting YOU model access for local development"
ME=$(az ad signed-in-user show --query id -o tsv | clean)
FOUNDRY_ID=$(az cognitiveservices account show -g "$RG" -n "$(out foundryName)" --query id -o tsv | clean)
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Cognitive Services OpenAI User" --scope "$FOUNDRY_ID" -o none
az role assignment create --assignee-object-id "$ME" --assignee-principal-type User \
  --role "Azure AI User" --scope "$FOUNDRY_ID" -o none

cat > backend/.env <<EOF
AZURE_OPENAI_ENDPOINT=$(out openAiEndpoint)
FOUNDRY_PROJECT_ENDPOINT=$(out foundryProjectEndpoint)
MAIN_MODEL_DEPLOYMENT=$(out mainModelDeployment)
MINI_MODEL_DEPLOYMENT=$(out miniModelDeployment)
ALLOWED_ORIGINS=http://localhost:5173
EOF

echo
echo "Done."
echo "  Backend:  $BACKEND_URL"
echo "  Frontend: $FRONTEND_URL"
echo "  Local config written to backend/.env"
echo "Push to main to trigger the first deployment."
