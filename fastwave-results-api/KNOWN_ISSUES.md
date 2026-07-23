# Known issues

Issues discovered while building the ingestion pipeline (Step 2) and the
public API (Step 3). None of the `hytek-parser`-related ones required
editing that library itself; all are worked around in `app/ingestion/`.

## 1. `parse_hy3` decodes files using the host's default locale encoding

`hytek_parser.hy3_parser.parse_hy3` opens the file with a bare `open(file)`
- no `encoding=` argument. Python then decodes it using
`locale.getpreferredencoding()`, i.e. whatever the *host machine's* default
happens to be.

Hy-Tek Meet Manager is Windows software and exports HY3 files in the
Windows-1252 codepage. On this Windows dev box that also happens to be the
system default, so `parse_hy3` "just works" here - but on a typical Linux
deployment (Railway included) the default is UTF-8, and cp1252 bytes above
0x7F are not valid UTF-8. Confirmed against the Michael Bowles 2026
fixture, which contains fada'd Irish names ("Aoibhínn", "Éabha") as raw
single-byte 0xC9/0xED characters - decoding the file as UTF-8 raises
`UnicodeDecodeError` at byte 0xED. This will crash real Irish meet files in
production for any swimmer with an accented name.

Workaround: `app/ingestion/service.py::_force_open_encoding` temporarily
monkeypatches `builtins.open` to inject `encoding="cp1252"` for any
text-mode open that doesn't already specify one, scoped to the single
`parse_hy3(...)` call via a context manager. Verified effective by forcing
`encoding="ascii"` in the same harness and confirming it reproduces the
`UnicodeDecodeError` - i.e. the patch does intercept the exact `open()`
call `parse_hy3` makes.

## 2. H1/H2 DQ detail lines can attribute to the wrong round

`h1_parser`/`h2_parser` resolve which round's `dq_info` an H1/H2 line
belongs to by checking `entry.finals_dq_info or entry.swimoff_dq_info or
entry.prelim_dq_info` in that fixed order. If the same entry were
disqualified in *both* prelims and finals (unusual, but not impossible),
the H1/H2 detail lines for the prelim DQ would be misattributed to the
finals row instead. Not exercised by the Michael Bowles 2026 fixture (it
has no H1 lines at all), so it hasn't blocked us, but it's worth knowing
about before trusting `dqDescription`/`dqDetail` on a file with DQs in
multiple rounds for the same swimmer.

## 3. ~~Our own `results` schema can't uniquely identify relay rows~~ (resolved)

Was a Step 1 schema gap surfaced by Step 2, not a hytek-parser issue.
`results.swimmerId` is `NULL` for relay team rows, and Postgres treats
every `NULL` as distinct from every other `NULL` for uniqueness purposes,
so the original single `uq_result_event_swimmer_round` constraint over
`(eventId, swimmerId, round)` could never detect "this is the same relay
result as last time," and there was no column distinguishing e.g. a club's
"A" and "B" relay teams in the same event/round.

Fixed in revision `8aa9ffeee218` ("relay result identity"): added
`results.relayTeamId` (the F1 relay team letter), replaced the single
unique constraint with two partial unique indexes -
`ux_result_individual (eventId, swimmerId, round) WHERE swimmerId IS NOT
NULL` and `ux_result_relay (eventId, clubId, relayTeamId, round) WHERE
swimmerId IS NULL` - and added `ck_result_relay_shape` to enforce that
every row is unambiguously one shape or the other. `promote.py` now
upserts relay results the same way it upserts individual ones (ON CONFLICT
via `index_elements`/`index_where`, since these are partial indexes rather
than named constraints - `ON CONFLICT ON CONSTRAINT` doesn't apply to
them). `relay_legs` are cleared and reinserted per result on re-ingest,
same as `result_splits`.

## 4. `relay_legs.legTimeHs` is always left `NULL`

HY3's G1 splits are cumulative team times at each interval, not each
swimmer's individual leg time, and there's no reliable way to derive one
from the other without a real relay example to verify the arithmetic
against (e.g. confirming splits align exactly with leg boundaries for
every stroke/distance combination). Left unset rather than shipping an
unverified formula.

## 5. ~~`LocalDirStorage` (STORAGE_DIR) does not survive a Railway redeploy~~ (resolved)

Was acceptable through Step 4 (ingestion fully re-runnable, demo seeded via
CLI rather than real uploads) but not once Step 5 exposes uploads to real
users - a club uploading a HY3 file and then losing the original on the
next deploy is a real problem (no re-download, no re-processing from the
source file if a bug is found later).

Fixed in Step 5: `app/ingestion/storage_r2.py::R2Storage` implements the
same `FileStorage` protocol against Cloudflare R2 (S3-compatible, via
boto3) - callers (`app.ingestion.service`) never changed, only which
backend `app.ingestion.storage.get_storage()` returns. `STORAGE_BACKEND`
(`local` | `r2`) selects the backend; `app.config.Settings` fails loudly
at startup if `ENVIRONMENT=production` and `STORAGE_BACKEND` isn't `r2`,
so production can no longer boot on the non-persistent local backend by
accident. `LocalDirStorage` remains the default for dev/test - no R2
credentials needed to run the test suite (R2-specific tests mock the S3
client via `moto`). No migration needed for this fix; the refresh_tokens
migration added alongside it in Step 5 is revision `5fca10e63ac1`.
