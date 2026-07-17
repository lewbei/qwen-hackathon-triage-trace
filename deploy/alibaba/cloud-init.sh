#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose-plugin
  - git
  - postgresql-client

runcmd:
  - systemctl enable --now docker
  - usermod -aG docker ubuntu
  - git clone https://github.com/lewbei/qwen-hackathon-triage-trace.git /opt/triagetrace
  - |
    cat > /opt/triagetrace/.env <<'EOF'
QWEN_API_KEY=${qwen_api_key}
QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
DATABASE_URL=postgresql+asyncpg://${db_user}:${db_password}@${db_host}:5432/${db_name}
SYNC_DATABASE_URL=postgresql://${db_user}:${db_password}@${db_host}:5432/${db_name}
APP_ENV=demo
LOG_LEVEL=info
MEMORY_TOKEN_BUDGET=800
DEFAULT_TENANT=default
EOF
  - |
    # Wait for RDS PostgreSQL to accept connections.
    for i in $(seq 1 60); do
      if pg_isready -h ${db_host} -p 5432 >/dev/null 2>&1; then
        break
      fi
      if [ "$i" -eq 60 ]; then
        echo "RDS did not become reachable" >&2
        exit 1
      fi
      sleep 5
    done
  - |
    # Ensure the pgvector extension is installed before the API container starts.
    PGPASSWORD='${db_password}' psql \
      -h ${db_host} -U ${db_user} -d ${db_name} \
      -c "CREATE EXTENSION IF NOT EXISTS vector;"
  - cd /opt/triagetrace && docker compose -f docker-compose.prod.yml up -d --build
  - |
    # Verify the application is healthy through the public nginx proxy.
    for i in $(seq 1 30); do
      if curl -sf http://localhost/api/health >/dev/null 2>&1; then
        echo "TriageTrace is healthy"
        exit 0
      fi
      if [ "$i" -eq 30 ]; then
        echo "Health check failed" >&2
        cd /opt/triagetrace && docker compose -f docker-compose.prod.yml logs api
        exit 1
      fi
      sleep 5
    done
