# dapilot plugin

Claude Code skills that drive the dapi platform through its public API.

## Requirements

- Python 3.10+ with the `jsonschema` package (`pip install jsonschema`).
  Set `DAPILOT_PYTHON` to the interpreter to use; otherwise `python3` on PATH.
- A dapi API key. Mint one with only the scopes you need: PRICING for quoting,
  ORDER for order work, print-farm scopes for fulfilment. Never use a FULL key.

## Environment

```
DAPI_API_URL        origin of the public API, no trailing /v1 (e.g. https://api.example.com)
DAPI_API_KEY        the API key, sent as x-api-key
DAPILOT_CACHE_DIR   optional, where the OpenAPI spec is cached (default ~/.cache/dapilot)
DAPILOT_PYTHON      optional, interpreter for bundled scripts (default python3)
DAPILOT_STORE_DIR   store-private files: the Etsy OAuth token, reference/platform-fees.json,
                    the cached Etsy taxonomy. Required by research-comparables.
ETSY_KEYSTRING      Etsy Open API v3 app credentials, for research-comparables only
ETSY_SHARED_SECRET
```

Nothing falls back to a default host. Missing env is a hard error.

## Loading

```bash
claude --plugin-dir /path/to/plugins/dapilot
/dapilot:dapi-request        # skills are namespaced by plugin name
```

## Running a pipeline

```bash
python scripts/run_intake.py <folder>          # headless; routing is code, workers are claude -p --agent
python scripts/run_intake.py <folder> --dry-run
python scripts/run_intake.py --resume          # every work dir with an enabled stage still to run
python scripts/run_intake.py --items <hash>,…  # specific work dirs already on disk
```

Each run keeps `runs/<id>/state.json` (the ledger), `report.md`,
`director.log` and every worker's raw output under `workers/`. Workers are
killed with their whole process group after `max_worker_minutes` from the
store config (default 15); a `.lock` older than that is stale and cleared.

`store/.env` (see `store/.env.example` in the host project) supplies
`ANTHROPIC_API_KEY`, `DAPI_*` and `DAPILOT_PYTHON`.

## Skills

General, loaded into every agent: `hierarchy`, `guardrails`, `output-format`, `work-ledger`.

| Job skill | Does |
|---|---|
| `dapi-request` | Builds, validates and sends a request to any public API route from the live OpenAPI contract. Every other skill goes through it. |
| `analyze-model` | First intake stage: geometry facts for one model file via the bundled mesh script. |
| `quote-model` | Prices one model through dapi's slicer; quotes named by sha256 so a file is never sliced twice. |
| `render-model` | Studio product images: draft turntable, the model picks orientation and hero angle, four finals. Needs Blender. |
| `preview-model` | The 8-frame draft turntable, so research and pricing can see the object before any final render is paid for. Needs Blender. |
| `research-comparables` | Finds the Etsy listings a model competes with, picks the true comparables, and writes the market band and our margin (`viability.json`). Needs Etsy credentials, no dapi key. |
| `propose-price` | Picks the sale price from the candidates `price_candidates.py` allows, with a rationale; the script's `--check` refuses anything below break-even or outside the candidates. Needs the store pricing policy. |
| `run-intake` | Director skill for an interactive session; the headless equivalent is `scripts/run_intake.py`. |

## Scripts

| Script | Does |
|---|---|
| `dapi.py` | spec-driven API client: `spec`, `ops`, `describe`, `validate`, `call`, `upload` |
| `ledger.py` | run state: `init`, `next`, `record`, `status`, `report`; the only writer of `runs/<id>/state.json` |
| `validate_result.py` | the result-line contract every level checks on the level below |
| `run_intake.py` | deterministic director and lead: launches one worker per brief |
| `quote.py` | upload, create, poll one quote; resumable; finds an existing quote by hash |
| `render_model.py`, `render_scene.py` | studio render driver and the Blender-side scene |
| `strip_3mf.py` | removes foreign slicer project settings (Creality Print, Orca, Prusa) from a 3mf so Bambu Studio accepts it; `--check` to test first |
| `etsy_search.py`, `etsy_auth.py` | Etsy comparables search with size and digital filters; OAuth login and refresh for the inventory pass |
| `market_stats.py` | price band, fees, VAT and margin from `comparables.json`, `quote.json` and the store pricing policy; writes `viability.json` |
| `price_candidates.py` | the two sale-price candidates (quote plus markup, market median) above the break-even floor; `--check` refuses any `price.json` below the floor, from any author |
| `pricing.py` | the fee, VAT and margin formula both scripts share; reads `$DAPILOT_STORE_DIR/reference/pricing-policy.json` |
| `analyze_mesh.py`, `load_3mf.py`, `make_fixtures.py` | geometry |
