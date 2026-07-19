# Alibaba Cloud Deployment

This directory contains Infrastructure-as-Code for deploying TriageTrace on a single Alibaba Cloud ECS instance. The instance runs the React UI, FastAPI backend, and a local `pgvector/pgvector:pg16` PostgreSQL container with a persistent Docker volume.

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
- The public UI is served on port 80 and the API is proxied through `/api`. Ports 8000, 5173, and 5432 are not exposed externally.
- SSH is allowed only from the CIDR you set in `ssh_cidr`.
- The pgvector extension is pre-installed in the `pgvector/pgvector:pg16` image; Alembic migrations create the extension in the application database.
- `cloud-init.sh` installs Docker, clones the public repo, writes the environment file, starts `docker-compose.prod.yml`, and verifies `/api/health` before completing.
- Containers use `restart: unless-stopped` so the demo survives an ECS reboot. The database is persisted in a named Docker volume, so data survives a normal reboot but not an instance deletion or disk replacement.
