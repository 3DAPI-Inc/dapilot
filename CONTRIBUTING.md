# Contributing

Thanks for looking. This is how the repository is put together and what a
change needs before it is merged.

## The one rule

Scripts do, skills decide, the orchestrator routes. Anything that can be a
script is a script. A skill makes one decision in one shop process. If logic
only exists inside a prompt, it cannot be tested and cannot be moved.
`CLAUDE.md` has the full contract; `docs/skills.md` says how a skill is written
and judged.

## Layout

```
plugins/dapilot/      the plugin: the thing that ships
  commands/           typed slash commands (/dapilot:quickstart, /dapilot:config)
  skills/             one decision each
  agents/             who runs what
  scripts/            standard-library Python wherever possible; the pipeline's tools
  templates/          what a new store is seeded from
  hooks/              the credential guard
docs/                 how and why
server/, frontend/    the console (optional, for a checkout)
scripts/dev/          setting a checkout up, releasing, packing
```

## Before you open a pull request

- `claude plugin validate plugins/dapilot` passes.
- Every script you touched still runs by hand with `--help` and does what its
  docstring says.
- A change to the plugin moves its version: `scripts/dev/release.sh`. The CI
  checks this, because Claude Code only delivers an update when the version
  changes.
- Nothing in the diff is a credential, a token file, a store or a run.
- Prose says 3DAPI, not dapi; identifiers keep `dapi`.
- No em dashes in anything a person reads.

## Reporting a problem

Open an issue with the template. For anything touching keys or accounts, see
[SECURITY.md](SECURITY.md) instead.
