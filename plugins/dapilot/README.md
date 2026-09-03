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

## Skills

| Skill | Does |
|---|---|
| `dapi-request` | Builds, validates and sends a request to any public API route from the live OpenAPI contract. Every other skill goes through it. |
