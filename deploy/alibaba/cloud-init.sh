#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose-plugin
  - git

runcmd:
  - systemctl enable --now docker
  - usermod -aG docker ubuntu
  - git clone https://github.com/lewbei/qwen-hackathon-triage-trace.git /opt/triagetrace
  - |
    cat > /opt/triagetrace/.env <<'EOF'
QWEN_API_KEY=${qwen_api_key}
QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
DATABASE_URL=postgresql+asyncpg://${db_user}:${db_password}@db:5432/triagetrace
SYNC_DATABASE_URL=postgresql://${db_user}:${db_password}@db:5432/triagetrace
POSTGRES_USER=${db_user}
POSTGRES_PASSWORD=${db_password}
APP_ENV=demo
LOG_LEVEL=info
MEMORY_TOKEN_BUDGET=800
DEFAULT_TENANT=default
EOF
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
