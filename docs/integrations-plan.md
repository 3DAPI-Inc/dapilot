# Integrations plan: connecting a seller's accounts in one step

**Decided 2026-10-13, after review:** the scope is reduced to what section 1
calls the `key` mechanism, plus Etsy's existing authorization. Built the same
day: `/dapilot:config` opens a local page (`scripts/integrate.py serve`)
listing the current integrations with the steps to obtain each value and one
field per value; the page posts to a local server that writes `store/.env`,
verifies each value against its platform with a free request, and records the
result in `store/integrations.json`; a connected integration shows as done.
The plugin's PreToolUse hook (`scripts/guard_secrets.py`) refuses the agent's
tools on the credential files. Nothing else in this document is scheduled:
no shared Session contract rework, no `BrowserSession`, no dapi-owned apps,
no console page. The browser adapters stay where `docs/social-plan.md` has
them. The rest of this document is kept as the analysis behind that decision.

Draft for review, 2026-10-13. Written after the
conversation of the same day and three fact-finding passes: an inventory of
the code that exists, the developer rules of six platforms as published on
2026-10-13, and what a Claude Code plugin can enforce. Sources are cited
inline where a fact comes from outside the repo.

The question this answers: a user installs dapilot and wants to connect their
Etsy shop, their Instagram, their X and the rest. Today that is a developer
portal per platform, a keystring pasted into `store/.env`, and an OAuth flow
run from a terminal. The target is one command, `/dapilot:config`, or one
console page, where every platform is a Connect button: the user approves on
the platform's consent page or signs in, and the row turns green. No key is
typed into the agent. No secret enters the model's context.

## 0. Before anything else: a token is in git

`store/etsy_token.read-only.bak.json` has been tracked since commit `f1264c7`
(2026-09-17) and is on the GitHub remote. It holds an Etsy access token and
refresh token with `listings_r shops_r`, obtained 2026-09-14. The live token
was re-issued on 2026-09-23 with a different refresh token, and the file has
no `api_key`, so the keystring and shared secret did not leak. The refresh
token in it may still be valid: Etsy's refresh tokens live 90 days and the
docs do not say a new login revokes the old one.

To do, by a person:

1. Revoke the app's access under Etsy account settings, Apps, Connected apps,
   then run `etsy_auth.py login` again.
2. `git rm --cached store/etsy_token.read-only.bak.json` and commit. The
   `.gitignore` rule on line 38 already covers the name; it did not apply
   because the file was tracked first.
3. Decide whether to rewrite history on the remote. The repo is private to
   the organisation; if it stays private, revocation is enough.

## 1. What the user sees

One page, one table. A row per platform with the account name, a status dot
and phrase (connected, needs sign-in, expiring, not connected), and one
button. The same table is what `/dapilot:config` prints and what
`integrate.py status` returns as JSON.

Connect does one of three things, and the user is not told which:

| Mechanism | What opens | What the user does | What is stored |
|---|---|---|---|
| `key` | a form on the console, or a hidden terminal prompt | pastes a key once | `store/.env` |
| `oauth` | the platform's consent page in the system browser | clicks Allow | token file in `social/sessions/<platform>/<account>.json` |
| `browser` | a headed CloakBrowser window on the login page | types username, password, 2FA | profile directory in `social/profiles/<platform>/<account>/` |

Disconnect removes the token or profile and, for `oauth`, revokes the grant
where the platform has a revoke endpoint.

## 2. Mechanism per platform

The facts that decide each row, as the developer docs stated them on
2026-10-13. The full report with URLs is in the research pass; the rows here
carry only what changes the design.

