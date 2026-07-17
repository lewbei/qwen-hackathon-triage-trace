# Alibaba Cloud Deployment

## One-command local startup

```bash
cp .env.example .env
# Edit .env and add QWEN_API_KEY
docker compose up --build
```

## Automated ECS + ApsaraDB RDS deployment

The repository includes Terraform Infrastructure-as-Code under `deploy/alibaba/` and a convenience wrapper at `scripts/deploy_alibaba_ecs.sh`.

```bash
export ALICLOUD_ACCESS_KEY=...
export ALICLOUD_SECRET_KEY=...

# Create deploy/alibaba/terraform.tfvars with your secrets
cat > deploy/alibaba/terraform.tfvars <<EOF
db_password  = "your-rds-password"
qwen_api_key = "your-dashscope-key"
EOF

./scripts/deploy_alibaba_ecs.sh
```

After `terraform apply` finishes, the public IP of the ECS instance is printed. Use it for the live URL in `README.md` and for the Devpost submission proof.

### What the Terraform stack creates

- VPC and VSwitch in the chosen region
- Security group exposing 22, 80, 443, 8000, and 5173
- ApsaraDB RDS for PostgreSQL 15 instance with pgvector support
- ECS instance running Ubuntu 22.04
- Elastic IP associated with the ECS instance
- Cloud-init script that installs Docker, clones the repo, writes the environment file, and starts TriageTrace via `docker compose up -d`

## Manual ECS + ApsaraDB RDS

1. Create an ECS instance in the same region as your RDS instance.
2. Install Docker and Docker Compose on ECS.
3. Create an ApsaraDB RDS for PostgreSQL instance with pgvector enabled:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```
4. Update `.env` with the RDS endpoint and Qwen Cloud API key.
5. Copy the project to ECS and run:
   ```bash
   docker compose up -d
   ```

## Fallback (ECS PostgreSQL)

If RDS is not operational, the same `pgvector/pgvector:pg16` image runs on ECS via `docker-compose.yml`.

## Notes

- Do not commit credentials, console IDs, or private hostnames.
- Redact deployment evidence before adding screenshots to the repository.
