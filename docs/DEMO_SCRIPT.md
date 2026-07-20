# TriageTrace — Demo Script

> Use this script when presenting the project to judges. Estimated time: 3 minutes.

## Setup (10 s)

1. Open `http://localhost:5173` (local dev via `docker compose up`) or the public URL in `README.md`.
2. If the screen shows *"No incident memory loaded"*, click **Initialize production demo**.
3. The page refreshes with the cart-service incident, runbook, memory timeline, and the **Guided incident sequence** panel.

## Story (30 s)

> *"We are on-call for `cart-service`. Redis latency spiked, checkout failures are above 40/min, and the last deployment was rolled back. We have months of runbook history — some old, some newer, and some that an attacker tried to inject. TriageTrace's memory firewall will recall only the safe, current procedure."*

Point out:

- **Signals strip:** live metrics, log error, and deployment status.
- **Memory timeline:** older procedure is `superseded`, newer is `simulated_safe`, attacker instruction is `quarantined`.
- **Runbook:** explicitly forbids restarting the database.

## Action 1 — Run the guided incident sequence (60–90 s)

1. Click **Run both triage modes** in the header.
2. The **Guided incident sequence** panel becomes active and walks through 7 steps.
3. Step through each card and explain what is happening:

### 1. Run an incident

> *"The agent receives the alert and starts a governed triage run."*

Point at the incident details and the stateless baseline proposal (if it was generated).

### 2. Recall the correct memory

> *"The memory firewall retrieves the current approved procedure for `cart-service`."*

Show the **packed** memory card with the `simulated_safe` or `active` badge.

### 3. Block poisoned and stale memories

> *"Anything unsafe or out-of-date is blocked before it reaches the model."*

Show the **rejected** and **omitted** memory cards:

- `quarantined` or `superseded` memories are rejected with a reason.
- Memories that did not fit the token budget are omitted.

### 4. Send governed context to Qwen

> *"Only the approved memories and the incident details are placed in the prompt."*

Click **Show full governed context** to reveal the system and user prompts actually sent to Qwen.

### 5. Produce a recommendation

> *"Qwen reasons with the governed context and proposes one safe remediation."*

Show the reasoning tool calls and the final proposed action.

### 6. Require human approval

> *"The proposal is intentionally gated. No action is taken until a human approves it."*

Enter operator feedback and click **Approve**.

### 7. Display predictive simulation result

> *"The outcome is predicted, not executed, and the approved memory is stored for future runs."*

Show the before/after score, delta, and `Predicted to improve`/`Predicted to worsen` label.

> *"This is a real Qwen run. The agent is not reading a script — it is searching pgvector, invoking tools, and returning a structured proposal."*

## Action 2 — Compare stateless (optional, 60 s)

1. Scroll to the **Triage recommendation** panel.
2. Show that the stateless baseline may suggest a less-safe action because it has no memory of the current approved procedure.

## Closing (20 s)

> *"TriageTrace makes the agent safer by giving it memory that is grounded in operator-approved, simulator-tested procedures — and by quarantining anything that contradicts policy."*

## Proof points to mention

- The recalled memory ID, tool calls, simulation scores, and governed prompt are all returned by the backend, not hardcoded.
- The `quarantined` poison memory and `superseded` old memory remain in the database but are excluded from retrieval.
- All lifecycle decisions are enforced in `backend/app/memory.py`.
