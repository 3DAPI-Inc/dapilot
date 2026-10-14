# dapilot plugin

Claude Code skills that drive the 3DAPI platform through its public API.

## Requirements

- Python 3.10+ with the `jsonschema` package (`pip install jsonschema`).
  Set `DAPILOT_PYTHON` to the interpreter to use; otherwise `python3` on PATH.
- A 3DAPI API key. Mint one with only the scopes you need: PRICING for quoting,
  ORDER for order work, print-farm scopes for fulfilment. Never use a FULL key.

## Environment

```
DAPI_API_URL        origin of the public API, no trailing /v1 (e.g. https://api.example.com)
DAPI_API_KEY        the API key, sent as x-api-key
DAPILOT_CACHE_DIR   optional, where the OpenAPI spec is cached (default ~/.cache/dapilot)
DAPILOT_PYTHON      optional, interpreter for bundled scripts (default python3)
DAPILOT_STORE_DIR   store-private files: the env file with the keys, the Etsy token, config,
                    reference/, products, social sessions. Optional: without it a checkout
                    uses its own store/ and anything else uses ~/.dapilot, one store per
                    machine (scripts/store_path.py), so updates and new sessions keep the setup.
ETSY_KEYSTRING      Etsy Open API v3 app credentials, for research-comparables only
ETSY_SHARED_SECRET
FAL_KEY             fal.ai key (FAL_API_KEY also accepted), for design-product's paid assets only
DAPILOT_STUDIO_URL  optional, the browser studio page for captures (default design.studio_url in config)
CHROME              optional, path to Chrome or Chromium for headless captures
```

