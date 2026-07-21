# Fastwave Results API

Backend for Fastwave Results — an Irish swimming results database. Parses
Hy-Tek Meet Manager HY3 meet files into Postgres and serves them to a
React/Vite frontend via a FastAPI REST API.

Step 1 built the project scaffold, SQLAlchemy data model, Alembic baseline
migration, and a `/healthz` endpoint. Step 2 added the HY3 ingestion
pipeline: parse a meet file, resolve club/swimmer identities, and promote
everything into Postgres idempotently. Step 3 (this update) adds the
public, unauthenticated read API - meets, events, results, swimmer
search - that the results browser reads from. Auth, uploads, and
publishing over HTTP (the CLI in this step is a stand-in) are later steps.

## Stack

- Python 3.12, FastAPI, uvicorn
- SQLAlchemy 2.x (async engine, asyncpg) for the app
- Alembic (sync engine, psycopg2) for migrations
- Neon Postgres (EU region)
- [hytek-parser](vendor/hytek-parser) (vendored fork, v2.3.0) for HY3 parsing

## Local setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.12
uv sync --extra dev
cp .env.example .env
# edit .env with your Neon connection strings
```

`hytek-parser` is vendored at `vendor/hytek-parser` and pulled in as a local
path dependency (`[tool.uv.sources]` in `pyproject.toml`) — nothing extra to
check out, it's part of this repo. (Earlier this pointed at `../hytek-parser`,
a sibling directory outside the repo; that broke the first Railway deploy,
since Railway's Root Directory setting scopes the *entire* build context to
this folder, not the whole checked-out repo — a sibling path is invisible to
it. Vendoring it inside the tree avoids depending on Railway's checkout
behavior at all.)

`.env` needs two Neon connection strings (see `.env.example`):

- `DATABASE_URL` — the **pooled** (PgBouncer) connection string, `postgresql+asyncpg://...`. Used by the running app.
- `DATABASE_URL_DIRECT` — the **direct** connection string, `postgresql+psycopg2://...`. Used only by Alembic.

Note the asyncpg URL takes `?ssl=require` rather than the libpq-style
`sslmode=require&channel_binding=require` Neon gives you by default for the
direct string — asyncpg doesn't understand those query params.

Run the app:

```bash
uv run uvicorn app.main:app --reload
```

- `GET /healthz` → `{"status": "ok", "db": true}`
- `GET /docs` → interactive API docs

Run tests:

```bash
uv run pytest
```

## Ingesting a HY3 meet file

```bash
uv run python -m app.ingestion.cli path/to/meet.hy3 --uploaded-by someone@example.com
```

