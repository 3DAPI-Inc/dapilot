# Quickstart

Getting the shop ready to use on a new machine takes one command, run in a
terminal from the repo root:

```bash
scripts/dev/shop.sh setup
```

The first run creates `store/.env` from its template and stops. Fill in the
required values (section 2), then run it again. From then on it installs,
fetches and signs in to whatever is missing, and ends with the check: `every
enabled stage can run` means the shop is ready. It is safe to rerun at any
time; steps that are already done are skipped.

Setup works through these steps:

1. **Tools.** Claude Code CLI, node and npm, ffmpeg and ffprobe, Blender and
   Chrome. When one is missing it prints the install command and offers to
   run it (Homebrew, or Claude's installer).
2. **Python.** Creates `.venv` from python 3.10 to 3.13 (the macOS system
   3.14 has no trimesh wheels) and installs `requirements.txt`. It installs
   again only when that file changes.
3. **Frontend.** Runs `npm install` in `frontend/` when `package-lock.json` is
   newer than `node_modules`.
4. **Store files.** Copies `store/.env` and `store/config.json` from their
   templates when missing, and stops if a required value in `.env` is empty.
   Copies `store/pricing-policy.example.json` to
   `store/reference/pricing-policy.json` when missing. That template is dapi's
   own policy (EU seller, 21% VAT inside every sale, 50% markup, full-plate
   cost), so review it: every price proposal and the break-even floor come
   from it.
5. **Sign-ins.** Claude (`claude auth login`, unless `CLAUDE_CODE_OAUTH_TOKEN`
   or `ANTHROPIC_API_KEY` is set). Etsy, through a browser, when there is no
   token or it lacks `listings_w`; the old token is kept as a backup. The Etsy
   app must list `http://localhost:3003/oauth/redirect` as a callback.
   Instagram, from `INSTAGRAM_ACCESS_TOKEN`, when a social platform is enabled.
6. **Store reference.** Fetches Etsy's seller and buyer taxonomies into
   `store/reference/`. After the Etsy sign-in, it writes
   `store/reference/etsy-listing.json` (the shop-wide listing choices) from
   `store/etsy-listing.example.json` and the shop's own profiles. It fills
   the shipping profile, return policy and readiness state when the shop has
   exactly one of each, and asks which one when there are several.
7. **Check.** See below.

Setup asks before it installs anything or opens a sign-in. With no terminal
(piped or scheduled) it logs each question it skipped, so the Etsy sign-in
waits for a run in a terminal.

To check without changing anything:

```bash
scripts/dev/shop.sh check [--offline]
```

`check` exits 0 when every enabled stage can run, 1 when some stage is
blocked (a run stops at the first blocked stage), 2 when nothing can start.
`--offline` skips the live calls.

Every command logs to `runs/shop-<time>.log`. Each line is prefixed with the
time and the step. The log also gets what the terminal leaves out:
- git commit and host
- which variables `store/.env` sets (names only)
- full pip and npm output, and `pip freeze`
- tool versions and each step's duration
- the Etsy profiles setup read
- every API request and response at the price gate
- the run summary and `report.md`

`SHOP_TRACE=1` also logs every shell command the script runs. When setup
fails on a new machine, that log is the thing to send.

## 2. Environment variables

All of them live in `store/.env` (gitignored). A value already set in the
shell wins over the file. `shop.sh check` reads every one, says which stage it
blocks when it is missing, and for credentials makes one free call to prove
they work (`GET /me` on dapi, `/users/me` on Etsy, the Instagram session).
Secrets are shown by length only, never by value.

| Variable | Needed by | Required | Notes |
|---|---|---|---|
| `DAPI_API_URL` | quote | yes | origin only, e.g. `https://api.example.com`; no `/v1` |
| `DAPI_API_KEY` | quote | yes | scoped key, PRICING for intake |
| `DAPI_STORE_ID` | quote | no | only when the dapi account has more than one store |
| `ETSY_KEYSTRING`, `ETSY_SHARED_SECRET` | research, publish | yes | Etsy Open API v3 app; plus `store/etsy_token.json` |
| `FAL_KEY` (or `FAL_API_KEY`) | design | yes | paid: lifestyle photos and clip |
| `INSTAGRAM_ACCESS_TOKEN`, or `INSTAGRAM_APP_ID` + `INSTAGRAM_APP_SECRET` | publish | when social posting is on | the session file under `store/social/` is what counts |
| `ANTHROPIC_API_KEY` | every worker | no | set: workers bill to the API; unset: to the logged-in subscription |
| `CLAUDE_CODE_OAUTH_TOKEN` | every worker | no | from `claude setup-token`; signs workers in without a browser login |
| `DAPILOT_BILLING` | every worker | no | `auto`, `api` or `subscription` |
| `DAPILOT_STORE_DIR` | everything | no | default `./store` |
| `DAPILOT_PYTHON` | everything | no | default `./.venv/bin/python` |
| `BLENDER` | preview | no | default `/Applications/Blender.app/...` |
| `CHROME` | design | no | found on its own on macOS |
| `DAPILOT_STUDIO_URL` | design | no | default `http://localhost:8081` |
| `DAPILOT_SOCIAL_ADAPTER_DIR` | publish | no | browser adapters for X, TikTok, LinkedIn |

## 3. Running an item: `shop.sh run`

Once the shop is ready, `scripts/dev/shop.sh run <model file or folder>` takes an
item from the file to a live listing. It reruns the setup first (quick when
nothing is missing) and stops at the price gate for a person.

```
1/6 setup      install what is missing, then env, files, tools, live credentials, per stage (as above)
2/6 services   job API on :3100 and console/studio on :8081, started if not answering
3/6 intake     analyze -> quote -> preview -> research -> price, unattended
4/6 where it is  every stage of every item in the run
5/6 price gate you approve, override or skip each proposed price
6/6 result     design -> copy -> publish, followed live, then the final status
```

| Stage | What happens | Costs |
|---|---|---|
| analyze | local geometry pre-check | a worker's tokens |
| quote | dapi slices and prices the file (18 to 40 s) | tokens |
| preview | Blender draft turntable | tokens |
| research | Etsy comparables, market band, viability | tokens, up to 1.00 USD |
| price | the model picks a candidate; `price_candidates.py --check` enforces the break-even floor | tokens |
| **gate** | **a person approves the price** | |
| design | studio stills, turntable, lifestyle photos and clip on fal | up to `design.usd_cap_per_item` |
| copy | Etsy title, description, tags, social captions | tokens |
| publish | Etsy draft, media, **activate (public, 0.20 USD)**, social posts | no tokens |

Nothing paid or public runs before the gate. At the gate `shop.sh` shows the
market band, the unit cost, the floor, each candidate with its margin and the
model's reasoning, and says what approving will set off (it reads
`publish.auto_activate` and `publish.auto_post` from `store/config.json`). You
type `approve` to confirm. The approval goes through `POST /jobs/{id}/price`,
the same route and the same floor check as the console's button.

