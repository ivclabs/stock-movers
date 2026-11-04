#!/usr/bin/env bash
# deploy.sh - build the Lambda code and create/update everything in AWS
# ----------------------------------------------------------------------
#   ./deploy.sh            show what will change, ask "yes", then apply
#   ./deploy.sh destroy    delete EVERYTHING this project created in AWS
#
#   .env ──keys──▶ Terraform ──▶ AWS (S3, CloudFront, 2 Lambdas, 3 schedules)
#
# Needs: being logged in to AWS (`aws login`), and the .venv (for pip).
set -euo pipefail
cd "$(dirname "$0")"

# Pass the keys from .env to Terraform as TF_VAR_... variables (never printed)
set -a
source .env
set +a
export TF_VAR_apca_key_id="$APCA_KEY_ID"
export TF_VAR_apca_secret="$APCA_SECRET"
export TF_VAR_anthropic_api_key="$ANTHROPIC_API_KEY"
export TF_VAR_alert_email="$ALERT_EMAIL"

if [[ "${1:-}" == "destroy" ]]; then
    mkdir -p build/package      # Terraform wants the folder to exist, even to delete
    terraform -chdir=infra destroy
    exit
fi

./build_lambda.sh
terraform -chdir=infra init -upgrade -input=false >/dev/null
terraform -chdir=infra apply      # shows the plan and waits for you to type "yes"
