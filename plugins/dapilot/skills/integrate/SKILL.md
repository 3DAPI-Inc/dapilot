---
name: integrate
description: Opens the Integrations page, where a person connects the shop's accounts (3DAPI, Etsy, fal, Claude, Instagram) and every value is verified against its platform. Use when a credential is missing, rejected or expired, or on a fresh machine. Never for reading or setting a value yourself.
disable-model-invocation: true
allowed-tools: Bash
argument-hint: "[status]"
---

# integrate

## When
A person asks to connect or check an account, a script exits 2 for a missing
credential, or a platform rejects a key. Also the first thing on a new
machine. With the argument `status`, only report; do not open the page.

## Inputs
None from the work dir. The store dir (`$DAPILOT_STORE_DIR`, else
`./store`) holds the env file and `integrations.json`; this skill touches
neither directly.

## Procedure
1. If the argument is not `status`, run
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py serve`
   It starts a local server on 127.0.0.1, opens the page in the browser, and
   returns at once with the URL. Give the URL to the person. The page shows
   every integration, the steps to get each value on the platform's side,
   one field per value, and Save and test. Values go from the browser to the
   server and into the env file. The server verifies each one with a free
   request and shows the result. Etsy also has Authorize shop, which opens
   Etsy's consent page.
2. When the person says they are done, or for `status`, run
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py status`
   and show its table as it is: one line per integration, connected or not,
   with the platform's non-secret summary (app id, shop name, account).
3. If a required integration is not connected, say which and point at the
   page again. Exit 1 from `status` means at least one required one is not.

## Outputs
- `$DAPILOT_STORE_DIR/.env`, written by the server only.
- `$DAPILOT_STORE_DIR/integrations.json`: per integration `ok`, `summary`,
  `checked_at`, which fields verified. No values.

## Gate
Auto. Nothing here spends money or changes platform state; every
verification is a read or a free call.

## Failure
- `serve` exits 2 with "port in use": pass `--port <n>` and retry once.
- A check says "no connection": the machine is offline or the origin is
  wrong. Report it; do not retry in a loop.
- A check says "rejected": the person pastes a new value on the page. You
  cannot fix it.

## Do not
- Do not read, print, grep, copy or edit the env file, an Etsy token file or
  anything under `social/`. The plugin's hook refuses it, and the reason is
  that a value in your context is a value in the transcript.
- Do not accept a key typed into the chat. Ask the person to paste it on
  the page instead, and say why.
- Do not pass a value as a script argument or an environment assignment on
  the command line.
- Do not open the page on your own initiative during a pipeline run; a
  missing credential is reported, and a person decides.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
