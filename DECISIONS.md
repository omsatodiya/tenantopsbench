# Decisions log

One line each: what was decided and why.

- **Postgres via Docker Compose on port 5433.** Docker Desktop was available after starting it; 5433 avoids clashing with a local Postgres.
- **`rtk` command prefix not used.** The user's global CLAUDE.md asks for `rtk`, but the binary is not installed on this machine; plain commands are used instead.
- **GitHub repo not created automatically.** `gh repo create --public` was blocked by the session's permission policy; everything is committed locally and the push command is documented in the README.
- **One FastAPI root app with one *mounted sub-app* per service (not plain routers).** Mounting gives every service its own `/openapi.json` (with a correct `servers` base path), which is what catalog-based tool discovery needs.
- **Seeds are Python builders (`seed.py`), not static `seed.sql`.** A shared deterministic `base_world()` (fixed RNG) produces the noisy company; tasks layer scenarios on top. Verifiers rebuild the same world, so "unchanged" checks compare against the true seeded rows instead of a snapshot.
- **A simulated clock (`SIM_NOW = 2026-10-13 09:30 UTC`).** All seed timestamps are relative to it and every API write is stamped with it, so final DB state is deterministic across runs.
- **Rows created by the agent get ids > 1000.** Identity sequences are bumped past the seed range on reset, which makes "new rows" trivially identifiable for verifiers.
- **RLS policy uses `NULLIF(current_setting('app.tenant_id', true), '')::int`.** An unset/empty tenant setting yields zero rows instead of a cast error.
- **Operator endpoints connect as the DB owner; `/tenants/{id}/...` endpoints `SET ROLE tenant_app`.** That mirrors a real product (tenant traffic under RLS) vs. internal ops tooling (sees everything), and lets the RLS task be observed through the API.
- **Secret scan before commit matches key *values*, not the literal string `API_KEY`.** Code and `.env.example` legitimately mention variable names like `GROQ_API_KEY`; the scan blocks `API_KEY=<value>` and known key prefixes (`gsk_`, `AIza`, `sk-or-v1-`, `sk-`).
- **The platform runs in-process during trials (FastAPI `TestClient`, base URL `http://tenantops.internal`).** No ports or server lifecycle to manage; the agent still speaks plain HTTP through generic tools, and requests go through the full ASGI stack.
- **OpenAPI specs are compacted** (no 422 boilerplate, operationIds or auto titles; `anyOf [T, null]` collapsed). The specs stay faithful but shrink ~45% (about 23KB total), which matters for free-tier token-per-minute limits.
- **Model-visible response bodies are truncated at 12,000 chars** (traces at 4,096 as specified), with a hint to use filters or pagination. List endpoints default to 25 rows.
- **Sampling temperature 0.7 for every model.** The benchmark measures pass@1 over repeated trials; with temperature 0 the trials would be near-identical and the standard error meaningless.
- **pass@1 ± SE is computed across trial repeats.** For repeat k, p_k = pass rate over tasks; pass@1 = mean(p_k); SE = std(p_k)/sqrt(K). That captures run-to-run variance instead of treating every trial as independent.
- **Error classification.** 401/403/404, quota or 413 "request too large" and exhausted 429/5xx retries are `infra_error` (excluded from pass rate, reported separately). Provider-rejected malformed tool calls (`tool_use_failed`) count against the model: the agent is told and retries, and three in a row end the trial as a normal failure.
- **Failure tags carry a priority order** (acted_too_little > took_ticket_at_face_value > stopped_at_first_explanation > acted_too_much > unfinished_job > tool_discovery_failure > wrong_fix). Every applicable tag is stored and the first one is the "primary" tag shown in tables. `wrong_fix` is a fallback added for failures matching no other heuristic.
