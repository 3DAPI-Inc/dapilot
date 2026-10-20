# runner

The container that hosts the dapilot plugin for the hosted tier: the plugin
as released, the Claude Code CLI, Python with the plugin's packages, ffmpeg
and Chromium, nothing else. It is meant for ECS Fargate, and it is in this
repository because the plugin and the image that runs it are released
together: a release tag builds the image for that release, byte for byte.
It is not a Coolify worker and does not live in the 3dapi workers directory.

The whole design (queue, storage, accounts, what is built and what is not)
is `docs/hosting.md`. The rule that governs this directory: the runner never
reaches inside the plugin. It drives it through the surface every local user
has: a store directory, environment variables, the director's command line,
its exit code and the files it writes.

## What is here

- `Dockerfile`: the image. Build from the repository root; the root
  `.dockerignore` limits the context to `plugins/dapilot` and this directory.
- `doctor.sh`: the image's default command. Checks every tool, that the
  plugin is read-only, that the data directory is writable, and renders two
  frames of a cube through the bundled studio page in headless Chromium.
  `--hold` keeps the container up after the checks.
- `consumer.py`: the queue consumer, the service's real command
  (`consumer.py serve`). Per message it pulls the seller's root from S3 to
  `$DAPILOT_DATA_DIR/<seller>`, seeds the store from the plugin's templates
  on first use, writes the store's env file from the seller's credentials
  and the plan limits into its config, runs `run_intake.py` (or
  `approve_price.py` then the director, for an approval), posts one event
  per director log line, reads the outcome from the ledger and the markers,
  pushes the root back with the secrets excluded, and deletes the message.
  The plugin sees a directory and a command, nothing else; the consumer
  never imports it.
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

## Environment the task sets

```
ANTHROPIC_API_KEY        bills the workers the director spawns (DAPILOT_BILLING=api is baked in)
AWS_REGION               the queue and the bucket; credentials come from the task role, never from env
DAPILOT_QUEUE_URL        the dapilot FIFO queue (not consumed yet)
DAPILOT_BUCKET           the bucket the seller stores live in (not used yet)
DAPILOT_BACKEND_URL      where events go and per-seller credentials come from (not used yet)
DAPILOT_BACKEND_TOKEN    the runner's service token for that backend
```

Per-seller values (the 3DAPI API key, Etsy keys and token, the fal key) are
never task environment. The runner fetches them per job from the backend and
writes them into that seller's store env file, which the plugin's own hook
keeps the workers' tools away from.

## Deploying on ECS Fargate

The stack is `Dapilot-<env>` in the 3dapi CDK app
(`infrastructure/cdk/lib/dapilot-stack.ts`); the image comes from this
repository's `runner-image` workflow. First time, in this order:

1. **Deploy the stack with the doctor command** (the default). From
   `3dapi/infrastructure/cdk`: `npm install`, then
   `npx cdk deploy Dapilot-staging --context env=staging`. It creates the
   registry, the queues, the bucket, two empty secrets, the roles and a
   service that wants an image that does not exist yet, so the service shows
   as failing to start. That is expected.
2. **Set the two secrets** once:
   `aws secretsmanager put-secret-value --secret-id staging/dapilot/anthropic-api-key --secret-string sk-ant-...`
   and the same for `staging/dapilot/backend-token`.
3. **Push an image.** Either tag a release so the workflow builds it, or by
   hand from a machine with Docker: `aws ecr get-login-password | docker login ...`,
   `docker build -f runner/Dockerfile --build-arg DAPILOT_RELEASE=$(git describe --tags --always) -t <RunnerRepositoryUri>:staging .`,
   `docker push`. The workflow needs the `AWS_RUNNER_IMAGE_ROLE_ARN` secret,
   an OIDC role allowed to push to `dapilot-runner`.
4. **Watch the task.** The service pulls the image and runs `doctor.sh
   --hold`; the log group `/ecs/staging/dapilot-runner` shows one `ok` per
   check ending in "all checks passed", including a real WebGL render. A
   `FAIL` line names what the image is missing.
5. **Switch to the consumer** once the backend serves the four routes:
   `npx cdk deploy Dapilot-staging --context env=staging --context dapilotCommand=serve`.
   The task then long-polls the queue and the first message starts a run.

Production is the same with `--context env=production`, after moving the
`:production` tag onto the image staging proved.

The settings, and why:

| Setting | Value | Why |
|---|---|---|
| CPU, memory | 2 vCPU, 8 GB | a run mostly waits on the model, the slicer and fal; the bursts are Chromium rendering and up to three workers |
| Ephemeral storage | 60 GB | the seller's root is pulled whole and work dirs carry media |
| Networking | public subnets, public IP, no inbound | the VPC has no NAT; the task needs S3, SQS, ECR, Anthropic, 3DAPI, Etsy and fal |
| Capacity | one task, On-Demand | Spot after the SIGTERM path is seen to push a consistent root |
| Stop timeout | 120 s | the consumer stops the director, pushes, and leaves the message |
| Init process | on | the director and workers are child processes that get killed on their caps |
| Queue | FIFO, group = seller id, visibility 5 min, DLQ after 3 | per-seller serialisation; a dead task's message reappears |
| Bucket | private, SSL only, CORS for the dashboard origin | presigned PUT from the browser, presigned GET for renders |
| Secrets | Anthropic key, backend token, from Secrets Manager | never in the task definition's plain environment |
| Task role | consume the queue, read and write the bucket | nothing else |

## Updating the plugin in production

Release the plugin (`scripts/dev/release.sh`), build the image from that tag
and push it to the registry; the service's task definition points at the
tag. Read the changelog first: a release that changes what a stage writes
changes what the dashboard reads.
