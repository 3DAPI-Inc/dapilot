# Judging a quote ERROR

The slicer's message is authoritative about the file. Your job is only to
sort it into one of two bins. When unsure, choose "file problem": a blocked
item costs nothing, a wasted slicer run costs a quota slot.

| Bin | Result | Typical messages |
|---|---|---|
| File problem (final) | `ok:true`, `needs_human`: "<message>; analysis says <bodies> bodies, <watertight_bodies> closed. Skip this model?" | "can not be parsed", "no valid geometry", "not watertight", "exceeds the build plate", "empty mesh", "unsupported format", anything naming the model or its geometry |
| 3DAPI problem (transient) | `ok:false`, `reason`: the message | "timed out", "queue", "internal error", "try again", "temporarily", "service unavailable", "could not complete the quote" with no mention of the file |

Use `analysis.json` to sharpen the question: a file the analysis already
showed as non-watertight with open bodies is a file problem even if the
slicer's message is vague.
