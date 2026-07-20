#cloud-config
package_update: true
packages:
  - git

runcmd:
  # Install Docker CE and Compose plugin in a distro-aware way.
  - |
    if command -v apt-get >/dev/null 2>&1; then
      apt-get update
      apt-get install -y ca-certificates curl gnupg
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
      chmod a+r /etc/apt/keyrings/docker.asc
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" >/etc/apt/sources.list.d/docker.list
      apt-get update
      apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    elif command -v dnf >/dev/null 2>&1; then
      dnf -y install dnf-plugins-core
      dnf config-manager --add-repo https://download.docker.com/linux/rhel/docker-ce.repo
      dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
  - systemctl enable --now docker
  - usermod -aG docker ubuntu || usermod -aG docker root || true
  - git clone https://github.com/lewbei/qwen-hackathon-triage-trace.git /opt/triagetrace
  - |
    cat > /opt/triagetrace/.env <<'EOF'
QWEN_API_KEY=${qwen_api_key}
QWEN_BASE_URL=https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1
DATABASE_URL=postgresql+asyncpg://${db_user}:${db_password}@db:5432/triagetrace
SYNC_DATABASE_URL=postgresql://${db_user}:${db_password}@db:5432/triagetrace
POSTGRES_USER=${db_user}
POSTGRES_PASSWORD=${db_password}
APP_ENV=demo
LOG_LEVEL=info
MEMORY_TOKEN_BUDGET=800
DEFAULT_TENANT=default
USE_LLM_POISON_CHECK=1
DEMO_SECRET=${demo_secret}
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
