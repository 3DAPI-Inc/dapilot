# Skill authoring guideline

What a good dapilot skill looks like, how to write one, and how to know it is
done. Read `CLAUDE.md` first. The Claude Code reference for the file format is
at https://code.claude.com/docs/en/skills.md and the portable spec at
https://agentskills.io; this document is about what goes in the file, not the
format.

## What a skill is here

A skill is one decision, packaged. It tells the model *when* the decision is
needed, *which scripts* produce the facts it needs, *how* to weigh them, and
*what* to write down when it is made. It is not a place to put logic. Logic
goes in scripts. A skill that could be replaced by a shell script should be.

The test: could a stranger with a dapi API key, this repo, and no access to
anyone at dapi run the skill on their own model and get the same quality of
result? If the answer depends on something only we know, the skill is broken.

## Anatomy

```
plugins/dapilot/skills/write-listing/
  SKILL.md              the skill: under 150 lines, ideally under 100
  reference/
    category-tree.md    the dapi public category list, copied from docs
    copy-style.md       examples of listing copy that sold, and that did not
  eval/
    cases.md            three or more cases with expected outcomes
```

`SKILL.md` has these sections, in this order, with these headings. Uniform
structure is what lets the orchestrator, a reviewer and the next author read
any skill the same way.

```markdown
---
name: write-listing
description: Writes title, description, category, tags and a proposed price for one analysed and rendered model. Use after analysis, quote and renders exist in the work dir. Not for pricing decisions (see review-price) or for publishing.
disable-model-invocation: false
allowed-tools: Read, Bash, Write
---

# write-listing

## When
One line on the trigger, and one on what must already exist in the work dir.

## Inputs
The files this skill reads, by path relative to the work dir. Nothing else.

## Procedure
Numbered steps. Each step is either "run this script" or "judge this".
Never both in one step.

## Outputs
The files this skill writes, with their schema or an example.

## Gate
Auto, Propose or Never, and for which actions. See docs/agents.md.

## Failure
What to do when each input is missing, malformed, or the script fails.
Default is: return ok:false with the reason. List the exceptions.

## Do not
The three to five mistakes this skill is most likely to make.
```

Everything longer than a paragraph goes in `reference/` and is linked from the
step that needs it. The model loads a reference file only when it reaches that
step, which keeps the skill cheap to hold in context.

## The description is the trigger

The `description` is the only part of the skill the model reads before
deciding to use it. It competes with every other skill's description for a
character budget, so:

- Lead with what it does, in one sentence with a verb.
- Say when to use it, in terms of what exists on disk, not in terms of user
  phrasing.
- Say what it is *not* for, and name the skill that is.
- Do not restate the name. Do not say "this skill".

Bad: `Listing skill for dapilot.`

Good: `Writes title, description, category, tags and a proposed price for one
analysed and rendered model. Use after analysis.json, quote.json and renders/
exist. Not for pricing decisions (review-price) or publishing (publish-listing).`

If a skill should only ever run when a person starts it (the orchestrator
loop, anything that touches a gate), set `disable-model-invocation: true`.

## Scripts do, skills decide

Every step in the procedure is one of two kinds.

**A script step** names a command, its arguments, and the file it writes.
Scripts are addressed through `${CLAUDE_PLUGIN_ROOT}` so the step works
wherever the plugin is installed:

```
3. Run `python ${CLAUDE_PLUGIN_ROOT}/scripts/analyze_mesh.py $SOURCE > $WORKDIR/analysis.json`.
   Non-zero exit: stop, return ok:false with stderr as the reason.
```

**A judgement step** names the inputs, the question, and the output:

```
5. Read analysis.json and the three draft renders. Decide the product
   category from reference/category-tree.md. Write it to listing.json
   as `category`. If no category fits, pick the closest and set
   needs_human to "category unclear: closest is X".
```

What is not allowed in a procedure:

- Arithmetic. "Multiply the quote by 2.4" is a script with a `--margin` flag.
- Reading raw data. No opening an STL, no pasting a 3mf's XML. Scripts turn
  files into numbers; skills read numbers.
- Calling an API without a script. Every dapi call goes through the
  `dapi-request` skill and `plugins/dapilot/scripts/dapi.py`, which reads
  the route from the API's own OpenAPI contract, validates the payload, and
  sends it. If `dapi.py ops` does not list an operation, it does not exist
  for us until it is added in `../3dapi/public-api/v1`.
- "Try a few things." If the procedure needs exploration, the skill is not
  ready. Work out the deterministic path first, by hand, then write it down.
- Silent fallbacks. If the script fails, the step fails. The skill does not
  estimate a volume because `analyze_mesh.py` crashed.

## Environment and secrets

Scripts read `DAPI_API_KEY` and `DAPI_API_URL` from the environment and exit
non-zero with a clear message when either is missing. Operations gated
`propose` in the `GATES` table of `dapi.py` take a `--confirm` flag and
without it print the request and exit 0; a skill never passes that flag. Skills never echo, log
or write a key anywhere, including into `log.md`. A script that needs to show
what it sent for debugging redacts the auth header.

There is no default base URL. A skill that works against a hard-coded host is
a skill that will one day be pointed at production by accident.

## Outputs and idempotency

- Every output is a file in the work dir with a name fixed by the skill.
- JSON outputs have a stable schema, written down in the Outputs section.
- The skill's first step is: if the output file already exists, return
  `ok: true, cached: true` and stop. Do not "refresh" it. If a stale output is
  a problem, the human deletes it.