The design stage also needs `ffmpeg` and `ffprobe` on PATH, the Python
package `fal-client`, Chrome, and the studio page served (`npm run dev` in the
host project's `frontend/`, route `/studio`).

Nothing falls back to a default host. Missing env is a hard error.

The easy way to set these: `/dapilot:config` opens a local page
(`scripts/integrate.py serve`, 127.0.0.1 only) that lists every integration
with the steps to obtain each value and one field per value. The page writes
`$DAPILOT_STORE_DIR/.env` (mode 0600), verifies each value against its
platform with a free request, and records the result in
`$DAPILOT_STORE_DIR/integrations.json`. No value passes through the model.
The plugin ships a PreToolUse hook (`hooks/hooks.json`,
`scripts/guard_secrets.py`) that refuses Read, Edit, Grep, Glob and Bash on
the env file, the Etsy token files and `social/`; scripts take the env file
only as `--env <file>`. The hook fences the model's tools, not code the model
writes: the real protection is that every value is revocable on its platform.

## Getting it

Two ways in, same plugin:

- **Install it from Claude Desktop.** Customize, Plugins, and add `dapilot`
  from the `3DAPI-Inc/dapilot` marketplace (or the zip a release ships).
  Then, in Claude Code or the desktop app's Code tab, type
  `/dapilot:quickstart`. It opens the Integrations page, where the accounts
  are connected; the store lives in `~/.dapilot` on that computer and
  survives updates and reinstalls.
- **Clone the repository from GitHub.** `git clone` it and run
  `/dapilot:quickstart` from a session started in the checkout (or
  `scripts/dev/shop.sh setup` in a terminal). This route also sets up the
  tools the pipeline itself needs (Python venv, Blender, Chrome, ffmpeg,
  the console). The store is `~/.dapilot` here too unless `DAPILOT_STORE_DIR`
  says otherwise; the templates it starts from are in `templates/`.

From a terminal, the same install and its updates:

```bash
claude plugin marketplace add 3DAPI-Inc/dapilot    # the repository is the marketplace
claude plugin install dapilot@dapilot              # the id is the marketplace's name, not the repo's
claude plugin update dapilot@dapilot               # pull a new version; then /reload-plugins or a new session
```

The marketplace tracks the repository's default branch; `3DAPI-Inc/dapilot#<branch>`
pins another. Updates are off by default for third-party marketplaces: turn
them on under `/plugin`, Marketplaces, dapilot, Enable auto-update, and a new
version is fetched in the background and applied at the next launch. While
the repository is private, Claude Code uses your own git credentials: an SSH
key loaded in ssh-agent, or an HTTPS credential helper (`gh auth setup-git`).
Without a GitHub SSH key set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1`.

For development from a checkout: `claude --plugin-dir plugins/dapilot` loads
the folder for one session, and `claude plugin marketplace add
/path/to/dapilot` then `claude plugin install dapilot@dapilot` installs it in
place. Skills and commands are namespaced: `/dapilot:<name>`. A typed slash
command comes from `commands/`; the desktop app and Cowork load a plugin's
components the next time a task starts.

## Releasing

A push to GitHub is a release, with one rule: installed copies only update
when `version` in `.claude-plugin/plugin.json` changes. Commit the work, then

```bash
scripts/dev/release.sh            # patch bump, validate --strict, commit, tag v<version>, push
scripts/dev/release.sh minor      # or major, or an exact 0.3.0; --no-push to stop before pushing
```

The `plugin` GitHub Actions workflow validates both manifests on every push
and fails a pull request that changes the plugin without moving the version.

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
store config (default 15), or `minutes_per_stage` for their stage (design: 40);
a `.lock` older than its stage's limit is stale and cleared.

The store's `.env` (seeded from `templates/.env.example`) supplies
`ANTHROPIC_API_KEY`, `DAPI_*` and `DAPILOT_PYTHON`.

## Skills

General, loaded into every agent: `hierarchy`, `guardrails`, `output-format`, `work-ledger`.

| Job skill | Does |
|---|---|
| `dapi-request` | Builds, validates and sends a request to any public API route from the live OpenAPI contract. Every other skill goes through it. |
| `analyze-model` | First intake stage: geometry facts for one model file via the bundled mesh script. |
| `quote-model` | Prices one model through 3DAPI's slicer; quotes named by sha256 so a file is never sliced twice. |
| `design-product` | Design stage, run by the `product-designer` agent: names the product and writes one brief (orientation, hero angle, stills, colour, lifestyle scenes); `design_product.py` captures studio stills and a turntable in the browser, generates lifestyle photos and a clip on fal under a per-item spend cap, and writes `design.json` with the photos in Etsy order and the listing video. |
| `render-model` | Retired 2026-09-13 in favour of `design-product`; kept for reference. |
| `preview-model` | The 8-frame draft turntable, so research and pricing can see the object before any final render is paid for. Needs Blender. |
| `research-comparables` | Finds the Etsy listings a model competes with, picks the true comparables, and writes the market band and our margin (`viability.json`). Needs Etsy credentials, no 3DAPI key. |
| `propose-price` | Picks the sale price from the candidates `price_candidates.py` allows, with a rationale; the script's `--check` refuses anything below break-even or outside the candidates. Needs the store pricing policy. |
| `run-intake` | Director skill for an interactive session; the headless equivalent is `scripts/run_intake.py`. |
| `config` (command) | Opens the Integrations page and waits while it is open; a person connects 3DAPI, Etsy, fal and optionally Instagram there, every value verified against its platform and never passing through the model. `/dapilot:config status` only reports. |
| `quickstart` (command) | First run, or the start of a listing: seeds the store, sets the environment up on any system, checks every required account against its platform and stops with the names if one is not connected; with a model file or folder it starts the intake run, which stops at the price for approval. Output is written for a person. |

## Scripts

| Script | Does |
|---|---|
| `dapi.py` | spec-driven API client: `spec`, `ops`, `describe`, `validate`, `call`, `upload` |
| `ledger.py` | run state: `init`, `next`, `record`, `status`, `report`; the only writer of `runs/<id>/state.json` |
| `validate_result.py` | the result-line contract every level checks on the level below |
| `run_intake.py` | deterministic director and lead: launches one worker per brief |
| `quote.py` | upload, create, poll one quote; resumable; finds an existing quote by hash |
| `render_model.py`, `render_scene.py` | Blender render driver and scene; the preview drafts |
| `design_product.py` | the design stage: `context`, `check` (brief schema and spend cap), `produce` (resumable, exit 3 at its deadline), `finalize` (photo order, listing video, Etsy limits) |
| `capture_turntable.py` | headless Chrome over the studio page: stills at exact yaws, turntables, depth passes; encodes with ffmpeg |
| `generate_image.py`, `generate_video.py`, `fal_queue.py` | lifestyle photo (image edit) and depth-conditioned clip (Wan) on fal; a request is written on submit and a rerun attaches, so nothing is paid twice |
| `upscale_video.py` | Topaz upscale on fal; manual, not part of any stage |
| `strip_3mf.py` | removes foreign slicer project settings (Creality Print, Orca, Prusa) from a 3mf so Bambu Studio accepts it; `--check` to test first |
| `source_thumbnails.py` | copies the plate and top previews a 3MF carries into `thumbnails/`; run by `ledger init`, so the research worker has the designer's own pictures before any draft exists |
| `etsy_search.py`, `etsy_auth.py` | Etsy comparables search with size and digital filters; OAuth login and refresh for the inventory pass |
| `integrate.py` | the Integrations page: `serve` (local server and page), `status`, `test`; writes the env file and `integrations.json`, verifies each credential with a free call |
| `setup_env.py` | the environment, the same on macOS, Linux and Windows: a venv under the store with the plugin's packages, Blender, Chrome and ffmpeg found or installed (winget, brew, apt, dnf), their paths written to the store's `.env`; `--no-install` only reports |
| `quickstart.py` | what `/dapilot:quickstart` runs: setup, wordmark, `integrate.py serve`, `integrate.py status`; says little, logs to the setup script's log |
| `guard_secrets.py` | the PreToolUse hook that refuses the model's tools on credential files |
| `market_stats.py` | price band, fees, VAT and margin from `comparables.json`, `quote.json` and the store pricing policy; writes `viability.json` |
| `price_candidates.py` | the two sale-price candidates (quote plus markup, market median) above the break-even floor; `--check` refuses any `price.json` below the floor, from any author |
| `pricing.py` | the fee, VAT and margin formula both scripts share; reads `$DAPILOT_STORE_DIR/reference/pricing-policy.json` |
| `analyze_mesh.py`, `load_3mf.py`, `make_fixtures.py` | geometry |
