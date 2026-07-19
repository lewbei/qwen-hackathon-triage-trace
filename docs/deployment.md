# Alibaba Cloud Deployment

## One-command local startup

```bash
cp .env.example .env
# Edit .env and add QWEN_API_KEY
docker compose up --build
```

## Automated ECS deployment

The repository includes Terraform Infrastructure-as-Code under `deploy/alibaba/` and a convenience wrapper at `scripts/deploy_alibaba_ecs.sh`.

```bash
export ALICLOUD_ACCESS_KEY=...
export ALICLOUD_SECRET_KEY=...

# Create deploy/alibaba/terraform.tfvars with your secrets
cat > deploy/alibaba/terraform.tfvars <<EOF
qwen_api_key = "your-dashscope-key"
ssh_cidr     = "YOUR.IP.ADDRESS/32"
EOF

./scripts/deploy_alibaba_ecs.sh
```

After `terraform apply` finishes, the public IP of the ECS instance is printed. Use it for the live URL in `README.md` and for the Devpost submission proof.

### What the Terraform stack creates

- VPC and VSwitch in the chosen region
- Security group exposing only port 80 publicly and SSH from your `/32`
- ECS instance running Ubuntu 22.04
- Elastic IP associated with the ECS instance
- Cloud-init script that installs Docker, clones the public repo, writes the environment file, and starts TriageTrace via `docker-compose.prod.yml`

On the ECS instance:

- `db` — `pgvector/pgvector:pg16` PostgreSQL container with a persistent named volume
- `api` — FastAPI backend container
- `ui` — nginx serving the React build and proxying `/api` to the backend

All three containers use `restart: unless-stopped`. The database survives an ECS reboot but not an instance deletion or disk replacement.

### Health verification

```bash
IP=$(terraform output -raw public_ip)

curl -f http://$IP/api/health

curl -f -X POST http://$IP/api/demo/accumulation
```

Run the accumulation demo at least three times, reboot ECS, and run it again.

## Notes

- Do not commit credentials, console IDs, or private hostnames.
- Redact deployment evidence before adding screenshots to the repository.
