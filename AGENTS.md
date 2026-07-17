# TriageTrace — Agent Notes

## Verification commands

- Unit / integration tests: `python -m pytest backend/tests -q`
- Docker smoke test: `docker compose up -d --build` then `curl http://localhost:5173/api/health`
- Docker demo smoke test: `curl -X POST http://localhost:5173/api/demo/winning-scenario`

## Working with the running API

The FastAPI container keeps an open SQLAlchemy / asyncpg connection while it is up. Running the test suite against the same Postgres instance can hang on `DELETE FROM memories` because of the open transaction. **Stop the `api` container before running tests:**

```bash
docker compose stop api
python -m pytest backend/tests -q
```

## Public network layout

- The `api` service is **not** published to the host (`ports:` removed). It is only reachable through the `ui` (nginx) proxy at `http://localhost:5173/api/`.
- `nginx` forwards `X-Forwarded-For`, `X-Real-IP`, and `X-Forwarded-Proto` so the in-memory rate limiter sees the real client IP.
- Direct port-8000 access to the API is blocked at the Docker Compose level.

## Public vs. privileged access

- Public callers are forced to the `DEFAULT_TENANT` (`default`) unless they provide the `X-Demo-Secret` header matching `DEMO_SECRET`.
- `POST /api/memories` always downgrades the request to an untrusted `external` `observation`; it cannot create a `procedure` or `policy` from the public API.
- `DELETE /api/memories/{id}` and `POST /api/demo/reset` always require `DEMO_SECRET`.
- The `demo/reset` endpoint no longer has a UI button; admins can call it with the secret via `curl`.

## Memory lifecycle rules

- Supersession is authority- **and** timestamp-aware: a record only supersedes an active memory when it has higher authority, or equal authority and a strictly newer `source_timestamp`.
- Out-of-order or equal-timestamp arrivals with equal authority are quarantined as `stale or out-of-order`.