| Platform | Mechanism | Why |
|---|---|---|
| 3DAPI | `key` | API key from the 3DAPI dashboard. No OAuth exists on the public API. |
| fal | `key` | Same. Design stage only. |
| Etsy | `oauth`, PKCE on localhost, user's own Seller App | PKCE is mandatory and the token exchange takes no secret. Etsy's own Quickstart registers `http://localhost:3003/oauth/redirect`. A Seller App is approved in minutes with no review and covers the seller's own shop. The shared secret still travels in the `x-api-key` header, so it must be on the user's machine. See 2a for why not one 3DAPI app. |
| Instagram | `browser` | Publishing needs Advanced Access: App Review per permission plus Business Verification, 10 to 20 days reported, and the exchange needs the app secret, so no local flow. Our reviewed app was blocked two days after its first token with no remedy (2026-09-23). Decided then; nothing found changes it. |
| TikTok | `browser` | Two reviews (app review, then a Content Posting audit; unaudited clients post private only). The audit itself requires a human to pick privacy and consent per post, so the API never gives silent posting either. |
| X | `browser` now; `oauth` later only with a decision | PKCE public clients work with a `127.0.0.1` callback and no review. But X has had no free tier since February 2026: every post is billed to the app owner (0.015 USD, 0.20 USD with a URL). A dapi-owned app pays for every seller's posts; a user-owned app means each seller registers one and buys credits. The 2026-09-23 decision was no paid APIs. |
| LinkedIn personal | `browser` now; `oauth` possible | Member posting is self-serve with no review, but the exchange needs the client secret (a dapi-hosted exchange) and the token lives 60 days with no programmatic refresh outside the partner programme, so every seller re-consents every 60 days. The browser profile has neither problem. |
| LinkedIn company page | `browser` | Company posting needs the Community Management API: legal-entity verification, a development tier, then a screencast review. |
| Pinterest | not yet; `oauth` when wanted | Posting needs Standard access after a video review, and Pinterest's review explicitly denies apps that collect credentials or cookies, so a browser fallback would disqualify an eventual app. Decide when a Pinterest adapter is planned. |

### 2a. Etsy: one 3DAPI app or one app per seller

The one-click dream needs one dapi-owned Etsy app with Commercial Access, the
tier for "applications that other Etsy sellers will connect to". It is
granted after a Personal App review and then a manual Commercial review with
no published timeline (third parties report weeks and repeated rejections).
One Etsy specialist's refusal in March 2025 said Etsy was "not allowing these
types of usage" for "apps that rely on AI generative content, LLM tools, or
machine learning tools". dapilot is such an app. Printful connects thousands
of shops through one app, so the model works; whether Etsy grants it to an
AI listing tool is unknown and takes weeks to find out.

Proposed: ship the per-seller path now and apply for the shared app in
parallel.

- **Now.** Each seller registers a Seller App (minutes, own shop only) and
  pastes the keystring and shared secret through the `key` mechanism. Then
  Connect runs the PKCE flow with our existing `etsy_auth.py`. Two pastes
  and one Allow, with the registration form linked from the page.
- **In parallel.** Register dapilot as a Personal App, then request
  Commercial Access, with the required disclaimer and a privacy policy on
  dapi.digital. If granted, the client id ships in the plugin and the two
  pastes disappear. The shared secret for the `x-api-key` header would then
  ship too, which Etsy accepts for PKCE apps but which makes rate limits
  (about 10,000 calls a day per keystring) shared across every installed
  copy. Research alone uses hundreds of calls per item.

### 2b. What 3DAPI has to own regardless

Nothing, under the per-seller Etsy path and browser profiles everywhere else.
No hosted exchange, no client secret on a 3DAPI server, no consent page in
3DAPI's name. The first hosted piece appears only if a platform with a
confidential client (LinkedIn, Pinterest, Meta) is moved to `oauth` for
published users; then one route on the public API swaps the code for tokens
with the stored secret. Not in this plan.

## 3. Architecture

Three layers, as everywhere in the repo: `integrate.py` does, the
`integrate` skill decides nothing but which platform the user named, and the
console page is a view over the script's JSON.

### 3a. Session contract (`scripts/social/base.py`)

Keep `SessionStatus` and exit code 4. Change the contract to match the
browser-first world:

- `login(redirected_url=None) -> SessionStatus` becomes abstract. Today
  `social_post.py` probes for it with `hasattr`.
- `mechanism: str` class attribute: `key`, `oauth` or `browser`.
- `refresh()` gets a default that returns `status()`. Only token sessions
  override it. `EXPIRING` is meaningful only for them.
- `logout()` with a default that removes the token file or profile.
- `keepalive()` stays as the concrete status-then-refresh. It stops being
  anything an adapter writes.
