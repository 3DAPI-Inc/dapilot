# User flow plan: from install to a listing without reading a log

Draft for review, 2026-10-14. What a seller does, what they see, and what has
to change so the model is the interface and the scripts do the waiting.

## 1. The problem as observed

On 2026-10-14 the first end-to-end run through chat went like this: the
person started the intake from a command, the run was long, so it had to be
started in the background; nothing came back on its own; the person asked
for the status and learned the run had stopped at the price gate; the gate
itself lives on the console's job page, not in the chat. Every step worked.
None of it felt like a product.

Three faults, each with its own fix:

1. **Pull instead of push.** The person asked what was happening. The model
   should say it, the moment there is something to say.
2. **A gate outside the conversation.** Approving the price means opening
   the console. The question should arrive in the chat with the numbers,
   and the answer should go back the same way.
3. **Developer output.** Stage names, hashes, run ids, exit codes.

## 2. Principles

- **One command per intent.** `quickstart` to set up, `sell` to list a
  model, `status` to see where things are. Nothing else is typed.
- **Scripts wait, the model relays.** A command runs one script that does
  the whole job or runs out of time politely; the model shows its output.
  The model never polls on its own initiative and never narrates stages.
- **Long work runs in the background and announces itself.** Claude Code
  re-invokes the model when a background command ends, so the model reports
  without being asked. Nothing longer than a few minutes runs in the
  foreground.
- **A gate is a question in the chat.** The price proposal is a short
  message with the number and the two or three choices. The answer is a
  word or a price. The script enforces the floor, as it does today.
- **The console is for power users.** Everything a seller needs happens in
  the chat. The console keeps the detail.

## 3. The flow a seller sees

```
/dapilot:quickstart                  once per computer
   wordmark, setup, the Integrations page, the connection table

/dapilot:sell <file or folder>       per model
   "Looking at cat-clicker.3mf: 1 part, 42 x 30 x 18 mm, prints in 48 minutes."
   "Comparable listings on Etsy sell between 4.99 and 12.00. Cost per unit 2.10."
   "Proposed price: 7.99 for a single. Approve, or give a price (not below 4.49)."
      -> person: "approve"   (or "9.99", or "stop")
   "Listing in progress: photos, text, Etsy. I'll tell you when it's live."
      ... later, unprompted ...
   "Live on Etsy: <url>. Posted to Instagram."

/dapilot:status                      any time
   one line per model: where it is, or what it is waiting for
```

Every line above is a script's output, relayed. The model adds nothing.

## 4. What has to change

| Piece | Today | Change |
|---|---|---|
| Intake entry | `run-intake` skill (a model director, expensive) and `run_intake.py` (code) | `commands/sell.md` runs `run_intake.py` only. The model director is not used by sellers |
| Intake duration | analyze, quote, preview, research, price: several minutes; design up to 40 | `sell` runs the pre-gate stages in the foreground with a time budget and exits at the gate or at the budget with a clear line. Post-gate stages run in the background; the completion notification is the report |
| Progress | `report.md` and `director.log` in `runs/<id>/` | `run_intake.py --plain`: one human line per stage as it completes, no hashes, no ids; the existing files stay for the console |
| The price gate | `POST /jobs/{id}/price` on the job server, which runs the floor check, files the product and starts the next director | `approve_price.py`: the same steps as a script, no server needed. `sell` prints the proposal; the model asks; the answer goes to the script; the floor still refuses |
| Status | `shop.sh status`, `GET /jobs` | `shop_status.py --plain`: one line per item, in words. `commands/status.md` relays it |
| Completion | the person asks | background task ends, model is re-invoked, relays the last lines: the listing URL, or the failure in one sentence and what to do |
| Failure | `<stage>.failed.json` with a reason, retry on the job page | the same marker; `sell` says "stopped at research: <reason>. Say retry to try again" and `retry` clears it, as the job page does |

## 5. Time budgets and where things run

The Bash tool allows ten minutes per call. Measured so far: analyze and quote
about a minute, preview about half a minute, research and price several
minutes each on a real item, design up to forty minutes, copy a few minutes,
publish under a minute.

- `sell` foreground: budget nine minutes. It ends at the gate (the normal
  case), or at the budget with "still working on research; I'll continue",
  in which case the model runs it again in the background and the next
  message arrives on its own.
- after approval: background, one command, from design to publish. The
  model reports when it ends. If the person asks in the meantime, `status`
  answers.
- in Cowork and in the desktop app's Code tab the same commands apply.
  Background completion notifications exist in Claude Code; whether a local
  Cowork session delivers them the same way is to be tested, like the
  browser launch was.

## 6. Output rules, for every command

The same rules `quickstart` already follows:

- Say what a person would say: the file's name, size in millimetres, print
  time in minutes, prices in the shop's currency. No stage names, hashes,
  run ids, paths or exit codes.
- One line per event. No lists of next steps. No explanations unless
  something failed, and then one sentence plus the one thing to do.
- The model shows the script's lines and adds at most one sentence.

## 7. Build order

| Step | What | Done when |
|---|---|---|
| F1 | `run_intake.py --plain` and a stage line table (one sentence per stage, filled from the stage's own output: analysis, quote, viability, price) | a run prints the lines in section 3 and nothing else |
| F2 | `approve_price.py`: floor check, product record, the post-gate director, from the command line | approving from a terminal does what the job page does |
| F3 | `commands/sell.md` with the foreground budget, the gate question and the background continuation | one model, from `/dapilot:sell` to "Live on Etsy", with one question in between and no status request |
| F4 | `shop_status.py --plain` and `commands/status.md` | `/dapilot:status` answers in one line per item |
| F5 | `retry` from the chat | a failed stage is cleared with one word |
| F6 | Cowork test: does a local session deliver the background completion | yes or no, written here |

Not in this plan: the console, the social adapters, the pipeline stages
themselves. They stay as they are. This plan changes who waits and who
speaks.

## 8. Open before F1

1. Currency and units in the plain lines: the shop's, from the pricing
   policy, or always USD as the quote comes?
2. When the person gives a price below the floor, the script refuses. Does
   `sell` offer the floor as the price, or ask again?
3. Does `/dapilot:sell` with a folder run every file, with one question per
   model, or stop after the first gate?