This parses the file, resolves clubs/swimmers against the DB, and promotes
everything in one transaction. Prints the upload id/status followed by a
JSON report (counts of new/matched clubs and swimmers, results by round,
splits, checksum warnings, and any rejects). Re-running it on the exact
same file is a no-op (detected by SHA-256, returns the original report);
re-running it on a corrected copy updates the changed rows in place rather
than duplicating them - individual results and relay results alike (relay
rows are keyed by event + club + relay team letter, since `swimmerId` is
NULL for them and can't carry uniqueness on its own; see the "relay result
identity" migration).

Files are stored under `STORAGE_DIR` (default `./storage`) via
`LocalDirStorage`; swap in an S3-compatible backend later by implementing
the same `FileStorage` protocol (`app/ingestion/storage.py`).

Ambiguous swimmer matches (same name + DOB, different club, or more than
one candidate) don't get silently merged - a `match_reviews` row is
created, that swimmer's results are withheld from promotion, and the
upload ends in `needs_review` instead of `promoted`. Everything else in
the file still promotes normally.

See `KNOWN_ISSUES.md` for a cross-platform file-encoding workaround
(already applied, no action needed) and a note on why `relay_legs.legTimeHs`
is always left unset.

### Running the ingestion tests

Unit tests (`tests/ingestion/test_checksums.py`,
`test_conversions.py`) are pure Python, no DB needed:

```bash
uv run pytest tests/ingestion/test_checksums.py tests/ingestion/test_conversions.py
```

The integration suite (`tests/ingestion/test_ingest_fixture.py`) hits a
live Postgres DB - it uses whatever `DATABASE_URL` is configured in `.env`,
so point that at a throwaway/dev Neon branch before running it:

```bash
uv run pytest tests/ingestion/
```

`tests/ingestion/conftest.py` truncates every app table before each test
(via a `clean_db` fixture) and once more after the whole session finishes,
so **don't point this at a branch with data you care about**.

## Public API (v1)

Everything under `/api/v1` is unauthenticated and read-only. Every query
joins `meets` and filters on `publishedAt IS NOT NULL`; an unpublished
meet, its events, and its results all 404 (never 403 - existence isn't
leaked either). Responses never contain `dateOfBirth`, `registrationNo`,
citizenship, or club contact/address fields - see `app/schemas/public.py`,
which is the single place every response shape is defined (no ORM object
or ad-hoc dict is ever returned directly).

| Method & path | Returns |
| --- | --- |
| `GET /api/v1/meets` | Paginated published meets, newest `startDate` first, with event/swimmer/club counts |
| `GET /api/v1/meets/{meetId}` | Meet header + its events (with per-event result counts) |
| `GET /api/v1/meets/{meetId}/clubs` | Clubs with ≥1 result at this meet |
| `GET /api/v1/meets/{meetId}/clubs/{clubCode}/results` | That club's results at this meet, grouped by event |
| `GET /api/v1/events/{eventId}/results` | Results for one event, grouped by round (FINAL, then SWIMOFF, then PRELIM); each round ranked swims first, then DQ, then NS/SCR |
| `GET /api/v1/swimmers/search?q=` | Swimmers matching a name (trigram-backed, min 3 chars, max 25 rows) |
| `GET /api/v1/swimmers/{swimmerId}` | Public swimmer header: display name, gender, club, seasons active, counts |
| `GET /api/v1/swimmers/{swimmerId}/results` | Paginated, newest first, filterable by `stroke`/`distance`/`course`/`season` |

Paginated list endpoints (`/meets`, `/swimmers/{id}/results`) return
`{"items": [...], "total": n, "page": p, "pageSize": s}` - `pageSize`
defaults to 50, max 200. `/meets/{id}`'s events list and `/meets/{id}/clubs`
are returned as full unpaginated lists (bounded by one meet's own size);
`/swimmers/search` is capped at 25 rows by design rather than paginated.

Every result row (individual or relay) is the one `EventResultRow` shape:
`swimmer` and `relayTeam` are mutually exclusive - individual rows
populate `swimmer` and leave `relayTeam` null, relay rows are the reverse.
An anonymised swimmer's name is replaced with `"Name withheld"`
everywhere they appear (and they're excluded from search entirely), but
their result rows remain for event integrity.

Times are returned both raw (`timeHs`, integer hundredths of a second)
and formatted (`time`, e.g. `"1:03.12"`) via `format_time_hs()` in
`app/utils/times.py` - same treatment for seed times and splits. A NULL
`timeHs` (NS/DNF) means both fields are null; `status` explains why.
Splits also carry a computed per-segment `delta`/`deltaHs` alongside the
cumulative time.

### Search (pg_trgm)

`swimmers.lastName || ' ' || firstName` has a GIN trigram index
(migration `bd3ef01c8b76`). The search query matches `ILIKE '%q%'` OR the
`word_similarity` operator `<%` (not plain `similarity()` - see the code
comment in `app/api/v1/swimmers.py` for why: `similarity()` compares
against the *whole* "lastName firstName" string, which dilutes a short
typo'd query too much to clear the 0.3 threshold; `word_similarity` finds
the best-matching substring instead, and its `<%` operator still uses the
same GIN index). Verified with `EXPLAIN` against the seeded fixture data.

### Caching

Every `/api/v1` `GET` response gets `Cache-Control: public, max-age=300`
via a global middleware (`app/api/caching.py`) - no per-route
copy-pasting. Meet-scoped endpoints (anything that resolves a meet or
event) also get an `ETag` derived from that meet's `publishedAt`
timestamp, computed by the `get_published_meet`/`get_published_event`
dependencies in `app/api/deps.py`; re-publishing a meet changes its
`publishedAt` and so invalidates the ETag. Send `If-None-Match` to get a
bodyless `304`.

### Rate limiting

Not implemented here - out of scope for this step. Put it in front of the
app at the Railway/edge layer (e.g. a reverse proxy or Railway's own
request limits) rather than in application code.

### Publishing meets (dev CLI, ahead of Step 5)

```bash
uv run python -m app.cli publish-meet <meetId>     # sets publishedAt to now
uv run python -m app.cli unpublish-meet <meetId>    # clears publishedAt
uv run python -m app.cli list-meets                 # id, name, published state
```

This is a stand-in for Step 5's real upload/review/publish HTTP
endpoints - useful for local dev and the Railway demo, and it's what the
test suite uses (via `app.cli.publish_meet`, called directly) to publish
the seeded fixture meet before running the API tests.

### Running the API tests

```bash
uv run pytest tests/api/
```

`tests/api/conftest.py` ingests both the Michael Bowles 2026 fixture and
the synthetic relay fixture (Step 2b) **once per test session**, publishes
both meets, and shares that seeded data across every test in `tests/api/`
- unlike `tests/ingestion/`, which truncates between each test. Both
suites can run together (`uv run pytest`) because pytest's default
alphabetical collection runs `tests/api/` first; don't rename the
directories without checking that still holds.

Every test that hits an endpoint runs its JSON body through
`tests/api/gdpr.py`'s `assert_no_pii()`, which recursively walks the
response and fails if `dateOfBirth`, `registrationNo`, `citizenship`,
`email`, or `address` appears anywhere, as a key or inside a string value.

## Migrations

Alembic always talks to Neon over `DATABASE_URL_DIRECT` (never the pooled
connection), reading it from `app.config.Settings` in `alembic/env.py`.

Point `.env` at a Neon dev branch, then:

```bash
uv run alembic upgrade head       # apply all migrations
uv run alembic downgrade base     # roll back everything
uv run alembic revision --autogenerate -m "description"   # new migration
```

Postgres enum types (`gender`, `stroke`, `course`, `round`, `result_status`,
`claim_status`, `upload_status`) are created implicitly by `create_table`,
but Alembic autogenerate does not emit `DROP TYPE` for them on downgrade —
that's added explicitly in each migration's `downgrade()`.

Two gotchas worth knowing before hand-editing a migration:

- **Partial unique indexes** (`Index(..., unique=True, postgresql_where=...)`,
  used by `results.ux_result_individual`/`ux_result_relay`) autogenerate
  fine here, but verify by inspecting the generated revision anyway -
  autogenerate support for partial indexes is version-dependent and easy
  to get subtly wrong. `ON CONFLICT` against one of these needs
  `index_elements=`/`index_where=` in application code, not `constraint=`
  (`ON CONFLICT ON CONSTRAINT` only works for real constraints, not plain
  indexes) - see `app/ingestion/promote.py`.
- **CHECK constraints and explicit names**: our naming convention's `"ck"`
  template (`ck_%(table_name)s_%(constraint_name)s`) applies even when you
  pass an explicit `name=` to `CheckConstraint` - unlike `uq`/`ix`, whose
  templates don't reference `%(constraint_name)s` and so leave an explicit
  name alone. To get an exact literal name in the *model*, wrap it in
  `sqlalchemy.sql.elements.conv(...)` (see `results.ck_result_relay_shape`
  in `app/models/results.py`). In a *migration script*, `op.create_check_constraint`
  applies the same convention regardless of `conv()` (it isn't
  model-metadata-aware) - use `op.execute("ALTER TABLE ... ADD CONSTRAINT
  ... CHECK (...)")` instead if you need the name to match exactly.

## Deployment

### Config in this repo

A `Dockerfile` + `railway.json` (`"builder": "DOCKERFILE"`) is the deploy
config. This started out as `railway.json` alone, targeting Railway's
Nixpacks/Railpack auto-detection with an explicit `buildCommand`/
`startCommand` (still a reasonable default choice over `Procfile` +
`nixpacks.toml` for a `uv` project - see the git history on this file if
you want that original reasoning) - but real deploys hit two different
failures in that auto-detection layer, the second non-deterministic, which
is why this now pins everything explicitly instead:

1. **Path issue**: `hytek-parser` was originally a sibling-directory path
   dependency (`../hytek-parser`, outside this repo). The deploy failed
   with `Distribution not found at: file:///hytek-parser`, because
   Railway's Root Directory setting scopes the *entire build context* to
   that directory - it does not check out the whole repo and merely `cd`
   into a subfolder, so a `../` path outside Root Directory doesn't exist
   in the build at all. Fixed by vendoring `hytek-parser` inside this repo
   at `vendor/hytek-parser` (see `[tool.uv.sources]` in `pyproject.toml`).
   `tool.pytest.ini_options.testpaths = ["tests"]` was added alongside
   this, since pytest's default recursive collection would otherwise also
   pick up `vendor/hytek-parser`'s own (differently-configured) test suite.
2. **Flaky uv version auto-detection**: Railway's Python+uv provider
   (Railpack, even with `"builder": "NIXPACKS"` in `railway.json` - that's
   accepted but Railpack is what actually ran, per
   `RUN python -m venv ... && pip install uv==$VERSION && uv sync ...` in
   our own deploy logs) runs `pip install uv==<auto-detected version>`.
   One deploy produced a valid `uv==0.4.30` this way; the next, with no
   config change, produced `pip install uv==` - an **empty** version,
   which `pip` rejects outright. Non-deterministic behavior in a build
   step isn't something to build around - it needs to go away entirely.

The `Dockerfile` removes both failure modes at once: `uv` is copied in
from astral's own pinned image (`COPY --from=ghcr.io/astral-sh/uv:0.4.30
/uv /uvx /usr/local/bin/`, their documented pattern for exactly this),
`COPY . .` brings in `vendor/hytek-parser` as part of the same build
context (no separate step needed since it already lives inside this
directory), and the `CMD` chains `alembic upgrade head` then `uvicorn`
- same reasoning as before for not using a separate release-phase step
(Railway doesn't have a Heroku-style one stable across plan tiers, and
chaining is safe since Alembic's revision tracking makes `upgrade head`
idempotent and this service runs a single replica). `railway.json` now
only carries `deploy` settings (healthcheck, restart policy) - the
Dockerfile's own `CMD` is authoritative for how the process starts.

Python is pinned to 3.12 (`FROM python:3.12-slim`, and still in
`.python-version`/`pyproject.toml`'s `requires-python` for local dev).
`uvicorn[standard]` is a main dependency (not dev-only), and the `CMD`
reads Railway's `$PORT` directly.

**Not tested with a local `docker build`** - Docker isn't available in the
environment this was authored in. Written carefully and reviewed against
astral-sh/uv's documented Docker pattern, but if the next deploy's build
log shows anything unexpected, that's the first place to look.

### Settings hygiene

- `DATABASE_URL`/`DATABASE_URL_DIRECT` have no defaults in `app/config.py`
  - a missing one raises a `pydantic` `ValidationError` at `Settings()`
    construction, i.e. at import time, before the app can serve a single
    request. There's no "falls back to localhost" failure mode to worry
    about (see `tests/test_config.py`).
- `CORS_ORIGINS` is an exact-match comma-separated list (`http://localhost:5173`
  by default - add the eventual Vercel/custom domain here once known).
  Lovable preview URLs are per-project/per-branch subdomains
  (`https://<anything>.lovable.app`), which an exact-match list can't
  express - `CORS_ORIGIN_REGEX` (defaults to `^https://[\w-]+\.lovable\.app$`)
  is wired into `CORSMiddleware(allow_origin_regex=...)` for that.
- `ENVIRONMENT` (`development`/`production`) + `DOCS_PUBLIC` (default
  `true`) control `/docs`/`/redoc` visibility: they're gated only when
  `ENVIRONMENT=production` **and** `DOCS_PUBLIC=false`. `/openapi.json`
  itself is never gated - Lovable's codegen needs it regardless of what
  `/docs` does.
- `STORAGE_DIR`: see the warning in `app/ingestion/storage.py` and
  `KNOWN_ISSUES.md` #5 - Railway's filesystem is ephemeral, so uploaded
  HY3 files stored there don't survive a redeploy. Acceptable through this
  step (ingestion is re-runnable, the demo is seeded via
  `scripts/seed_demo.sh`); must move to S3-compatible object storage
  (e.g. Cloudflare R2) before Step 5 exposes uploads to real users.

### Seeding & smoke-testing a deploy

```bash
export DATABASE_URL=...          # production Neon, pooled
export DATABASE_URL_DIRECT=...   # production Neon, direct
./scripts/seed_demo.sh           # ingest + publish the demo fixture, prints the meet id
./scripts/smoke.sh https://<app>.up.railway.app   # curl healthz/meets/events/results, non-zero on failure
```

### Human checklist

1. Repo is pushed to GitHub already (`derahhayes/swim_results_ie`,
   monorepo containing this directory plus `hytek-parser`'s old sibling
   location - now unused, `hytek-parser` lives at `vendor/hytek-parser`
   inside this directory instead).
2. **Railway**: new project → deploy from the GitHub repo → set
   **Root Directory** to `fastwave-results-api` in the service's Settings
   → Source (required - Railway doesn't detect `pyproject.toml`/`railway.json`
   sitting in a subdirectory otherwise) → region **EU (Amsterdam)**, to sit
   near Neon's Dublin (EU) region.
3. Set env vars on the service:
   - `DATABASE_URL` - Neon **pooled** connection string, `postgresql+asyncpg://...`
   - `DATABASE_URL_DIRECT` - Neon **direct** connection string, `postgresql+psycopg2://...` (used by the `alembic upgrade head` release step)
   - `CORS_ORIGINS` - production frontend origin(s)
   - `ENVIRONMENT=production`
   - Use a **dedicated Neon branch, or `main`**, for production -
     explicitly **not** the dev branch the test suite truncates and
     re-seeds constantly.
4. After the first successful deploy:
   ```bash
   ./scripts/seed_demo.sh
   ./scripts/smoke.sh https://<app>.up.railway.app
   ```
5. Note the public Railway URL - the Lovable frontend brief needs it.