- Two shared implementations, so an adapter writes almost nothing:
  - `OAuthSession`: PKCE and state, loopback listener on `127.0.0.1` with a
    paste fallback that works without a TTY (today the fallback is TTY-only
    and `--no-browser` only suppresses the browser), token file with
    `expires_at`, refresh when within a configured margin of expiry, revoke
    on logout. `etsy_auth.py` already contains this for Etsy; it is lifted
    out, and `etsy_auth.py` becomes the Etsy adapter's thin wrapper so the
    research and publish scripts keep working.
  - `BrowserSession`: persistent CloakBrowser profile under
    `social/profiles/<platform>/<account>/`, fixed fingerprint seed stored
    in the session JSON, scrubbed environment. `status()` loads the
    platform's home URL and looks for the logged-out marker. `login()` opens
    the same page headed and waits until the marker is gone or a timeout.
    An adapter supplies `login_url`, `home_url` and `logged_out_marker`.
- Profile directory moves from "next to the JSON" to `social/profiles/`, as
  `docs/social-plan.md` already says. `.gitignore` line 39 covers both.

### 3b. The script: `plugins/dapilot/scripts/integrate.py`

JSON out, exit code says whether it worked, runnable by hand.

```
integrate.py status   [--platform P]                 every platform's SessionStatus and mechanism
integrate.py connect   --platform P [--account A] [--redirected-url URL] [--no-browser]
integrate.py disconnect --platform P [--account A]
integrate.py set-key   --platform P                  reads the value from stdin or a hidden prompt, writes store/.env, verifies with one call
integrate.py describe  --platform P                  the steps a person does on the platform's side, as text
```

`connect` dispatches on the platform's mechanism from config. For `key` it
never takes the value as an argument: arguments and chat are transcript.
It verifies every connection with one real call (`GET /me`, `GET /users/me`,
the home page) and prints only account name, scopes and expiry.

Exit codes follow `social_post.py`: 0 ok, 1 failed, 2 usage or environment,
4 a person has to act (the browser window is waiting, the consent page was
not approved in time).

### 3c. Config

A new `integrations` block in `store/config.json`, replacing the ad hoc
`social.platforms` entries over time. The example file carries every known
platform, disabled:

```json
"integrations": {
  "etsy":      {"mechanism": "oauth",   "adapter": "etsy",          "account": "shop", "enabled": true,
                "scopes": "listings_r listings_w shops_r", "client_id_env": "ETSY_KEYSTRING"},
  "instagram": {"mechanism": "browser", "adapter": "instagram_web", "account": "shop", "enabled": false},
  "x":         {"mechanism": "browser", "adapter": "x_web",         "account": "shop", "enabled": false},
  "linkedin":  {"mechanism": "browser", "adapter": "linkedin_web",  "account": "shop", "enabled": false},
  "tiktok":    {"mechanism": "browser", "adapter": "tiktok_web",    "account": "shop", "enabled": false}
}
```

`enabled` defaults to false everywhere. Today `social_post.py` and
`publish_product.py` default it to true while `shop.sh` and `shop_inspect.py`
default it to false.

### 3d. The skill: `plugins/dapilot/skills/integrate/SKILL.md`

`disable-model-invocation: true`, `argument-hint: <platform>`. Procedure:
run `integrate.py describe` and show it, run `integrate.py connect`, run
`integrate.py status` and show the table. The skill decides nothing. It
exists so a user can type `/dapilot:config etsy` in a session and so the
behaviour is the same from the console. Absolute script paths are substituted
into the skill text, because `${CLAUDE_PLUGIN_ROOT}` is not exported to Bash
commands (documented; it explains the empty variable in the headless workers
on 2026-09-13).

### 3e. Console and server

`GET /integrations` returns `integrate.py status`. `POST
/integrations/{platform}/connect` starts `integrate.py connect` as a
subprocess and returns the URL to open, or that a window was opened. `POST
/integrations/{platform}/key` takes the value in the request body from a
form on the page and pipes it to `set-key` on stdin; it never logs it.
`DELETE /integrations/{platform}` disconnects. The page is a new route,
`/integrations`, in the site nav, built the way the catalogue is: a table,
status as dot plus phrase, one button per row. The Social tab on the job page
links to it instead of listing sessions read-only.

### 3f. Where secrets live and who can read them

Three places, all under `$DAPILOT_STORE_DIR`, all gitignored today:

| Secret | File | Written by | Read by |
|---|---|---|---|
| static keys (3DAPI, fal, Etsy keystring and shared secret) | `store/.env` | `set-key`, or the person | `run_intake.py`, `server/app.py`, `shop.sh`, scripts with `--env` |
| OAuth tokens | `social/sessions/<platform>/<account>.json` | `OAuthSession.save`, mode 0600, atomic | the adapter |
| browser profiles | `social/profiles/<platform>/<account>/` | CloakBrowser | CloakBrowser, scrubbed env |

