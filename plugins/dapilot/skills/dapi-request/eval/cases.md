# dapi-request eval cases

All cases run against `eval/openapi.json`, a snapshot of the contract
generated offline with `scripts/dev/dump_openapi.sh`. Regenerate it when the
public API changes. `$S` is `plugins/dapilot/scripts/dapi.py`,
`$SPEC` is `eval/openapi.json`, `$PY` is a Python with `jsonschema`.

## 1. Happy path: a valid createQuote body validates

```
echo '{"s3_key":"public-api/quotes/x/input/1-a.stl","name":"shark"}' \
  | $PY $S --spec $SPEC validate createQuote --body -
```
Expected: `ok:true, valid:true`, `request.scope` PRICING, `request.gate` auto. Exit 0.

## 2. Hostile input: wrong body is rejected with named errors

Body `eval/bad-create-quote.json` (missing `s3_key`, unknown `material`,
`name` of 120 chars).
```
$PY $S --spec $SPEC validate createQuote --body eval/bad-create-quote.json
```
Expected: `ok:false, valid:false` and exactly these three errors, exit 1:
- `body: 's3_key' is a required property`
- `body: Additional properties are not allowed ('material' was unexpected)`
- `name: '...' is too long`

## 3. Query and path parameters are checked

```
$PY $S --spec $SPEC validate listQuotes --query page_size=500 --query foo=1
$PY $S --spec $SPEC validate getQuoteByPublicId
```
Expected: first fails with `query.page_size: 500 is greater than the maximum
of 100` and `unknown query parameter: foo`; second fails with
`missing path parameter: publicId`. Both exit 1.

## 4. Gated operation without --confirm is a dry run

```
DAPI_API_URL=http://127.0.0.1:1 DAPI_API_KEY=k $PY $S --spec $SPEC \
  call updatePrintFarmOrderStatus --path orderId=AP7K2M9X --body eval/status.json
```
Expected: `ok:true, dry_run:true`, `request.url` ends in
`/v1/print-farm/orders/AP7K2M9X/status`, nothing sent, exit 0.

## 5. Missing env fails before any network

```
env -u DAPI_API_URL $PY $S --spec $SPEC call getMe
```
Expected: `ok:false, reason: "DAPI_API_URL is not set"`, exit 2.

## 6. Live path against a mock (spec fetch, cache, auth header, rate limit)

Serve `eval/openapi.json` at `/openapi.json` and a JSON body at `/v1/me`
from a local HTTP server (see `eval/mock.py`), then:
```
DAPI_API_URL=http://127.0.0.1:8765 DAPI_API_KEY=test DAPILOT_CACHE_DIR=/tmp/c \
  $PY $S spec --refresh
DAPI_API_URL=http://127.0.0.1:8765 DAPI_API_KEY=test DAPILOT_CACHE_DIR=/tmp/c \
  $PY $S call getMe
```
Expected: `spec` reports 35 operations and writes
`/tmp/c/openapi.127.0.0.1_8765.json`; `call` returns `ok:true, status:200`,
the mock saw `x-api-key: test` and user agent `dapilot-dapi-request/0.1.0`,
`rate_limit` carries the `x-ratelimit-*` headers. A GET on an unknown quote
returns `ok:false, status:404` and exit 1.

## Cached rerun

`validate` and `describe` are pure and idempotent; there is no output file to
cache. `spec` without `--refresh` reuses the cache and never hits the network.

Last run: 2026-09-03, all six cases pass.
