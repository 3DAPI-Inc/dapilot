# runner

The container that hosts the dapilot plugin for the hosted tier: the plugin
as released, the Claude Code CLI, Python with the plugin's packages, ffmpeg
and Chromium, nothing else. It runs as a Coolify resource on the EC2 host,
next to 3dapi's other workers, and it is in this repository rather than the
3dapi workers directory because the plugin and the image that runs it are
released together: Coolify builds the image from a release tag, so the plugin
inside it is that release, byte for byte.

The whole design (queue, storage, accounts, what is built and what is not)
is `docs/hosting.md`. The rule that governs this directory: the runner never
reaches inside the plugin. It drives it through the surface every local user
has: a store directory, environment variables, the director's command line,
its exit code and the files it writes.

## What is here

- `Dockerfile`: the image. Build from the repository root; the root
  `.dockerignore` limits the context to `plugins/dapilot` and this directory.
- `doctor.sh`: the image's default command, as `doctor.sh --serve`. Checks
  every tool, that the plugin is read-only, that the data directory is
  writable, renders two frames of a cube through the bundled studio page in
  headless Chromium, and then starts the consumer. A failed check exits
  before any message is taken. Without a flag it runs the checks and exits;
  `--hold` keeps the container up idle after them.
- `consumer.py`: the queue consumer (`consumer.py serve`). Per message it
  pulls the seller's root from S3 to
  `$DAPILOT_DATA_DIR/<seller>`, seeds the store from the plugin's templates
  on first use, writes the store's env file from the seller's credentials
  and the plan limits into its config, runs `run_intake.py` (or
  `approve_price.py` then the director for an approval, `ledger.py retry`
  then the director for a retry of a failed or blocked stage), posts one event
  per director log line, reads the outcome from the ledger and the markers,
  pushes the root back with the secrets excluded, and deletes the message.
  The plugin sees a directory and a command, nothing else; the consumer
  never imports it.
- `triage.py`: the supervisor's judgement, one class per kind of stop with
  its code, its text patterns and its policy; `triage.py selftest` checks
  the fixture table of real reasons, `triage.py classify "<reason>"` shows
  one decision. Stdlib only.
