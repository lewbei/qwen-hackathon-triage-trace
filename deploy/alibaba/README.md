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
   ssh_cidr     = "YOUR.IP.ADDRESS/32"
   ```
4. Deploy:
   ```bash
   terraform init
   terraform apply
   ```
5. After apply, Terraform prints the ECS public IP. Verify the deployment:
   ```bash
   curl -f http://$(terraform output -raw public_ip)/api/health

   curl -f -X POST \
     http://$(terraform output -raw public_ip)/api/demo/accumulation
   ```

## Notes

- This is a proof-of-deployment template. Adjust instance class, security-group rules, and SSH key for your account.
- The public UI is served on port 80 and the API is proxied through `/api`. Ports 8000 and 5173 are not exposed externally.
- SSH is allowed only from the CIDR you set in `ssh_cidr`.
- The RDS instance must have `pgvector` enabled; Terraform creates a PostgreSQL 15 instance.
- `cloud-init.sh` waits for RDS, installs the `vector` extension, starts `docker-compose.prod.yml`, and verifies `/api/health` before completing.
- Containers have `restart: unless-stopped` so the demo survives an ECS reboot.
