# Alibaba Cloud Deployment

This directory contains Infrastructure-as-Code for deploying TriageTrace on Alibaba Cloud (ECS + ApsaraDB RDS PostgreSQL with pgvector).

## Quick start

1. Install the Alibaba Cloud CLI (`aliyun`) and Terraform.
2. Configure credentials:
   ```bash
   export ALICLOUD_ACCESS_KEY=...
   export ALICLOUD_SECRET_KEY=...
   export ALICLOUD_REGION=cn-hongkong
   ```
3. Provide secrets in a `terraform.tfvars` file:
   ```hcl
   db_password  = "your-rds-password"
   qwen_api_key = "your-dashscope-key"
   ```
4. Deploy:
   ```bash
   terraform init
   terraform apply
   ```
5. After apply, Terraform prints the ECS public IP. Update your `.env` and README live URL with that IP.

## Notes

- This is a proof-of-deployment template. Adjust instance class, security-group rules, and SSH key for your account.
- The RDS instance must have `pgvector` enabled; Terraform creates a PostgreSQL 15 instance.
- `pgvector` is created automatically by `docker compose` using `pgvector/pgvector:pg16`. For RDS, run `CREATE EXTENSION IF NOT EXISTS vector;` after creation.
- The `cloud-init.sh` script installs Docker, clones the public repo, writes the environment file, and runs `docker compose up -d`.