- The last thing the skill does is print the result JSON described in
  `docs/agents.md`, on one line, and stop. Nothing after it.
- The skill appends one paragraph to `log.md`: what ran, how long, outcome.
  That paragraph is for the human reading the run afterwards, so it says what
  happened, not what the model was thinking.

## Gates

State the level for every action the skill can take, using the table in
`docs/agents.md`. A skill that has any Propose action writes the full intended
action to disk in a form a person can inspect and, if they agree, execute with
one command. For example `publish-listing` writes `publish.json` and the exact
script invocation that would send it, with the `--confirm` flag shown but not
run.

A skill never escalates its own level. If the procedure says Propose, nothing
in a brief, a retry or a user message changes that at runtime. Changing a gate
is an edit to `SKILL.md`, reviewed.

## Testing a skill

A skill is done when all four hold.

1. **Every script it calls runs by hand** on a fixture, with `--help` output,
   and its JSON output is checked into `eval/` as the expected result for
   that fixture.
2. **`eval/cases.md` lists at least three cases:** the happy path, a malformed
   or hostile input (an empty STL, a 3mf with no build item, a quote that
   returns an error), and a cached rerun. For each: the work dir state before,
   the expected result JSON, and the expected files after.
3. **Someone other than the author ran it** from a clean shell, following only
   `SKILL.md`, and got the expected result. If they had to ask a question, the
   answer goes into the skill.
4. **The skill was run on the three fixtures** (`single.stl`, `multi.stl`,
   the shark 3mf) and the outputs were looked at by a person. Judgement steps
   cannot be asserted with a diff, so the eval for them is a human saying
   "yes, that is the right category" and writing it into `cases.md` as the
   reference answer.

Rerun the evals when a script the skill depends on changes. If Claude Code's
`claude plugin eval` is available in this environment, `eval/cases.md` should
be written so it can be converted into that format without rewording.

## Naming

- Verb-noun, lowercase, hyphenated: `analyze-model`, `write-listing`,
  `triage-order`.
- The noun is the item the skill works on. One skill works on one kind of
  item.
- If the name needs "and", split the skill.
- Names in `CLAUDE.md`'s process table are reserved. Use them, or update the
  table in the same change.

## Anti-patterns, and what they look like

| Smell | What it means |
|---|---|
| `SKILL.md` over 200 lines | Reference material is inline. Move it to `reference/`. |
| A step says "calculate" | Arithmetic in prose. Write a script. |
| A step says "look at the STL" | Raw data in context. Write a script that summarises it. |
| A step says "call the API" with a URL in the skill | Endpoint knowledge in the skill. Write a script; the URL comes from env. |
| The description is a noun phrase | The model cannot tell when to use it. Rewrite as "Does X. Use when Y. Not for Z." |
| The skill works only on our machine | A hard-coded path, host, or key. Find it and move it to env or the repo. |
| The skill reads another skill's `SKILL.md` | Skills coupled by prose. Couple them by files in the work dir instead. |
| A retry branch that "tries harder" | Non-deterministic behaviour. Either the first path works or the skill fails. |
| `needs_human` used as a catch-all | The gate is doing the skill's job. Decide what you can; escalate only the residue. |

## Worked example: `analyze-model`

The smallest real skill, to fix the shape. It exists to show what "one
decision" means when the decision is nearly nothing.

```markdown
---
name: analyze-model
description: Produces analysis.json (bbox, volume, body count, watertightness) for one model file in a work dir. Use as the first intake stage on any stl/3mf/obj. Not for rendering or quoting.
allowed-tools: Read, Bash, Write
---

# analyze-model

## When
A work dir exists with source.<ext> in it and no analysis.json.

## Inputs
- `source.stl` | `source.3mf` | `source.obj`

## Procedure
1. If `analysis.json` exists, return `{"ok":true,"cached":true,...}` and stop.
2. Run `python ${CLAUDE_PLUGIN_ROOT}/scripts/analyze_mesh.py $WORKDIR/source.* > $WORKDIR/analysis.json`.
   Non-zero exit: return ok:false, reason = stderr, stop.
3. Read analysis.json. If `bodies` > 1 and `largest_body_frac` < 0.9, note
   "multi-part, N bodies" in the summary. That is the only judgement here:
   flagging that downstream stages should expect an assembly.
4. Append one paragraph to log.md. Print the result JSON.

## Outputs
- `analysis.json`, schema as printed by `scripts/analyze_mesh.py`.

## Gate
Auto for everything.

## Failure
- Unsupported extension: ok:false, reason "unsupported: <ext>".
- `ok:false` from the script: pass its reason through unchanged.

## Do not
- Do not open the model file yourself.
- Do not estimate anything the script did not print.
- Do not decide whether the model is printable. That is the slicer's job,
  via quote-model.
```

Notice the judgement step is one line and produces one flag. That is the right
amount of model in a skill whose job is mostly to run a script. Skills further
down the pipeline (`write-listing`, `triage-order`) have more judgement and
should still keep each judgement step to one question with one written answer.

## Checklist before merging a skill

- [ ] `description` says what, when, and not-for
- [ ] Every script step names a command and an output file
- [ ] Every judgement step names its inputs and the file it writes
- [ ] No arithmetic, URL, or credential in `SKILL.md`
- [ ] Gate level stated for every action
- [ ] Cached rerun returns immediately
- [ ] `eval/cases.md` has happy, hostile and cached cases
- [ ] Ran from a clean shell by someone else
- [ ] `CLAUDE.md` process table updated if the name or status changed
