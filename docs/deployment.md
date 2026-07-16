# Alibaba Cloud Deployment

## One-command local startup

```bash
cp .env.example .env
# Edit .env and add QWEN_API_KEY
docker compose up --build
```

## ECS + ApsaraDB RDS

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
