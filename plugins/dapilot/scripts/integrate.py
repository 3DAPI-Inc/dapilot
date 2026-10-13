#!/usr/bin/env python3
"""Connect the shop's accounts without a value ever passing through the agent.

    integrate.py serve  [--port 3105] [--no-browser] [--foreground]   open the Integrations page
    integrate.py status [--json]                                       what is connected; never a value
    integrate.py test   [--integration ID]                             verify the saved values against each platform

`serve` starts a small local web server bound to 127.0.0.1, opens it in the
browser and returns. The page lists every integration dapilot knows, with the
steps a person follows on the platform's side and one field per value. The
browser posts the values to this server, the server writes them to
`$DAPILOT_STORE_DIR/.env` (mode 0600) and verifies each integration with one
real, free request. Results, without values, go to
`$DAPILOT_STORE_DIR/integrations.json`, which `status` and the page read.

The agent runs this script and reads its output. It never sees a value: the
page talks to the server, the server talks to the platforms, and the only
things reported back are "set", "verified" and a non-secret summary such as
the shop's name. The plugin's PreToolUse hook (`scripts/guard_secrets.py`)
refuses the agent's tools on the env file and the token files.

Exit codes: 0 ok, 1 a verification failed, 2 usage or environment.
"""
from __future__ import annotations

import argparse
import base64
import http.server
import json
import os
import re
import secrets
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGO = HERE.parent / "assets" / "3dapi-logo.png"
UA = "dapilot-integrate/0.1"
DEFAULT_PORT = 3106
NAVY = "\033[38;2;28;50;84m"   # the brand's navy, #1C3254
BANNER = """\
     ██████   █████  ██████  ██
     ██   ██ ██   ██ ██   ██ ██
▀▀█  ██   ██ ███████ ██████  ██
 ▀█  ██   ██ ██   ██ ██      ██
▀▀▀  ██████  ██   ██ ██      ██
"""
ETSY_SCOPES = "listings_r listings_w shops_r"
ETSY_API = "https://openapi.etsy.com/v3/application"
IDLE_SECONDS = 1800


# --------------------------------------------------------------------------- store