A file whose price was already approved goes straight on to design, copy and
publish. `shop.sh` lists such files before step 3 and asks first.

Options:

- `--dry-run`: steps 1 to 3 without launching a worker; shows what the director would dispatch.
- `--no-approve`: stop at the gate and print the command to approve later.

The same content hash is never processed twice: running a file again resumes
from its first unfinished stage.

## 4. Other commands

```bash
scripts/dev/shop.sh status [hash ...]              # where each item is, from the files in work/<hash>/
scripts/dev/shop.sh approve <hash> [price] [reason]  # the gate by hand; a price off the candidates needs a reason
scripts/dev/shop.sh resume                         # every work dir with a stage left
scripts/dev/up.sh                                  # just the services, for working in the console
```

`status` reads like this:

```
74f4ddf92753  cat (clickerfactory.com).3mf
  ✓ analyze  ✓ quote  ✓ preview  ✓ research  ✓ price  ◆ design  · copy  · publish
  now: design: waiting, a person approves the price: scripts/dev/shop.sh approve 74f4ddf92753
```

## 5. Where to look when something stops

| What | Where |
|---|---|
| this script's log | `runs/shop-<time>.log` (`SHOP_TRACE=1` for every shell command) |
| the director's progress | `runs/<run>/director.log` (server-started runs) or the shop log |
| run ledger and report | `runs/<run>/state.json`, `runs/<run>/report.md` |
| a worker's raw output | `runs/<run>/workers/` |
| an item's files | `work/<sha256>/` (`*.error.json`, `price.rejected.json`, `.lock`) |
| server and console | `runs/server.log`, `runs/frontend.log` |

Common stops:

- **price: blocked, no sellable price.** The break-even floor is above the
  market. Only an override with a reason can price it; usually the item is not
  worth selling.
- **slice: failed.** Foreign 3mf project settings; the console upload strips
  them, a folder run does not yet. Run `strip_3mf.py` on the file first.
- **design fails its captures.** The studio at :8081 is not answering; see
  `runs/frontend.log`.
- **a stage stays running.** A worker holds `.lock`; it is killed at
  `max_worker_minutes` and the item can be resumed.
