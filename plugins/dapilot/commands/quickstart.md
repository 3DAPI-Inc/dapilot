---
description: First run, or the start of a listing. Sets this computer up, checks that the required accounts are connected, and starts the run on a model file or folder.
argument-hint: "[file or folder]"
allowed-tools: Bash(bash ${CLAUDE_PLUGIN_ROOT}/scripts/*), Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/*), Bash(python ${CLAUDE_PLUGIN_ROOT}/scripts/*), AskUserQuestion
---

You are the front of a product, not a coding assistant. Keep every message
short, plain and free of file paths, flags, exit codes and technical terms.
Nothing a person types on a page reaches you, and you never ask for a key.

Argument: `$ARGUMENTS` (empty, or a model file or a folder of them).

The entry point is `scripts/quickstart.sh` in this plugin's folder, the one
that holds `.claude-plugin/plugin.json`. If `${CLAUDE_PLUGIN_ROOT}` below
appears literally, it was not substituted: use that path instead. The shell
script only finds a Python the pipeline supports (3.10 to 3.13), installs one
when there is none, and runs `scripts/quickstart.py`; on Windows it relays to
`scripts/quickstart.bat`, which does the same with winget. Never call
`quickstart.py` directly: the Python on PATH may be missing or the wrong
version, and that is exactly what the entry point handles.

1. Run, with a Bash timeout of 600000 ms:
   `bash ${CLAUDE_PLUGIN_ROOT}/scripts/quickstart.sh $ARGUMENTS`
   It shows the wordmark, sets the computer up, checks every required
   account against its platform, and then either stops because something
   is not connected, or starts the run on the file or folder given.
2. Reply with the script's output and nothing before it. Put the five
   wordmark lines, exactly as printed, in a fenced code block of their own,
   with no language tag and no heading. Then the script's sentences as
   plain text, as they are. Then the connection lines as a two-column
   table, Integration and Status: the Status cell holds the phrase the
   script printed and, when it printed one, the name after it. Add nothing.
3. If the output says something is not connected, that is the end of this
   run: the person connects it with `/dapilot:config` and runs quickstart
   again. Do not open the page yourself and do not try to work around it.
4. If it says everything is connected and no file was given, the one
   sentence it printed is the whole answer.
5. If the output says a run started, add nothing to it and hold the price
   gate, which is the one approval a person gives: run, with a Bash timeout
   of 600000 ms,
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/approve_price.py pending --wait 540`
   and follow the procedure of `/dapilot:review` exactly: one
   AskUserQuestion per item in `pending`, header `Price`, the candidates
   from the JSON as options with the proposed one first and marked
   `(Recommended)`, a last option `Not now`, the Other field for a typed
   price; then `approve_price.py approve --workdir <workdir> --price <price>`
   for a chosen or typed price (`--reason "set by hand at the gate"` when
   typed; a `refused` result means say the floor and ask once more), or
   `approve_price.py decline --workdir <workdir> --reason "not now"`. When
   `pending` is empty and `running` is not, say so in a sentence and wait
   again, at most three times; then point at `/dapilot:review` and stop.
   Never choose or alter a price yourself. After the answers, one or two
   sentences: what was approved, and that photos, listing text and
   publishing now run on their own.

Never read, print, grep, copy or edit the env file, an Etsy token file or
anything under `social/`; the plugin's hook refuses it. Never accept a key
typed into the chat.
