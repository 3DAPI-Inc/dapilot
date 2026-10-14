---
name: output-format
description: The exact shape of what every dapilot agent produces, the one-line result JSON, the log paragraph, and the report section, plus the validator that checks it. Loaded into every agent. Your superior rejects anything else.
user-invocable: false
---

# output-format

## The result line

The last thing you print is one JSON object on one line, and then nothing.
Every field is required. `null` is allowed only where shown.

```json
{"ok": true, "cached": false, "run": "<run id>", "item": "<sha256>", "stage": "analyze",
 "agent": "catalogue-worker", "department": "catalogue",
 "outputs": ["analysis.json"], "needs_human": null, "reason": null,
 "summary": "8 bodies, all solid, 29.4 cm3, 152 x 14 x 12 mm"}
```

- `ok` true means the stage completed. false means it did not; `reason`
  says why in one sentence naming what was run and what came back.
- `cached` true means the output already existed and you did nothing.
- `run`, `item`, `stage`, `department` are copied from the brief verbatim.
  `agent` is your agent name.
- `outputs` are paths relative to the work dir that now exist. Empty when
  `ok` is false or `needs_human` is set.
- `needs_human` is null, or one question a person can answer with yes, no,
  or one value. When set, `ok` is true and the stage will be marked blocked.
- `summary` is one line, at most 200 characters, and goes into the report
  verbatim. Numbers and facts, not narration.

Check it before printing:

```
echo '<the json>' | $PY ${CLAUDE_PLUGIN_ROOT}/scripts/validate_result.py --workdir $WORKDIR
```

Leads run the same check with `--expect item=... stage=...` on every result
they receive, and `ledger.py record` runs it again. A result that fails is
recorded as rejected, not repaired.

## The log paragraph

Append one paragraph to `$WORKDIR/log.md` per stage, before the result line:

```
## analyze  2026-09-03T14:02:11Z  catalogue-worker  attempt 1
Ran analyze_mesh.py on source.stl (1.1s). 8 bodies, all watertight, volume
29.4 cm3, bbox 152 x 14 x 12 mm. Flagged multi-part. Wrote analysis.json.
```

What ran, how long, what came out, what was written. Not what you thought.

## The report section (leads)

A lead's final message is its section of the run report, produced by
`ledger.py report`, followed by its own result line whose `item` is `-` and
`stage` is `lead`. The director does not read anything else from a lead.
