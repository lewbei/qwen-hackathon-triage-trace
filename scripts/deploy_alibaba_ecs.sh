#!/usr/bin/env bash
# Deploy TriageTrace to Alibaba Cloud ECS + RDS using the Terraform templates.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/../deploy/alibaba"

if [[ -z "${ALICLOUD_ACCESS_KEY:-}" || -z "${ALICLOUD_SECRET_KEY:-}" ]]; then
  echo "Error: set ALICLOUD_ACCESS_KEY and ALICLOUD_SECRET_KEY"
  exit 1
fi

if [[ ! -f "$DEPLOY_DIR/terraform.tfvars" ]]; then
  echo "Error: create $DEPLOY_DIR/terraform.tfvars with db_password and qwen_api_key"
  exit 1
fi

cd "$DEPLOY_DIR"
terraform init
terraform apply -auto-approve
