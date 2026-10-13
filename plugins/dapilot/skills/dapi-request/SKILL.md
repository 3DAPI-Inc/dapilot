---
name: dapi-request
description: Builds, validates and sends a request to any 3DAPI public API route from the live OpenAPI contract. Use whenever a skill needs to call 3DAPI (quote, poll, list orders, update status). Not for deciding what to send; the calling skill decides, this skill makes it correct and safe.
allowed-tools: Read, Bash, Write
---

# dapi-request

## When
Any time a skill needs to talk to dapi. Every 3DAPI call in this plugin goes
through `${CLAUDE_PLUGIN_ROOT}/scripts/dapi.py`; no skill assembles HTTP
requests in prose. Needs `DAPI_API_URL` and, to send, `DAPI_API_KEY`.

## Inputs
- The operation id (from `ops`), or the intent in words, which step 1 turns
  into an operation id.
- The values the caller wants to send: path params, query params, body.
- For a proposal that a person will execute: the work dir to write it to.

## Procedure
1. Find the operation. Run
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/dapi.py ops` (add `--tag "Quotes"` to
   narrow). Pick the id whose summary matches the intent. If none matches,
   stop with `ok:false, reason: "no public route for <intent>"`. Do not
   improvise a path.
2. Read its contract. Run `dapi.py describe <op>`. Note `scope`, `gate`,
   the required params, `body_schema` and `example_body`.
3. Judge the payload. Map the caller's values onto the schema fields. Use
   only fields the schema declares; the server rejects unknown ones. Where
   the caller gave nothing for a required field, stop and say which field,
   rather than inventing a value.
4. Validate without sending. Write the body to `$WORKDIR/req.<op>.json` and
   run `dapi.py validate <op> --path k=v --query k=v --body <file>`. If
   `valid` is false, fix only what the `errors` list names and validate
   again. Two failed rounds means the inputs are wrong: stop with the errors.
5. Send, or propose. If `gate` is `auto`, run `dapi.py call <op> ...`. If
   `gate` is `propose`, run the same `call` without `--confirm`: it prints
   the exact request as a dry run and exits 0. Write that output to
   `$WORKDIR/proposal.<op>.json` and return `needs_human` with the command
   the person should run, with `--confirm` appended. Never pass `--confirm`
   yourself.
6. Interpret the result. `ok:true` with `status` 2xx is success; `body` is
   the response. Otherwise read `status` and `body`: 400 is a contract
   mismatch (go back to step 3 once), 401 or 403 is a key or scope problem
   (stop, report the `scope` from step 2), 404 is a missing resource (stop),
   429 is rate limiting (stop, report `rate_limit`), 5xx is 3DAPI's problem
   (stop, do not retry here; the orchestrator owns retries).

## Outputs
- `$WORKDIR/req.<op>.json` the body that was validated.
- `$WORKDIR/res.<op>.json` the full `call` output on success or failure.
- `$WORKDIR/proposal.<op>.json` for gated operations, the dry run.
- Result line: `{"ok":..., "outputs":[...], "needs_human":..., "reason":...,
  "summary": "<METHOD> <path> -> <status>"}`.

## Gate
The gate is per operation and lives in the `GATES` table in `dapi.py`.
Mutating operations not in that table are `propose`. This skill never
changes a gate and never passes `--confirm`. Read-only calls are `auto`.

## Failure
- Missing env: the script exits 2 with the reason. Pass it through, stop.
- Unknown operation: stop. The route does not exist for us.
- `valid:false` after two fixes: stop with the error list.
- Connection failed or 5xx: stop with the reason. No retry inside this skill.

## Do not
- Do not type a URL or path. The spec is the only source of routes.
- Do not add fields the schema does not declare, even if the intent seems
  to need them. Report the gap instead.
- Do not pass `--confirm`, ever.
- Do not log or write `DAPI_API_KEY` anywhere, including `log.md`.
- Do not call `spec --refresh` on every request. The cached spec is fine
  until a 400 suggests the contract moved.

## Reference
- `reference/surfaces.md` the two API surfaces, scopes, which routes mutate.
- `reference/quote-flow.md` the intake sequence: upload, create, poll.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