def store_dir() -> Path:
    s = os.environ.get("DAPILOT_STORE_DIR")
    if s:
        return Path(s).expanduser().resolve()
    root = Path(os.environ.get("DAPILOT_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    return (root / "store").resolve()


def env_value(raw: str) -> str:
    """A .env value the way bash reads it: quoted up to the closing quote, else cut at ` #`."""
    v = raw.strip()
    if v and v[0] in "\"'" and v.find(v[0], 1) > 0:
        return v[1:v.find(v[0], 1)]
    if v.startswith("#"):  # `KEY=   # comment` is an empty value, not a comment as the value
        return ""
    return re.split(r"\s#", v, maxsplit=1)[0].strip()


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = env_value(v)
        if v:
            out[k] = v
    return out


def quote_value(v: str) -> str:
    return v if re.fullmatch(r"[A-Za-z0-9_./:@+=,-]+", v) else '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_env(path: Path, updates: dict[str, str | None]) -> None:
    """Set or clear keys in the env file, keeping every other line as it is.
    `KEY=` and `#KEY=` lines are replaced in place; new keys go at the end."""
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*#?\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", line)
        k = m.group(1) if m else None
        if k in updates and k not in seen:
            seen.add(k)
            v = updates[k]
            out.append(f"{k}={quote_value(v)}" if v else f"{k}=")
        else:
            out.append(line)
    new = [k for k, v in updates.items() if k not in seen and v]
    if new:
        if not any(line.startswith("# ---- written by integrate.py") for line in out):
            if out and out[-1].strip():
                out.append("")
            out.append("# ---- written by integrate.py (the Integrations page) ----")
        out.extend(f"{k}={quote_value(updates[k])}" for k in new)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)
    path.chmod(0o600)


def results_path() -> Path:
    return store_dir() / "integrations.json"


def read_results() -> dict:
    try:
        return json.loads(results_path().read_text())
    except (OSError, ValueError):
        return {}


def write_results(doc: dict) -> None:
    p = results_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    tmp.replace(p)


# --------------------------------------------------------------------------- http helpers

def http_json(method: str, url: str, headers: dict | None = None, body: bytes | None = None, timeout: int = 20):
    """One request. Returns (status, parsed json or None, headers). Never raises with the URL in the
    message: an error is reported by status code only, because some platforms put tokens in the URL."""
    req = urllib.request.Request(url, data=body, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, _parse(raw), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        return e.code, _parse(raw), dict(e.headers)
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        return 0, {"error": f"no connection: {reason.__class__.__name__}"}, {}
    except (TimeoutError, OSError) as e:
        return 0, {"error": f"no connection: {e.__class__.__name__}"}, {}


def _parse(raw: bytes):
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def auth_result(status: int, ok_codes=(200,)) -> tuple[bool, str]:
    if status == 0:
        return False, "no connection"
    if status in (401, 403):
        return False, "rejected: the platform did not accept the key"
    if status in ok_codes:
        return True, "verified"
    return False, f"unexpected answer ({status})"


# --------------------------------------------------------------------------- the integrations

def test_dapi(env: dict) -> dict:
    url, key = env.get("DAPI_API_URL", "").strip().rstrip("/"), env.get("DAPI_API_KEY")
    if not url or not key:
        return {"ok": False, "summary": "missing values"}
    if url.endswith("/v1"):
        url = url[:-3]  # the contract's paths carry /v1; accept either form, as dapi.py does
    status, body, hdrs = http_json("GET", url + "/v1/me", {"x-api-key": key, "Accept": "application/json"})
    ok, note = auth_result(status)
    summary = note
    if ok and isinstance(body, dict):
        who = body.get("name") or body.get("email") or body.get("id") or body.get("keyId")
        scopes = body.get("permissions") or body.get("scopes")
        summary = "key accepted" + (f" for {who}" if who else "") + (f", scopes {', '.join(map(str, scopes))}" if isinstance(scopes, list) else "")
    return {"ok": ok, "summary": summary, "fields": {"DAPI_API_URL": ok or status != 0, "DAPI_API_KEY": ok}}


def test_claude(env: dict) -> dict:
    key, tok = env.get("ANTHROPIC_API_KEY"), env.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not key and not tok:
        return {"ok": False, "summary": "no value: workers will use this machine's `claude auth login`"}
    fields, parts, ok = {}, [], False
    if key:
        status, body, _ = http_json("GET", "https://api.anthropic.com/v1/models",
                                    {"x-api-key": key, "anthropic-version": "2023-06-01"})
        k_ok, note = auth_result(status)
        n = len(body.get("data", [])) if k_ok and isinstance(body, dict) else 0
        fields["ANTHROPIC_API_KEY"] = k_ok
        parts.append(f"API key {note}" + (f", {n} models visible" if n else ""))
        ok = ok or k_ok
    if tok:
        fields["CLAUDE_CODE_OAUTH_TOKEN"] = True
        parts.append("subscription token present (checked for presence only; verified by the first worker run)")
        ok = True
    return {"ok": ok, "summary": "; ".join(parts), "fields": fields}


def etsy_token_path() -> Path:
    return store_dir() / "etsy_token.json"


def test_etsy(env: dict) -> dict:
    ks, sec = env.get("ETSY_KEYSTRING"), env.get("ETSY_SHARED_SECRET")
    if not ks or not sec:
        return {"ok": False, "summary": "missing values"}
    api_key = f"{ks}:{sec}"
    status, body, _ = http_json("GET", ETSY_API + "/openapi-ping", {"x-api-key": api_key})
    ok, note = auth_result(status)
    fields = {"ETSY_KEYSTRING": ok, "ETSY_SHARED_SECRET": ok}
    if not ok:
        return {"ok": False, "summary": f"app key {note}", "fields": fields}
    app_id = body.get("application_id") if isinstance(body, dict) else None
    summary = "app key verified" + (f" (app {app_id})" if app_id else "")
    shop = etsy_shop(api_key, env)
    if shop is None:
        return {"ok": False, "summary": summary + "; shop not authorized yet", "fields": fields, "action": "authorize"}
    return {"ok": True, "summary": summary + f"; shop {shop}", "fields": fields}


def etsy_shop(api_key: str, env: dict) -> str | None:
    """The authorized shop's name through the saved token, refreshing it if needed. None when there is no usable token."""
    if not etsy_token_path().exists():
        return None
    child_env = {**os.environ, **env, "DAPILOT_STORE_DIR": str(store_dir())}
    r = subprocess.run([sys.executable, str(HERE / "etsy_auth.py"), "whoami"], env=child_env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    try:
        me = json.loads(r.stdout)
    except ValueError:
        return None
    shop_id = me.get("shop_id") if isinstance(me, dict) else None
    if not shop_id:
        return "authorized (no shop on this account)"
    status, body, _ = http_json("GET", f"{ETSY_API}/shops/{shop_id}", {"x-api-key": api_key})
    if status == 200 and isinstance(body, dict) and body.get("shop_name"):
        return body["shop_name"]
    return f"id {shop_id}"


def etsy_authorize(env: dict) -> dict:
    """Start the PKCE login in the background. The browser opens on Etsy's consent page; the loopback
    on :3003 catches the redirect and etsy_auth.py writes the token file. Nothing comes back here."""
    if not env.get("ETSY_KEYSTRING") or not env.get("ETSY_SHARED_SECRET"):
        return {"ok": False, "summary": "save the keystring and shared secret first"}
    log = store_dir() / "integrations.etsy-login.log"
    log.touch(mode=0o600, exist_ok=True)
    child_env = {**os.environ, **env, "DAPILOT_STORE_DIR": str(store_dir())}
    with log.open("ab") as fh:
        subprocess.Popen([sys.executable, str(HERE / "etsy_auth.py"), "login", "--scopes", ETSY_SCOPES],
                         env=child_env, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True)
    return {"ok": True, "summary": "Etsy opened in the browser. Sign in to the shop, click Allow, then Test here."}


def test_fal(env: dict) -> dict:
    key = env.get("FAL_KEY") or env.get("FAL_API_KEY")
    if not key:
        return {"ok": False, "summary": "missing value"}
    status, body, _ = http_json("POST", "https://rest.fal.ai/storage/auth/token?storage_type=fal-cdn-v3",
                                {"authorization": f"Key {key}", "content-type": "application/json"}, b"{}")
    ok, note = auth_result(status)
    if not ok and isinstance(body, dict) and isinstance(body.get("detail"), str):
        note = body["detail"][:120]
    return {"ok": ok, "summary": "key accepted (no paid request made)" if ok else note, "fields": {"FAL_KEY": ok}}


def test_instagram(env: dict) -> dict:
    tok, app_id, app_sec = env.get("INSTAGRAM_ACCESS_TOKEN"), env.get("INSTAGRAM_APP_ID"), env.get("INSTAGRAM_APP_SECRET")
    if not tok and not (app_id and app_sec):
        return {"ok": False, "summary": "no value (optional: Instagram is disabled in config until the browser adapter exists)"}
    fields, parts, ok = {}, [], True
    if tok:
        status, body, _ = http_json("GET", "https://graph.instagram.com/me?fields=username,account_type",
                                    {"Authorization": f"Bearer {tok}"})
        t_ok, note = auth_result(status)
        if status == 400:
            t_ok, note = False, "rejected: the token is invalid or expired"
        fields["INSTAGRAM_ACCESS_TOKEN"] = t_ok
        parts.append(f"token {note}" + (f" for @{body.get('username')}" if t_ok and isinstance(body, dict) and body.get("username") else ""))
        ok = ok and t_ok
    if app_id and app_sec:
        q = urllib.parse.urlencode({"client_id": app_id, "client_secret": app_sec, "grant_type": "client_credentials"})
        status, _, _ = http_json("GET", "https://graph.facebook.com/oauth/access_token?" + q)
        a_ok, note = auth_result(status)
        if status == 400:
            a_ok, note = False, "rejected: app id and secret do not match"
        fields["INSTAGRAM_APP_ID"] = fields["INSTAGRAM_APP_SECRET"] = a_ok
        parts.append(f"app {note}")
        ok = ok and a_ok
    return {"ok": ok, "summary": "; ".join(parts), "fields": fields}


INTEGRATIONS = [
    {
        "id": "dapi", "name": "3DAPI", "required": True,
        "what": "The slicer, the cost basis and the producer. Every quote and order goes through its public API.",
        "steps": [
            "Sign in to your 3DAPI account and open the API keys page.",
            "Create a key. Give it the PRICING scope for intake; add ORDER only if this machine will place orders.",
            "Copy the key once; 3DAPI does not show it again.",
            "The API origin is the public API host your account uses, without /v1 (for example https://api.example.com).",
        ],
        "fields": [
            {"key": "DAPI_API_URL", "label": "API origin", "secret": False, "placeholder": "https://..."},
            {"key": "DAPI_API_KEY", "label": "API key", "secret": True},
        ],
        "test": test_dapi,
    },
    {
        "id": "etsy", "name": "Etsy", "required": True,
        "what": "The storefront. Research reads the market through it; publish creates the listings in your shop.",
        "steps": [
            "Open https://www.etsy.com/developers/your-apps and sign in with the account that owns the shop.",
            "Create a new app. Choose the Seller App type (your own shop only): it is approved within minutes with no review.",
            "Under callback URLs add exactly: http://localhost:3003/oauth/redirect",
            "Copy the Keystring and the Shared secret from the app page into the two fields below, then Save and test.",
            "Click Authorize shop. A browser tab opens on Etsy; sign in to the shop and click Allow. Then click Test.",
        ],
        "fields": [
            {"key": "ETSY_KEYSTRING", "label": "Keystring", "secret": True},
            {"key": "ETSY_SHARED_SECRET", "label": "Shared secret", "secret": True},
        ],
        "actions": [{"id": "authorize", "label": "Authorize shop"}],
        "test": test_etsy,
    },
    {
        "id": "fal", "name": "fal.ai", "required": True,
        "what": "Lifestyle photos and the listing clip in the design stage. Each call costs money; the stage has a cap per item.",
        "steps": [
            "Sign in at https://fal.ai and open https://fal.ai/dashboard/keys.",
            "Add a key and copy it.",
            "Add credit to the account; the design stage spends about 1.60 USD per item.",
        ],
        "fields": [{"key": "FAL_KEY", "label": "API key", "secret": True}],
        "test": test_fal,
    },
    {
        "id": "claude", "name": "Claude (headless workers)", "required": False,
        "what": "How the pipeline's workers sign in when they run without a terminal. Leave empty to use this machine's `claude` login.",
        "steps": [
            "Either: create an API key at https://console.anthropic.com/settings/keys. Runs then bill to the API in dollars.",
            "Or: run `claude setup-token` in a terminal and paste the token it prints. Runs then use the subscription.",
            "Set only one. If both are set, store/.env's DAPILOT_BILLING decides.",
        ],
        "fields": [
            {"key": "ANTHROPIC_API_KEY", "label": "API key", "secret": True, "optional": True},
            {"key": "CLAUDE_CODE_OAUTH_TOKEN", "label": "Subscription token", "secret": True, "optional": True},
        ],
        "test": test_claude,
    },
    {
        "id": "instagram", "name": "Instagram (API)", "required": False,
        "what": "Optional. The Meta API adapter. Disabled in config: Meta blocked our app on 2026-09-23 and the browser adapter is not built yet.",
        "steps": [
            "Open https://developers.facebook.com/apps and create an app with the Instagram product.",
            "Add the Instagram account (Business or Creator) to the app with a role, then generate a long-lived token for it.",
            "Paste the token. The app id and secret are needed only for the sign-in flow that renews tokens.",
        ],
        "fields": [
            {"key": "INSTAGRAM_ACCESS_TOKEN", "label": "Long-lived token", "secret": True, "optional": True},
            {"key": "INSTAGRAM_APP_ID", "label": "App id", "secret": False, "optional": True},
            {"key": "INSTAGRAM_APP_SECRET", "label": "App secret", "secret": True, "optional": True},
        ],
        "test": test_instagram,
    },
]
BY_ID = {i["id"]: i for i in INTEGRATIONS}
MANAGED_KEYS = {f["key"] for i in INTEGRATIONS for f in i["fields"]} | {"FAL_API_KEY"}


# --------------------------------------------------------------------------- state and runner

def run_test(iid: str) -> dict:
    integ = BY_ID[iid]
    env = read_env(store_dir() / ".env")
    try:
        res = integ["test"](env)
    except Exception as e:  # never let a traceback carry a value
        res = {"ok": False, "summary": f"check failed: {e.__class__.__name__}"}
    res["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    doc = read_results()
    doc[iid] = res
    write_results(doc)
    return res


def state() -> dict:
    """Everything the page and `status` may know. No value, ever: only whether a key is set,
    plus the text of fields marked non-secret."""
    env = read_env(store_dir() / ".env")
    results = read_results()
    rows = []
    for integ in INTEGRATIONS:
        fields = []
        for f in integ["fields"]:
            v = env.get(f["key"]) or (env.get("FAL_API_KEY") if f["key"] == "FAL_KEY" else None)
            fields.append({"key": f["key"], "label": f["label"], "secret": f["secret"], "optional": f.get("optional", False),
                           "set": bool(v), "value": (v if v and not f["secret"] else None), "placeholder": f.get("placeholder", "")})
        res = results.get(integ["id"], {})
        required_set = all(fl["set"] for fl in fields if not fl["optional"])
        if res.get("ok"):
            status = "done"
        elif res and required_set:
            status = "failed"
        elif any(fl["set"] for fl in fields):
            status = "untested"
        else:
            status = "empty"
        rows.append({"id": integ["id"], "name": integ["name"], "required": integ["required"], "what": integ["what"],
                     "steps": integ["steps"], "fields": fields, "actions": integ.get("actions", []),
                     "status": status, "result": {k: res.get(k) for k in ("ok", "summary", "checked_at", "fields", "action")}})
    return {"store": str(store_dir()), "env_file": str(store_dir() / ".env"), "integrations": rows}


# --------------------------------------------------------------------------- the page

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>3DAPI Integrations</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Montserrat:wght@600&display=swap" rel="stylesheet">
<style>
/* 3dapi palette only: glacier white + navy. Rust is the one accent and marks a failed check. */
:root{--navy:#1c3254;--navy-deep:#142540;--blue:#8eb0e2;--ink:#080b10;--rust:#813a39;
      --g50:#fbfdff;--g100:#f4f8fc;--g200:#eaf1f9;--g300:#dde8f4;--g400:#cbdcee;--white:#fff;
      --mute:rgba(8,11,16,.58);--radius:6px;--radius-sm:4px}
*{box-sizing:border-box}
body{margin:0;background:var(--g100);color:var(--ink);font:14px/1.5 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1080px;margin:0 auto;padding:40px 24px 64px}
h1{font:600 22px/1.2 Montserrat,Inter,sans-serif;color:var(--navy);margin:0 0 6px}
.brand{display:flex;align-items:center;gap:14px;margin:0 0 6px}.brand img{height:24px;width:auto;display:block}.brand h1{margin:0}
.sub{color:var(--mute);margin:0 0 24px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:var(--navy)}
a{color:var(--navy)}

table{width:100%;border-collapse:separate;border-spacing:0;background:var(--white);border:1px solid var(--g300);border-radius:var(--radius);box-shadow:0 8px 32px -8px rgba(28,50,84,.12)}
th{text-align:left;font-weight:500;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);padding:10px 16px;background:var(--g50);border-bottom:1px solid var(--g300)}
th:first-child{border-top-left-radius:var(--radius)}th:last-child{border-top-right-radius:var(--radius)}
td{padding:16px;vertical-align:top;border-bottom:1px solid var(--g200)}
tbody:last-child tr:last-child td{border-bottom:0}
tbody.open tr.main td{border-bottom:0}
col.c-name{width:21%}col.c-status{width:19%}col.c-actions{width:18%}

.name{font-weight:500;color:var(--navy);font-size:15px}
.req{display:block;color:var(--mute);font-size:12px;margin-top:2px}
.what{color:var(--mute);font-size:13px;margin:8px 0 0}
.how{display:inline-block;margin-top:10px;font-size:12px;color:var(--navy);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline;text-underline-offset:2px;font-family:inherit}

.status{display:flex;align-items:center;gap:8px;color:var(--ink);white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--g400);flex:none}
.done .dot{background:var(--navy)}.failed .dot{background:var(--rust)}.untested .dot{background:var(--blue)}
.result{font-size:12px;margin-top:6px;color:var(--mute)}.failed .result{color:var(--rust)}
.when{display:block;font-size:11px;color:var(--mute);margin-top:2px}

.field{margin-bottom:10px}.field:last-child{margin-bottom:0}
.field label{display:flex;align-items:baseline;gap:8px;font-size:12px;color:var(--mute);margin-bottom:4px}
.field .state{margin-left:auto;font-size:11px;color:var(--navy);white-space:nowrap}
.field .clear{font-size:11px;color:var(--mute);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline;text-underline-offset:2px;font-family:inherit}
input{width:100%;font:inherit;font-size:13px;padding:7px 10px;border:1px solid var(--g300);border-radius:var(--radius-sm);background:var(--g50);color:var(--ink)}
input::placeholder{color:rgba(8,11,16,.35)}
input:focus{outline:0;border-color:var(--navy);box-shadow:0 0 0 3px rgba(142,176,226,.45);background:var(--white)}

.actions{display:flex;flex-direction:column;gap:6px}
button.b{font:inherit;font-size:13px;font-weight:500;padding:7px 12px;border-radius:var(--radius-sm);border:1px solid var(--g400);background:var(--white);color:var(--navy);cursor:pointer;text-align:center;transition:background .15s,border-color .15s}
button.b:hover{background:var(--g100);border-color:var(--blue)}
button.b.primary{background:var(--navy);color:var(--white);border-color:var(--navy)}
button.b.primary:hover{background:var(--navy-deep);border-color:var(--navy-deep)}
button.b:disabled{opacity:.45;cursor:default}

tr.steps{display:none}tbody.open tr.steps{display:table-row}
tr.steps td{background:var(--g50);padding:4px 16px 16px;border-top:0}
tr.steps ol{margin:0;padding-left:20px;font-size:13px;color:var(--ink)}
tr.steps li{margin:4px 0}tr.steps li::marker{color:var(--mute)}

footer{display:flex;justify-content:space-between;align-items:center;margin-top:20px;color:var(--mute);font-size:13px;gap:12px;flex-wrap:wrap}

@media (max-width:820px){
  main{padding:28px 16px 56px}
  thead,col{display:none}
  table,tbody,tr,td{display:block;width:100%}
  table{border-radius:var(--radius)}
  td{padding:8px 16px;border-bottom:0}
  tr.main td:first-child{padding-top:16px}tr.main td:last-child{padding-bottom:16px}
  tbody{border-bottom:1px solid var(--g200)}tbody:last-of-type{border-bottom:0}
  tbody.open tr.steps{display:block}tr.steps td{padding:8px 16px 16px}
  .actions{flex-direction:row;flex-wrap:wrap}button.b{flex:1 1 auto}
}
</style></head><body><main>
<div class="brand"><img src="/logo.png" alt="3DAPI"><h1>Integrations</h1></div>
<p class="sub">Connect the accounts your shop runs on. Each value stays on this computer (<code id="envfile"></code>) and is checked with its platform. Nothing you type here reaches the assistant.</p>
<table>
  <colgroup><col class="c-name"><col class="c-status"><col class="c-values"><col class="c-actions"></colgroup>
  <thead><tr><th>Integration</th><th>Status</th><th>Values</th><th>Actions</th></tr></thead>
  <tbody id="list"></tbody>
</table>
<footer><span id="summary"></span><button class="b primary" id="finish">Finish</button></footer>
</main>
<script>
const TOKEN = "%%TOKEN%%";
const api = (path, body) => fetch(path, {method: body ? "POST" : "GET", headers: {"content-type": "application/json", "x-token": TOKEN}, body: body ? JSON.stringify(body) : undefined}).then(r => r.json());
const phrase = {done: "connected", failed: "check failed", untested: "saved, not tested", empty: "not connected"};
let state; const opened = new Set();
function esc(s){return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function render(){
  document.getElementById("envfile").textContent = state.env_file;
  const table = document.querySelector("table");
  for (const tb of table.querySelectorAll("tbody")) tb.remove();
  let done = 0, required = 0;
  for (const it of state.integrations){
    if (it.required){required++; if (it.status === "done") done++;}
    if (it.status === "empty" && !opened.has(it.id + ":closed")) opened.add(it.id);
    const r = it.result || {};
    const tb = document.createElement("tbody"); tb.dataset.id = it.id; tb.className = it.status + (opened.has(it.id) ? " open" : "");
    tb.innerHTML = `
      <tr class="main">
        <td><span class="name">${esc(it.name)}</span><span class="req">${it.required ? "required" : "optional"}</span>
          <p class="what">${esc(it.what)}</p>
          <button class="how" data-act="how">${opened.has(it.id) ? "Hide the steps" : "How to get the values"}</button></td>
        <td><span class="status"><span class="dot"></span>${phrase[it.status]}</span>
          ${r.summary ? `<div class="result">${esc(r.summary)}</div>` : ""}
          ${r.checked_at ? `<span class="when">checked ${esc(r.checked_at.slice(0, 16).replace("T", " "))}</span>` : ""}</td>
        <td>${it.fields.map(f => `
          <div class="field"><label for="${it.id}-${f.key}">${esc(f.label)}${f.optional ? " (optional)" : ""} <code>${f.key}</code>
            ${f.set ? `<span class="state">${r.fields && r.fields[f.key] === true ? "verified" : "saved"}</span><button class="clear" data-key="${f.key}">remove</button>` : ""}</label>
          <input id="${it.id}-${f.key}" name="${f.key}" type="${f.secret ? "password" : "text"}" autocomplete="off" spellcheck="false"
             placeholder="${f.set ? (f.secret ? "saved; type to replace" : "") : esc(f.placeholder)}" value="${f.value ? esc(f.value) : ""}"></div>`).join("")}</td>
        <td><div class="actions"><button class="b primary" data-act="save">Save and test</button><button class="b" data-act="test">Test</button>
          ${(it.actions || []).map(a => `<button class="b" data-act="${a.id}">${esc(a.label)}</button>`).join("")}</div></td>
      </tr>
      <tr class="steps"><td colspan="4"><ol>${it.steps.map(s => `<li>${esc(s).replace(/(https?:\/\/[^\s]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>')}</li>`).join("")}</ol></td></tr>`;
    table.appendChild(tb);
  }
  document.getElementById("summary").textContent = `${done} of ${required} required integrations connected`;
}
async function refresh(){ state = await api("/api/state"); render(); }
document.querySelector("table").addEventListener("click", async e => {
  const btn = e.target.closest("button"); if (!btn) return;
  const tb = btn.closest("tbody"); const id = tb.dataset.id;
  if (btn.dataset.act === "how"){
    if (opened.has(id)){ opened.delete(id); opened.add(id + ":closed"); } else { opened.add(id); opened.delete(id + ":closed"); }
    render(); return;
  }
  for (const b of tb.querySelectorAll("button")) b.disabled = true;
  try {
    if (btn.dataset.key){ await api("/api/clear", {integration: id, key: btn.dataset.key}); }
    else if (btn.dataset.act === "save"){
      const values = {}; for (const inp of tb.querySelectorAll("input")) if (inp.value.trim()) values[inp.name] = inp.value.trim();
      await api("/api/values", {integration: id, values});
    } else if (btn.dataset.act === "test"){ await api("/api/test", {integration: id}); }
    else { const r = await api("/api/action", {integration: id, action: btn.dataset.act}); if (r.summary) alert(r.summary); }
  } finally { await refresh(); }
});
document.getElementById("finish").addEventListener("click", async () => { await api("/api/finish", {}); document.body.innerHTML = '<main><h1>Done</h1><p class="sub">You can close this tab. Run <code>/dapilot:integrate</code> again any time.</p></main>'; });
refresh();
</script></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = UA
    token = ""
    last_seen = time.time()

    def log_message(self, fmt, *args):  # quiet; nothing here may log a body
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, doc) -> None:
        self._send(code, json.dumps(doc).encode(), "application/json")

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return self.client_address[0] in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost", "[::1]")

    def do_GET(self):
        Handler.last_seen = time.time()
        if not self._local():
            return self._json(403, {"error": "local only"})
        if self.path == "/":
            return self._send(200, PAGE.replace("%%TOKEN%%", self.token).encode(), "text/html; charset=utf-8")
        if self.path == "/logo.png" and LOGO.exists():
            return self._send(200, LOGO.read_bytes(), "image/png")
        if self.path == "/api/state" and self.headers.get("x-token") == self.token:
            return self._json(200, state())
        self._json(404, {"error": "not found"})

    def do_POST(self):
        Handler.last_seen = time.time()
        if not self._local() or self.headers.get("x-token") != self.token:
            return self._json(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        iid = body.get("integration")
        if self.path == "/api/finish":
            self._json(200, {"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if iid not in BY_ID:
            return self._json(400, {"error": "unknown integration"})
        allowed = {f["key"] for f in BY_ID[iid]["fields"]}
        if self.path == "/api/values":
            values = {k: str(v) for k, v in (body.get("values") or {}).items() if k in allowed and str(v).strip()}
            if values:
                write_env(store_dir() / ".env", values)
            return self._json(200, run_test(iid))
        if self.path == "/api/clear":
            k = body.get("key")
            if k in allowed:
                write_env(store_dir() / ".env", {k: None})
            return self._json(200, run_test(iid))
        if self.path == "/api/test":
            return self._json(200, run_test(iid))
        if self.path == "/api/action":
            if iid == "etsy" and body.get("action") == "authorize":
                return self._json(200, etsy_authorize(read_env(store_dir() / ".env")))
            return self._json(400, {"error": "unknown action"})
        self._json(404, {"error": "not found"})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def our_page(port: int) -> bool:
    """True when an Integrations page is already served on this port (an earlier run, still within its idle time)."""
    status, _, _ = http_json("GET", f"http://127.0.0.1:{port}/", timeout=2)
    if status != 200:
        return False
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            return "<title>3DAPI Integrations</title>" in r.read(4096).decode(errors="replace")
    except OSError:
        return False


def serve(args) -> int:
    store = store_dir()
    store.mkdir(parents=True, exist_ok=True)
    if not args.foreground:
        for port in range(args.port, args.port + 6):   # a busy port is skipped; an earlier page is reused
            url = f"http://127.0.0.1:{port}/"
            if our_page(port):
                if not args.no_browser:
                    webbrowser.open(url)
                print(json.dumps({"ok": True, "url": url, "store": str(store), "note": "the page was already open; reused"}))
                return 0
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--foreground", "--port", str(port)]
                                     + (["--no-browser"] if args.no_browser else []),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True, env={**os.environ, "DAPILOT_STORE_DIR": str(store)})
            time.sleep(0.6)
            if child.poll() is None:
                print(json.dumps({"ok": True, "url": url, "store": str(store),
                                  "note": f"page served for up to {IDLE_SECONDS // 60} idle minutes or until Finish; then run `integrate.py status`"}))
                return 0
        print(json.dumps({"ok": False, "reason": f"ports {args.port} to {args.port + 5} are all in use"}))
        return 2
    Handler.token = secrets.token_urlsafe(24)
    try:
        srv = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        print(json.dumps({"ok": False, "reason": f"port {args.port} is in use"}), file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{args.port}/"

    def idle_watch():
        while True:
            time.sleep(15)
            if time.time() - Handler.last_seen > IDLE_SECONDS:
                srv.shutdown()
                return
    threading.Thread(target=idle_watch, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    srv.serve_forever()
    return 0


def banner(args) -> int:
    colour = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    print((NAVY + BANNER + "\033[0m") if colour else BANNER, end="")
    return 0


def status(args) -> int:
    st = state()
    if args.json:
        print(json.dumps(st, indent=1))
    else:
        w = max(len(i["name"]) for i in st["integrations"])
        for i in st["integrations"]:
            mark = {"done": "●", "failed": "✕", "untested": "○", "empty": " "}[i["status"]]
            phrase = {"done": "connected", "failed": "check failed", "untested": "saved, not tested", "empty": "not connected"}[i["status"]]
            extra = f"  {i['result']['summary']}" if i["result"].get("summary") and i["status"] != "empty" else ""
            print(f"{mark} {i['name']:<{w}}  {phrase}{'' if i['required'] else '  (optional)'}{extra}")
    missing = [i["name"] for i in st["integrations"] if i["required"] and i["status"] != "done"]
    return 1 if missing else 0


def test(args) -> int:
    ids = [args.integration] if args.integration else [i["id"] for i in INTEGRATIONS]
    bad = 0
    for iid in ids:
        if iid not in BY_ID:
            print(json.dumps({"ok": False, "reason": f"unknown integration {iid}"}), file=sys.stderr)
            return 2
        res = run_test(iid)
        print(json.dumps({"integration": iid, **res}))
        if BY_ID[iid]["required"] and not res["ok"]:
            bad += 1
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--foreground", action="store_true", help="run in this process instead of detaching")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    t = sub.add_parser("test")
    t.add_argument("--integration", choices=[i["id"] for i in INTEGRATIONS])
    sub.add_parser("banner", help="print the 3DAPI wordmark (navy when stdout is a terminal)")
    a = ap.parse_args()
    return {"serve": serve, "status": status, "test": test, "banner": banner}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