- `supervisor.py`: the second command of this image (`doctor.sh
  --supervise`, its own Coolify resource with `DAPILOT_SUPERVISE_QUEUE_URL`,
  the backend URL and token, and the runner user's key). Reads one message
  per stopped stage, looks at the run through the backend, lets triage
  decide, and answers with the backend's retry route, a delayed copy of the
  message on its own queue, or a `supervisor.left` event. Never touches the
  bucket or a store. `once --message FILE --local-backend DIR` runs one
  message against a directory: `run.json` is what the backend would answer,
  and retries, events and re-queued messages land in `retries.jsonl`,
  `events.jsonl` and `requeued.jsonl` there.
- `requirements.txt`: boto3, the AWS SDK for Python, installed into the same
  venv as the plugin's packages.

The consumer runs on a laptop with no AWS account, which is how it is tested:
a directory stands in for the bucket and another for the backend.

```sh
# a bucket with one uploaded model, a backend with the seller's values, one message
mkdir -p /tmp/c/bucket/sellers/s1/intake/r1 /tmp/c/backend /tmp/c/data
cp fixtures/multi.stl /tmp/c/bucket/sellers/s1/intake/r1/
echo '{"DAPI_API_KEY":"k","DAPI_API_URL":"http://127.0.0.1:1","ETSY_KEYSTRING":"k","ETSY_SHARED_SECRET":"s","FAL_KEY":"f"}' > /tmp/c/backend/credentials.json
echo '{"budget":{"usd_per_run":5}}' > /tmp/c/backend/limits.json
echo '{"kind":"run","seller":"s1","run":"r1","keys":["sellers/s1/intake/r1/multi.stl"]}' > /tmp/c/msg.json
DAPILOT_DATA_DIR=/tmp/c/data DAPILOT_PLUGIN_DIR=$PWD/plugins/dapilot DAPILOT_PYTHON=$PWD/.venv/bin/python ANTHROPIC_API_KEY=... \
  .venv/bin/python runner/consumer.py once --message /tmp/c/msg.json --local-bucket /tmp/c/bucket --local-backend /tmp/c/backend --director-args=--dry-run
```

`--director-args=--dry-run` makes the director plan the first pass and
launch nothing, so the whole loop runs without spending a token. Events land
in `backend/events.jsonl`, the outcome in `backend/outcome.json`, and the
pushed files under `bucket/sellers/s1/`, never the env file.

What the backend has to serve, all under a service token:

| Route | Direction | Body |
|---|---|---|
| `GET /internal/dapilot/sellers/{id}/credentials` | to the runner | the store's env values: `DAPI_API_KEY`, `DAPI_API_URL`, `ETSY_KEYSTRING`, `ETSY_SHARED_SECRET`, `FAL_KEY`, optional `etsy_token` |
| `GET /internal/dapilot/sellers/{id}/limits` | to the runner | `config.json` keys the plan sets: `budget.usd_per_run`, `max_workers_per_lead`, `max_attempts` |
| `POST /internal/dapilot/runs/{id}/events` | from the runner | `run.started`, `pass.dispatched`, `stage.result`, `gate.answered`, `stage.retried`, `run.ended`, `log` |
| `POST /internal/dapilot/runs/{id}/outcome` | from the runner | per item per stage status, open gates with candidates, failed and blocked markers, the director's summary |

## Trying the image

```sh
docker build -f runner/Dockerfile --build-arg DAPILOT_RELEASE=$(git describe --tags --always) -t dapilot-runner .
docker run --rm dapilot-runner
```

The second command prints one `ok` per check and exits 0. To run the
plugin's director by hand inside the container against a store kept on a
volume:

```sh
docker run --rm -it -v dapilot-data:/data/dapilot -e ANTHROPIC_API_KEY=... dapilot-runner sh
# inside: point the plugin at a trial store, then a dry run
export DAPILOT_STORE_DIR=/data/dapilot/trial
"$DAPILOT_PYTHON" "$DAPILOT_PLUGIN_DIR/scripts/run_intake.py" /path/to/model.stl --dry-run
```

## Environment the container gets

```
ANTHROPIC_API_KEY        bills the workers the director spawns (DAPILOT_BILLING=api is baked in)
AWS_REGION               us-east-1 (the SDK's own name, AWS_DEFAULT_REGION, is accepted too)
AWS_ACCESS_KEY_ID        an access key of the stack's <env>-dapilot-runner IAM user: consume the
AWS_SECRET_ACCESS_KEY    queue, read and write the bucket, nothing else
DAPILOT_QUEUE_URL        the dapilot FIFO queue (the stack's RunsQueueUrl output)
DAPILOT_BUCKET           the bucket the seller stores live in (SellersBucketName output)
DAPILOT_BACKEND_URL      where events go and per-seller values come from; the backend's Hono app is
                         mounted under /api, so it ends in /api: https://staging-api.dapi.digital/api
DAPILOT_BACKEND_TOKEN    the runner's service token; the backend holds the same value
```

Per-seller values (the 3DAPI API key, Etsy keys and token, the fal key) are
never container environment. The runner fetches them per job from the backend
and writes them into that seller's store env file, which the plugin's own hook
keeps the workers' tools away from.

## Deploying on Coolify

The AWS side is the stack `Dapilot-<env>` in the 3dapi CDK app
(`infrastructure/cdk/lib/dapilot-stack.ts`): the FIFO queue and its
dead-letter queue, the sellers bucket, an IAM user for the runner and grants
for the backend's user. It stands alone and imports nothing from the slicer's
stack. Staging first, production the same way afterwards.

1. **Deploy the stack, and only it.** From `3dapi/infrastructure/cdk`:
   `npm install`, `npx cdk diff Dapilot-staging --exclusively --context env=staging`,
   read it, then the same command with `deploy`. Note the `RunsQueueUrl`,
   `SellersBucketName` and `RunnerUserName` outputs. `--exclusively` matters:
   a plain deploy also deploys every stack the target depends on, from the
   branch you are on, which is how production lost resources once
   (2026-10-20).
2. **Create the runner's access key** in the IAM console for the
   `<env>-dapilot-runner` user. It is pasted into Coolify in the next step
   and nowhere else.
3. **The Coolify resource**, one instance:
   - Source: this repository, at the release tag to run. Build pack
     Dockerfile, Dockerfile location `runner/Dockerfile`, base directory `/`
     (the image copies `plugins/dapilot` from the repository root; the root
     `.dockerignore` keeps the context to that and `runner/`). Build
     argument `DAPILOT_RELEASE` = the tag.
   - No port, no domain, no health check URL. "Running" is the health check,
     and the log is the report: one `ok` per check, `all checks passed`,
     then `starting the queue consumer`. A `FAIL` line names what the image
     is missing and the container exits.
   - Environment: the table above.
   - A persistent volume at `/data/dapilot`. The container runs as uid 1000
     (`runner`); a volume created by an earlier root image needs
     `chown -R 1000:1000` once.
   - Memory limit about 3 GB on the shared host; the peaks are Chromium
     rendering stills and up to three `claude -p` workers. Watch the first
     real run; if it swaps, the runner moves to its own small instance.
4. **The backend's side.** The same `DAPILOT_QUEUE_URL`, `DAPILOT_BUCKET` and
   `DAPILOT_BACKEND_TOKEN` in that environment's backend, which already runs
   as the IAM user the stack granted. The first message starts a run.

What is deliberately not here: no Fargate task, no ECR registry, no Secrets
Manager. The first version of the stack had all three and was deployed to
production once with zero tasks; the Coolify host made them unnecessary.

## Updating the plugin in production

Release the plugin (`scripts/dev/release.sh`), then point the Coolify
resource at the new tag and redeploy; Coolify rebuilds the image from it.
Read the changelog first: a release that changes what a stage writes changes
what the dashboard reads.
