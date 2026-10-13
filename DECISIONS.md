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
