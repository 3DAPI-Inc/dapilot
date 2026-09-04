---
name: quote-model
description: Prices one model through dapi's slicer by running the bundled quote script (upload, create, poll) and judging the outcome. Use as the quote stage after analysis.json exists. The slicer's verdict on printability is final; this skill never estimates a price.
allowed-tools: Read, Bash, Write
---

# quote-model

## When
A brief with `stage: quote`. The work dir has `source.<ext>`, `meta.json`
and `analysis.json`, and no `quote.json`.

## Inputs
- `source.<ext>`, `meta.json` (the sha256 becomes the quote's name)
- `analysis.json`, read only when judging an error

## Procedure
1. If `quote.json` exists, print a `cached:true` result and stop.
2. Run, with a Bash timeout of 600000 ms because the slicer takes minutes:
   `$PY ${CLAUDE_PLUGIN_ROOT}/scripts/quote.py --workdir $WORKDIR`
   It finds an existing quote by name, or uploads and creates one, then polls.
3. Judge the outcome by the script's `status`:
   - `DONE`: `quote.json` is written. Summary: unit price, batch count if
     present, seconds waited. Result `ok:true`, outputs `["quote.json"]`.
   - `ERROR`: `quote.error.json` is written. Read its `errorMessage` and
     `analysis.json`, then decide with `reference/errors.md`:
     a file problem (unparseable, not watertight, too large for a plate)
     is final: return `ok:true`, outputs `[]`, `needs_human` set to one
     line naming the problem and asking whether to skip the model. A
     dapi-side problem (queue, timeout, internal error) is transient:
     return `ok:false` with the message as `reason`; the ledger retries once.
   - `PROCESSING` after the wait (exit 3): return `ok:false` with the
     script's reason. `quote.pending.json` stays, so the retry resumes
     polling instead of creating a second quote.
   - Any other failure (exit 1 or 2 with `reason`): `ok:false`, pass the
     reason through.
4. Append the log paragraph: quote publicId, status, price, seconds
   waited, whether it was found or created. Remove `.lock`. Print the
   result line.

## Outputs
- `quote.json` only for a DONE quote: `publicId`, `name`, `store_id`,
  `unit_price`, `batch`, timestamps, `raw` response.
- `quote.error.json` for ERROR or CANCELLED, same shape.
- `quote.pending.json` while a quote is in flight; the script owns it.

## Gate
Auto. A quote is one slicer run on dapi and counts against the daily quote
limit; the script never creates a second quote for the same file.

## Failure
- Missing `DAPI_API_KEY` or `DAPI_API_URL`: the script exits 2; pass it on.
- HTTP 401 or 403: key or scope problem; `ok:false`, do not retry here.
- HTTP 429: rate limited; `ok:false` with the message.

## Do not
- Do not estimate, guess or compute a price. `unit_price` comes from dapi.
- Do not call dapi routes yourself; only through `quote.py`.
- Do not create a quote when one is pending. The script handles resumption.
- Do not treat a slicer file error as transient to "try again". That wastes
  a slicer run and the daily limit.

`$PY` is `${DAPILOT_PYTHON:-python3}`.
