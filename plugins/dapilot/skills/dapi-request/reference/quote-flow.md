# Quote flow (intake: slice and price one file)

Three calls and one S3 upload. Every step is `auto`.

```
1. uploadQuoteFile   POST /v1/quotes/upload_file  body {file_name?}
      -> {publicId, upload_url, s3_key, expires_in}
2. PUT the file bytes to upload_url        dapi.py upload <upload_url> <file>
      (not an API call; presigned S3, expires in expires_in seconds)
3. createQuote       POST /v1/quotes  body {s3_key, name?, webhook_metadata?}
      -> {publicId, status: PROCESSING | ERROR}
4. getQuoteByPublicId GET /v1/quotes/{publicId}   poll
      -> {status: FILE_UPLOADED | PROCESSING | DONE | CANCELLED | ERROR,
          result (price) | null, batch {max_per_plate, prices[]} | null,
          errorMessage?, createdAt, finishedAt}
```

Rules for the calling skill (`quote-model`):

- Use the model file's content hash as `name` (max 100 chars) so a quote can
  be found again with `listQuotes` and never created twice for one file.
- Before step 1, `listQuotes` and look for that name. If one exists and is
  not ERROR or CANCELLED, poll it instead of creating another.
- Poll with backoff starting at 10 seconds. A slicer run takes minutes. Stop
  polling at DONE, ERROR or CANCELLED, or after a budget the orchestrator
  sets; leave it PROCESSING and let the next pass pick it up.
- `result` is the unit price for quantity one. `batch.prices` is the price
  ladder; `max_per_plate` says how many fit on one plate.
- ERROR with `errorMessage` is the slicer's verdict on printability. That is
  authoritative. Record it, do not second-guess it locally.
