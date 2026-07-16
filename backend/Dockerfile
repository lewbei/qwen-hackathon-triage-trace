FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml requirements.txt ./
COPY backend/ ./backend/

RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --no-deps -e .

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
