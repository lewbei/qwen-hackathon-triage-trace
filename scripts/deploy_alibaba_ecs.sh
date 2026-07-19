#!/usr/bin/env bash
# Deploy TriageTrace to Alibaba Cloud ECS using the Terraform templates.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/../deploy/alibaba"

if [[ -z "${ALICLOUD_ACCESS_KEY:-}" || -z "${ALICLOUD_SECRET_KEY:-}" ]]; then
  echo "Error: set ALICLOUD_ACCESS_KEY and ALICLOUD_SECRET_KEY"
  exit 1
fi

if [[ ! -f "$DEPLOY_DIR/terraform.tfvars" ]]; then
  echo "Error: create $DEPLOY_DIR/terraform.tfvars with qwen_api_key and ssh_cidr"
  exit 1
fi

if ! grep -qE '^\s*ssh_cidr\s*=' "$DEPLOY_DIR/terraform.tfvars"; then
  echo "Error: set ssh_cidr in $DEPLOY_DIR/terraform.tfvars (use your IP/32)"
  exit 1
fi

if ! grep -qE '^\s*qwen_api_key\s*=' "$DEPLOY_DIR/terraform.tfvars"; then
  echo "Error: set qwen_api_key in $DEPLOY_DIR/terraform.tfvars"
  exit 1
fi

cd "$DEPLOY_DIR"
terraform init
terraform apply -auto-approve
