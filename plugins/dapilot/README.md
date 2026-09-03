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
```

`store/.env` (see `store/.env.example` in the host project) supplies
`ANTHROPIC_API_KEY`, `DAPI_*` and `DAPILOT_PYTHON`.

## Skills

General, loaded into every agent: `hierarchy`, `guardrails`, `output-format`, `work-ledger`.

| Job skill | Does |
|---|---|
| `dapi-request` | Builds, validates and sends a request to any public API route from the live OpenAPI contract. Every other skill goes through it. |
| `analyze-model` | First intake stage: geometry facts for one model file via the bundled mesh script. |
| `run-intake` | Director skill for an interactive session; the headless equivalent is `scripts/run_intake.py`. |

## Scripts

| Script | Does |
|---|---|
| `dapi.py` | spec-driven API client: `spec`, `ops`, `describe`, `validate`, `call`, `upload` |
| `ledger.py` | run state: `init`, `next`, `record`, `status`, `report`; the only writer of `runs/<id>/state.json` |
| `validate_result.py` | the result-line contract every level checks on the level below |
| `run_intake.py` | deterministic director and lead: launches one worker per brief |
| `analyze_mesh.py`, `load_3mf.py`, `render_turntable.py`, `make_fixtures.py` | geometry and rendering |