The model must not be able to read any of them. What a plugin can enforce,
per the Claude Code docs on 2026-10-13:

- **A plugin cannot ship permission deny rules.** Its `settings.json` honours
  only `agent` and `subagentStatusLine`. Deny rules come from user, project
  or managed settings.
- **A plugin can ship a `PreToolUse` hook**, in `hooks/hooks.json`, and
  hooks run before permission modes, including bypass. The hook blocks Read,
  Edit, Grep and Glob on the three locations and Bash commands that name
  them, by exit 2 with a reason. This is the layer we ship.
- **Deny rules and hooks do not stop a script that opens the file itself.**
  The docs say so; only the OS sandbox does, and the sandbox's read denial
  also blocks `run_intake.py` sourcing `.env`. So the enforced layer is a
  guard against the model reading secrets with its tools, not a guarantee
  against a script it writes. The guarantee lives on the platform side:
  scoped 3DAPI keys, revocable grants, and nothing in the files a person
  cannot revoke.
- `integrate.py` also writes the same deny rules into the user's
  `~/.claude/settings.json` on first run, with the person's approval, so the
  built-in Read tool refuses the paths even if the plugin is disabled. Paths
  are absolute or `~/`-anchored; `./store/.env` protects nothing when the
  session starts elsewhere.
- Nothing prints a secret. Today `etsy_auth.py token` prints the raw access
  token and `shop_inspect.py` prints a secret that matches the placeholder
  pattern. Both go.

### 3g. Cowork

The user intends to run dapilot from Claude Cowork. What the docs confirm:
Cowork installs plugins with skills, hooks and MCP servers, does not read the
CLI's `~/.claude` directory, and refuses a plugin with a `bin/` directory.
What they do not confirm: that a script Cowork runs can open the system
browser, bind a loopback port, or show a headed browser window a person can
type into. In the CLI's own sandbox, loopback listeners need
`network.allowLocalBinding` and `open` fails unless excluded. Hooks and MCP
servers run outside the sandbox.

So the integrate flow has two possible homes, and a test decides:

1. **Scripts, as everything else in the repo.** Works in the CLI today
   (sandbox off or configured). Unknown in Cowork.
2. **A plugin MCP server** (`dapilot-connect`) that runs the loopback
   listener and launches the headed window outside the sandbox, exposing
   `status`, `connect`, `disconnect` and nothing that returns a token. The
   scripts stay the implementation; the server is a thin host for them.

Step 0 of the build order is a ten-minute test in Cowork: can a script open a
URL and catch a loopback redirect, and can it show a headed Chrome window.
Until that is run, nothing here should assume Cowork can do either.

**I0 result, 2026-10-13.** Run from the uploaded plugin in a Cowork task.
The session answered: "This session still can't run commands on your Mac ...
your keys are saved there and not in this cloud session." So a cloud Cowork
session runs no script on the machine at all: no server, no browser, no
hook-guarded files, nothing. The plugin's `integrate` command therefore tells
the person to run `integrate.py serve` and `status` in a terminal and paste
the table back. The docs mention Cowork sessions "that run on your computer in
the desktop app" as the case where local MCP servers load; whether such a
session can bind a port and open a browser is still untested. For a cloud
session the only in-app route is an MCP App backed by a hosted server, which
is the connector architecture, not this plugin.

## 4. Fixes found along the way

Independent of the plan, found during the inventory. Each is small.

- `publish_product.py` treats exit 4 from `social_post.py` as exit 1: the
  "person must sign in" help text is dropped and the step is recorded as
  failed. It should record `needs_login` with the help text so the console
  can show Connect.
- `frontend/src/api/dapi.ts` still reads `VITE_DAPI_API_KEY` into the bundle.
  Only a type import uses the file. Remove the key read.
- `social_post.py session` exits 2 when every platform is disabled, so the
  Social tab's session list is always empty today.
- `etsy_search.py bearer_token` swallows a failed refresh and silently skips
  the inventory pass. It should report `needs_login`.
