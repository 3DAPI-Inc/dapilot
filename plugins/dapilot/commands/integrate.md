---
description: Open the Integrations page to connect 3DAPI, Etsy, fal, Claude and Instagram; or `status` to report what is connected
argument-hint: "[status]"
allowed-tools: Bash
---

Connect the shop's accounts. Every value is entered on a local page and
verified against its platform; nothing passes through you.

Argument: `$ARGUMENTS` (empty, or `status`).

The script is `scripts/integrate.py` in this plugin's folder, the folder that
holds `.claude-plugin/plugin.json`. If `${CLAUDE_PLUGIN_ROOT}` below appears
literally, it was not substituted here: find the plugin folder and use that
path instead. The script needs only Python 3's standard library.

1. Unless the argument is `status`, run
   `${DAPILOT_PYTHON:-python3} ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py serve`
   It starts a server on 127.0.0.1, opens the page in the browser and returns
   at once with a JSON line holding `url`. Give the person that URL. The page
   lists each integration with the steps to obtain its values on the
   platform's side, one field per value, Save and test, and for Etsy an
   Authorize shop button. If the JSON says the port is busy, run it again
   with `--port 3106`. If no browser opened, or the URL does not load for the
   person, the script is running where it cannot reach their browser (a Cowork
   session, for example): tell them to run the same `serve` command in a
   terminal on their own machine, which opens the page there, and continue
   with step 2 once they say they are done.
2. When the person says they are done, or when the argument is `status`, run
   `${DAPILOT_PYTHON:-python3} ${CLAUDE_PLUGIN_ROOT}/scripts/integrate.py status`
   and show the table exactly as printed: one line per integration, connected
   or not, with the platform's non-secret summary. Exit 1 means a required
   integration is not connected; say which and point at the page.

Do not read, print, grep, copy or edit the env file, an Etsy token file or
anything under `social/`; the plugin's hook refuses it. Do not accept a key
typed into the chat: ask the person to paste it on the page, and say why (a
value in the conversation is a value in the transcript). Do not pass a value
as a script argument.
