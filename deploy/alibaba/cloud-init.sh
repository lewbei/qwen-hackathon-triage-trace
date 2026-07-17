#!/bin/bash
#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose
  - git

runcmd:
  - systemctl enable --now docker
  - usermod -aG docker ubuntu
  - mkdir -p /opt/triagetrace
  - |
    cat > /opt/triagetrace/.env <<EOF
QWEN_API_KEY=${qwen_api_key}
QWEN_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
DATABASE_URL=postgresql+asyncpg://${db_user}:${db_password}@${db_host}:5432/${db_name}
SYNC_DATABASE_URL=postgresql://${db_user}:${db_password}@${db_host}:5432/${db_name}
APP_ENV=production
LOG_LEVEL=info
MEMORY_TOKEN_BUDGET=800
DEFAULT_TENANT=default
EOF
  - cd /opt/triagetrace && git clone https://github.com/lewbei/qwen-hackathon-triage-trace.git .
  - cd /opt/triagetrace && docker compose up -d