- The README's env section omits `INSTAGRAM_*` and
  `DAPILOT_SOCIAL_ADAPTER_DIR`, says Etsy is research-only, and its script
  table omits `social_post`, `publish_product`, `etsy_listing`,
  `listing_copy` and `product_record`.
- `docs/social-plan.md` and the code disagree on the adapter directory, the
  token path, the profile path, the exit codes and what `adapter` means.
  The plan is updated to the code when this plan is accepted.
- `scripts/etsy_auth.py` and `scripts/etsy_search.py` at the repo root are
  stale copies of the plugin's. Delete with approval.

## 5. Build order

Each step is a commit with a done test. Estimates are for one person.

| Step | What | Done when | Size |
|---|---|---|---|
| I0 | Cowork test: open a URL, catch a loopback redirect, show a headed Chrome window from a plugin script | a written yes or no for each, in this document | hours |
| I1 | Token hygiene: revoke and untrack the committed token, remove the browser key read, stop `etsy_auth.py token` printing | `git ls-files store/` shows only example files; `grep -r VITE_DAPI_API_KEY frontend/src` is empty | hours |
| I2 | Contract: `login` and `logout` abstract, `mechanism`, default `refresh`; `OAuthSession` lifted from `etsy_auth.py`; `etsy` adapter; `enabled` defaults false everywhere | `etsy_auth.py login|refresh|whoami` behave as before through the new class; `social_post.py session status` lists Etsy | 1 day |
| I3 | `integrate.py` with `status`, `connect`, `disconnect`, `set-key`, `describe`; the `integrations` config block; the `integrate` skill | from a fresh `store/`, `/dapilot:config etsy` ends with a green row and `etsy_listing.py shop` works, with no key in the transcript | 1 day |
| I4 | Hook guard in `hooks/hooks.json` plus the user-scope deny rules written by `integrate.py`; probe script that tries to read each protected path with each tool and expects refusal | the probe passes in a plain session and in a `claude -p` worker | half a day |
| I5 | `BrowserSession` on CloakBrowser (pinned version, signature check, one concurrent session, scrubbed env, seed in the session JSON), with `instagram_web` as the first adapter: `login_url`, `home_url`, `logged_out_marker`, and the existing web uploader flow from the social plan | `integrate.py connect --platform instagram` opens a window, a person signs in, `status` says ok; `status` on a logged-out profile exits 4 | 2 to 3 days, the posting flow dominates |
| I6 | Console: `/integrations` page and the four routes; `publish_product.py` records `needs_login`; the Social tab links to the page | every row's Connect works from the browser; a disconnected Instagram shows as needs sign-in on the job page instead of a failed step | 1 day |
| I7 | `x_web`, `linkedin_web`, `tiktok_web` adapters on `BrowserSession`, one at a time, each the three strings plus `submit` | one post live and verified per platform; a killed run does not double post | 1 to 2 days each |
| I8 | Etsy shared app: register Personal App, request Commercial Access, host the disclaimer and privacy policy on dapi.digital; on grant, ship the client id and drop the two pastes | Etsy's decision, in this document, with the date | weeks of waiting, hours of work |

I5 and I7 are the social plan's S2 to S4 under new names; this plan does not
add adapters, it gives them one login and one status.

## 6. Risks accepted

- A browser profile is a credential as strong as a password. It lives under
  the store dir with the rest; losing the laptop loses it. Same as today.
- Selector drift on every browser platform, every few weeks. Known, in the
  social plan.
- Instagram challenges fresh accounts on fresh browsers. The `describe`
  text for Instagram says to use the account by hand for a few days first.
  Nothing in code shortens this.
- The hook guard is a fence against the model's own tools, not against code
  the model writes. Stated in 3f; the real guarantee is revocability.
- Etsy may refuse Commercial Access to an AI tool. The per-seller path does
  not depend on it.

## 7. Open before I2

1. Etsy for published users: per-seller Seller App now and apply for the
   shared app in parallel, as proposed in 2a, or wait for Etsy's answer?
2. X: browser, consistent with no paid APIs, or let a seller bring their own
   X app and pay per post?
3. Cowork: if I0 says no to the headed window, is the integrate flow built
   as a plugin MCP server, or is Cowork out of scope for connecting accounts
   and the CLI or console does it?
4. Does the `integrations` block replace `social.platforms` in one commit,
   or do both exist for a while?
5. History rewrite for the committed token, or revoke only?
