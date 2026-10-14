# Social plan: posting the catalogue to Instagram, TikTok, X and LinkedIn

The construction plan for the promotion process. Decided 2026-09-21: a
platform is driven through its official API only where that API is free and
needs no approval for our own account; everywhere else a CloakBrowser session
drives the platform's own web uploader. No paid API, no aggregator.
`docs/store-plan.md` and `docs/agents.md` still hold: scripts do, skills
decide, state on disk by content hash, nothing posted twice.

**Revised 2026-09-23: Instagram goes through the browser too.** The Meta
route worked for two days. On the third, `/me` answered `API access blocked`
(OAuthException 200) with the token 59 days from expiry and no change on our
side; the only remedy is a person in the Meta dashboard, and there is no
telling when it happens again. A route that can be pulled without notice
cannot carry the shop's posting, so Instagram joins TikTok, X and LinkedIn on
CloakBrowser: `instagram_web` in `DAPILOT_SOCIAL_ADAPTER_DIR`, the same
`Integration` and `Session` contracts, the web uploader with the persistent
profile. `instagram_api.py` stays in the plugin for anyone whose app Meta
leaves alone; in our config it is disabled until `instagram_web` exists.

## 1. Route per platform (checked 2026-09-21)

| Platform | Route | Why | Analytics |
|---|---|---|---|
| Instagram | Browser: the web uploader, since 2026-09-23 (was the official API, Instagram Login variant, until Meta blocked the app's API access without notice) | the API is free and needs no review, but Meta can and did switch it off; a route we cannot restore ourselves is not a route | counts read from the post page in the browser; the API's insights when it is on |
| TikTok | Browser: TikTok Studio web uploader, using its native scheduler | the Content Posting API is free but unaudited apps post private only, and the audit guidelines reject "a utility tool to help upload contents to the account(s) you or your team manages" | view, like, comment, share counts read from TikTok Studio in the browser |
| X | Browser: web composer | the free tier closed to new developers on 2026-02-06; pay per use is 0.015 USD per post, 0.20 USD with a link | counts read from the post page |
| LinkedIn | Browser for the company page; the free self-serve API (`w_member_social`) if we post as a person | company page posting needs the Community Management API: approval, a legal entity, weeks | counts read from the page admin view |

The route is data: `social.platforms.<name>.adapter` in `store/config.json`
is `api` or `browser`. Switching X to its API later is a config edit and a
key in `store/.env`, nothing else.

## 2. Where it sits

- **Department: Growth.** `docs/store-plan.md` already reserves it for
  marketing. It reads `work/<hash>/design.json` and the product record and
  writes only under `work/<hash>/social/`. It gets no 3DAPI key.
- **Process: `promote`**, its own stage table, separate from intake. It
  starts for a product whose record is `ready` (price approved, design
  done). It does not wait for Etsy: captions carry the listing URL when
  `storefront.url` exists and the shop URL from config when it does not.
  Instagram and TikTok captions have no clickable links anyway.
- **One skill, one decision.** `write-social-post` writes the words. Every
  other step is a script. No model drives a browser: a worker clicking
  through an uploader costs dozens of turns and is the least deterministic
  thing we could build.

| Stage | Who | Requires | Produces | Gate |
|---|---|---|---|---|
| `social-media` | script `social_media.py`, no model | `design.json` | `social/media.json`, `social/media/*` | auto |
| `social-copy` | skill `write-social-post`, growth worker | `design.json`, `social/media.json`, product record | `social/posts.json` | auto |
| `social-approve` | a person, in the console | `social/posts.json` | `social/approved.json` | propose |
| `social-post` | script `social_post.py`, no model | `social/approved.json` | `social/<platform>.json` per platform | auto once approved |
| `social-metrics` | script `social_metrics.py`, scheduled | `social/<platform>.json` | `social/metrics.jsonl` | auto |

`social-approve` stays a human gate until a record of approved posts with no
take-downs exists; promotion is a commit, as for every other gate.

## 3. The file contract under `work/<hash>/social/`

- `media.json`: the assets each platform gets. `social_media.py` makes a
  9:16 1080x1920 turntable (a second capture from `/studio`, not a padded
  square), mixes a licensed audio bed from `$DAPILOT_STORE_DIR/reference/audio/`
  with ffmpeg (the web uploaders have no sound library and silent video does
  worse), writes a cover frame, and converts photos to JPEG (the Instagram
  API takes JPEG only).
- `posts.json`: per platform: caption, hashtags, alt text, asset ids. Checked
  by `social_post.py check`: length limits per platform, hashtag caps, the
  only price mentioned is the approved price, no em dashes, link rules.
- `approved.json`: who approved, when, which platforms.
- `<platform>.pending.json`: written before the submit click or the publish
  call, with a caption fingerprint. A rerun that finds it never posts; it
  goes straight to verification.
- `<platform>.json`: written only after the post was read back from the
  profile (browser) or returned an id (API): post URL, id, posted_at.
- `metrics.jsonl`: one line per reading, at 24 h and 7 d. Zero views at 24 h
  is reported as a possible shadow limit.

Exit codes of `social_post.py post`: 0 posted and verified, 3 submitted but
not yet visible (run again, it verifies only), 4 challenge screen (login,
2FA, captcha: a person is needed), 5 selector not found (the page changed).

## 4. Code layout and the open-source boundary

```
plugins/dapilot/                     published
  skills/write-social-post/          the one decision; reference/platform-rules.json
  scripts/social_media.py            variants, audio bed, covers
  scripts/social_post.py             check, queue, post, verify; adapter registry
  scripts/social_metrics.py
  scripts/social_adapters/instagram_api.py   (+ instagram_auth.py, token refresh)
  scripts/social_adapters/linkedin_api.py    personal profile only
social/                              private, not in the plugin, not published
  browser_session.py                 CloakBrowser persistent context, scrubbed env
  social_login.py                    headed window, a person logs in once per platform
  adapters/x_web.py tiktok_web.py linkedin_web.py
```

`social_post.py` loads browser adapters from `DAPILOT_SOCIAL_ADAPTER_DIR`.
A stranger who installs the plugin gets the API adapters and a loud "no
adapter configured for tiktok" for the rest. The detection-evading part
never ships under 3DAPI's name.

## 5. Browser rules

- `cloakbrowser` from pip, version pinned; the wrapper verifies the binary's
  Ed25519 signature. The free macOS x86_64 binary is Chromium 145 while the
  paid build is 151: an old Chromium is itself a signal, and we accept it.
- The free tier allows one concurrent session, so posting is serial.
- One persistent profile per platform under
  `$DAPILOT_STORE_DIR/social/profiles/<platform>/`, gitignored. Profiles are
  credentials.
- Passwords are never stored or typed by a script. `social_login.py` opens a
  headed window, a person logs in and passes 2FA, the profile keeps the
  session. When a session dies the poster exits 4 and a person logs in again.
- The browser process starts with a scrubbed environment: no 3DAPI, Etsy, fal
  or Anthropic key is visible to the closed binary.
- Headed on the laptop, from the laptop's IP. Headless only after headed has
  a clean record.
- Cadence is config: at most one post per platform per day, inside a time
  window with jitter, `humanize=True`. TikTok and LinkedIn posts are queued
  through the platforms' own schedulers in one weekly session where possible.
- New accounts are used by hand for two weeks before a script touches them.
- Failure screenshots go to `runs/`, never to `work/` or a report.

## 6. Instagram API specifics (kept for the API adapter; our shop uses the browser since 2026-09-23)

- Meta app in development mode, Instagram Login product, our Business or
  Creator account as Instagram Tester. Scopes: basic, content publish,
  manage insights.
- Token is long-lived (60 days); `instagram_auth.py refresh` runs with the
  metrics schedule. Token at `$DAPILOT_STORE_DIR/instagram_token.json`.
- The API fetches media from a public URL. Decided 2026-09-21: Etsy is the
  source. Every photo and the video uploaded to a listing, a draft included,
  get a public CDN URL (JPEG and MP4), recorded in `publish.json` by
  `etsy_listing.py media`. No bucket, no tunnel, and social posting follows
  Etsy publishing, which is the order we want anyway.
- One Reel (9:16 turntable or lifestyle clip) and one carousel (photos in
  listing order) per product, on different days.

## 6b. The two contracts (built 2026-09-21, `scripts/social/base.py`)

`Integration` is one platform: `limits`, `check(post)` with no network,
`submit(post, save, resume)` which checkpoints through `save` before the step
that cannot be undone, `confirm(post, checkpoint)` which only reads, and
`metrics`. `Session` is one account on one platform staying signed in:
`status()` asks the platform and answers `ok`, `expiring`, `needs_login` or
`challenge`; `refresh()` renews without a person where the platform allows
it; `keepalive()` is status then refresh when due; `login_help()` is the
sentence a person reads. They are separate because they run on different
clocks: posts follow the queue, `social_post.py session keepalive` runs
daily whether or not anything is posted.

- API platforms: the session is a token file. Instagram's 60-day token is
  renewed once it has 14 days left.
- Browser platforms: the session is a CloakBrowser persistent profile plus a
  small JSON beside it. `status()` opens the platform headless and looks for
  the logged-in marker; `refresh()` is a short visit that lets the platform
  roll its cookies; a login wall or a challenge is `needs_login` or
  `challenge` and exits 4. No script ever types a password. The profile must
  be launched with the same fingerprint seed, locale, timezone and IP every
  time: a session that "will not stay connected" is usually a new device
  fingerprint on every launch, which the platform answers by ending the
  session. The seed is stored in the session JSON when the profile is made.

## 7. Build order

| Phase | Build | Done when |
|---|---|---|
| S0 Skeleton | `social` block in `store/config.example.json`, promote stage table, `social_media.py`, `write-social-post` with its check and an eval | the cat clicker has a validated `media.json` and `posts.json`, no network touched |
| S1 Instagram | `instagram_auth.py`, `instagram_api.py`, media hosting | one Reel live from the script; a rerun posts nothing; insights read back |
| S2 Browser core and X | `browser_session.py`, `social_login.py`, `x_web.py`, the pending and verify cycle | one post live and verified with its URL; killing the run mid-upload and rerunning does not double post; a logged-out profile exits 4 |
| S3 LinkedIn | `linkedin_web.py` for the page (or `linkedin_api.py` for a person) | one page post live, verified |
| S4 TikTok | `tiktok_web.py` on TikTok Studio with scheduling | one scheduled video goes public at its time, verified the day after |
| S5 Console and metrics | Social panel on the Catalogue page (queue, approve, status, counts), `social_metrics.py`, shadow-limit flag | a product approved in the console is posted on four platforms and shows 24 h counts |
| S6 Schedule | launchd or a scheduled run for queue and metrics; gate promotion by commit | a week of posts with no person involved except challenges |

S1 and S2 are independent and prove the two adapter kinds; everything after
is another adapter on the same contract.

## 8. Known risks, accepted

Account bans and shadow limits (all four platforms forbid automated access
in their terms), selector drift every few weeks per browser platform, login
challenges that stop an unattended run, a stale free Chromium, a closed
binary holding brand sessions, no analytics beyond public counts outside
Instagram. Mitigations are in section 5; none removes the risk.

## 9. Open before S1

1. Do the four brand accounts exist, and how old are they?
2. LinkedIn: company page (browser) or a personal profile (free API)?
3. Audio: which licensed tracks go in `reference/audio/`?
4. R2 free tier for Instagram media, or the tunnel?
