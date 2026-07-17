# Backend Instructions

These instructions apply to files under `backend/`.

## Memory lifecycle

Memory conflict handling must consider:

* tenant;
* scope;
* subject;
* predicate;
* source authority;
* source timestamp;
* validity and expiration;
* provenance;
* current status.

Insertion order is not temporal order.

For conflicting memories:

* lower authority must not supersede higher authority;
* at equal authority, only a strictly newer source timestamp may supersede the current record;
* an older or equal timestamp arriving later must remain non-recallable;
* status and lineage changes must be atomic;
* lifecycle decisions must use one shared implementation.

Do not manually force statuses in demos or tests to manufacture the desired result.

## Trust and tenancy

Trusted provenance may only be assigned by internal workflows.

Public callers must not control:

* tenant identity;
* trusted provenance;
* source authority;
* internal status;
* supersession links;
* validation state.

All reads, writes, deletes, decisions, lineage requests, and run lookups must enforce tenant isolation.

Treat public endpoint input as hostile.

## Retrieval

Retrieval must exclude:

* quarantined;
* superseded;
* expired;
* deleted;
* future-dated;
* cross-tenant memories.

Core retrieval tests should exercise:

1. candidate search;
2. reranking or the named deterministic fallback;
3. utility scoring;
4. MMR;
5. token packing;
6. rejected-memory audit output.

Do not describe a fallback-path test as live Qwen reranking.

## Simulation

Simulation is a predictive screen, not execution validation.

Use terms such as:

* `simulation`;
* `simulated_safe`;
* `predicted improvement`.

Do not use “validated execution” or “outcome verified” unless the action was actually executed and post-action evidence was observed.

Harmful sub-actions must override otherwise positive compound actions.

## Testing

Changes to lifecycle, retrieval, authorization, or demo verdicts require regression coverage.

Important cases include:

* out-of-order ingestion;
* equal-authority timestamp conflicts;
* higher- and lower-authority conflicts;
* cross-tenant attempts;
* trusted-provenance forgery;
* poison quarantine;
* stale, expired, and future-dated exclusion;
* harmful compound actions;
* demo PASS and FAIL paths;
* cleanup after both success and exceptions.

### Running tests against Postgres

The FastAPI container keeps an open asyncpg connection while it is running. Running `pytest backend/tests` against the same Postgres instance can hang on table-wide deletes because of the open API transaction. Stop the API container before testing:

```bash
docker compose stop api
pytest backend/tests
```
