# TriageTrace — Demo Script

> Use this script when presenting the project to judges. Estimated time: 3 minutes.

## Setup (10 s)

1. Open `http://localhost`.
2. If the screen shows *"No incident memory loaded"*, click **Initialize production demo**.
3. The page refreshes with the cart-service incident, runbook, and memory timeline.

## Story (30 s)

> *"We are on-call for `cart-service`. Redis latency spiked, checkout failures are above 40/min, and the last deployment was rolled back. We have months of runbook history — some old, some newer, and some that an attacker tried to inject. TriageTrace's memory firewall will recall only the safe, current procedure."*

Point out:

- **Signals strip:** live metrics, log error, and deployment status.
- **Memory timeline:** older procedure is `superseded`, newer is `simulated_safe`, attacker instruction is `quarantined`.
- **Runbook:** explicitly forbids restarting the database.

## Action 1 — Run triage (60–90 s)

1. Click **Run triage** in the header.
2. Wait for the `Triage recommendation` panel to update.
3. Show:
   - **Memory mode** card: proposes `Scale the Redis cache and restart the cart workers`.
   - **Memory recalled** badge and the safe memory it pulled from the firewall.
   - **Memory pack** progress bar: only the safe memory was packed; the poison and stale memory were rejected/omitted.
   - **Triage trace:** the evidence tools the agent called (metrics, runbook, deployments).

> *"This is a real Qwen run. The agent is not reading a script — it is searching pgvector, invoking tools, and returning a structured proposal."*

## Action 2 — Compare stateless (optional, 60 s)

1. Click **Compare stateless**.
2. Show that the stateless baseline may suggest a less-safe action because it has no memory of the current approved procedure.

## Action 3 — Approve (30 s)

1. Click **Approve**.
2. The **Simulation outcome** panel appears with:
   - before/after score;
   - delta;
   - `Predicted to improve` label.
3. A new `simulated_safe` memory is written and appears in the **Memory timeline** and **Memory lens**.

## Closing (20 s)

> *"TriageTrace makes the agent safer by giving it memory that is grounded in operator-approved, simulator-tested procedures — and by quarantining anything that contradicts policy."*

## Proof points to mention

- The recalled memory ID, tool calls, and simulation scores are all returned by the backend, not hardcoded.
- The `quarantined` poison memory and `superseded` old memory remain in the database but are excluded from retrieval.
- All lifecycle decisions are enforced in `backend/app/memory.py`.
