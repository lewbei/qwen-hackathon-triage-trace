# Frontend Instructions

These instructions apply to files under `frontend/`.

## Stack

- Vite + React 18 + TypeScript
- Tailwind CSS for styling
- No additional UI libraries; keep dependencies minimal
- The production build is served by the `ui` container (nginx) and API calls are proxied through `/api/`

## Build and verify

```bash
cd frontend
npm ci
npm run build
```

After a Docker build:

```bash
docker compose up -d --build
curl -s http://localhost:5173/api/health
```

## API access

- Use relative URLs (`/api/...`) so the nginx proxy routes them to the backend.
- Do not hardcode `http://localhost:8000` or expose internal ports.
- Do not put local filesystem paths in query strings or browser-visible URLs.
- Inspect response bodies; HTTP 200 from the backend can still mean the operation was rejected (e.g. `stale` or `index_unavailable`).

## Components

- Keep components focused. Prefer composition over deep prop drilling.
- Re-use shared TypeScript interfaces by extracting them into a `types.ts` file only when more than one component needs them.
- Use explicit typed `useState` hooks. Avoid `any` for new code.
- Loading, error, and empty states should be visible.
- Avoid emojis unless explicitly requested.

## State management

- `useState` and `useEffect` are sufficient for this demo UI.
- For multi-step flows, maintain a clear state machine and disable destructive actions while loading.

## Demo UI guidelines

- The judge-facing demo should tell one coherent story per run, not scatter attention across many unrelated controls.
- Show PASS/FAIL derived from actual backend state, not assumptions.
- Display memory lifecycle states (active, simulated_safe, superseded, quarantined) with consistent status badges.
- Show both the stateless and memory-mode proposal side by side so the difference is obvious.
