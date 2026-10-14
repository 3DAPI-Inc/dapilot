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

sys.path.insert(0, str(HERE))
from store_path import store_dir  # noqa: E402  one rule for every script: $DAPILOT_STORE_DIR, a checkout's store/, else ~/.dapilot


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


# --------------------------------------------------------------------------- settings

TEMPLATE_CONFIG = HERE.parent / "templates" / "config.example.json"
PUBLISH_MODES = ("live", "sandbox")


def config_path() -> Path:
    return store_dir() / "config.json"


def read_config() -> dict:
    for p in (config_path(), TEMPLATE_CONFIG):
        try:
            doc = json.loads(p.read_text())
            if isinstance(doc, dict):
                return doc
        except (OSError, ValueError):
            continue
    return {}


def settings_state() -> dict:
    """The settings the page shows. Only keys a person switches on the page; nothing secret."""
    pub = read_config().get("publish") or {}
    mode = str(pub.get("mode") or "live").lower()
    return {"publish_mode": mode if mode in PUBLISH_MODES else "live"}


def write_settings(changes: dict) -> dict:
    """Apply the page's changes to the store's config.json, keeping every other key. A store with no
    config yet starts from the plugin's template, as quickstart would seed it."""
    doc = read_config()
    if "publish_mode" in changes:
        mode = str(changes["publish_mode"]).lower()
        if mode not in PUBLISH_MODES:
            raise ValueError(f"publish_mode must be one of {PUBLISH_MODES}")
        doc.setdefault("publish", {})["mode"] = mode
    cp = config_path()
    cp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    tmp.replace(cp)
    return settings_state()


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
    return {"ok": True, "summary": summary + f"; shop {shop}", "detail": shop, "fields": fields}


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


LOGIN_CHILD: subprocess.Popen | None = None   # the sign-in listener on :3003 started by this server, if any
LOGIN_PORT = 3003


def port_free(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def login_pid_path() -> Path:
    return store_dir() / "integrations.etsy-login.pid"


def end_login() -> None:
    """End the sign-in listener, ours or one a previous run of this server left behind (its pid is on disk)."""
    global LOGIN_CHILD
    pids = set()
    if LOGIN_CHILD is not None and LOGIN_CHILD.poll() is None:
        pids.add(LOGIN_CHILD.pid)
    try:
        pids.add(int(login_pid_path().read_text().strip()))
    except (OSError, ValueError):
        pass
    for pid in pids:
        try:
            os.kill(pid, 15)
        except (ProcessLookupError, PermissionError):
            pass
    LOGIN_CHILD = None
    login_pid_path().unlink(missing_ok=True)
    for _ in range(15):
        if port_free(LOGIN_PORT):
            return
        time.sleep(0.2)


def login_state() -> dict:
    """Whether the Etsy sign-in is waiting for Allow right now, for the page."""
    return {"waiting": LOGIN_CHILD is not None and LOGIN_CHILD.poll() is None}


def etsy_authorize(env: dict) -> dict:
    """Start the PKCE login in the background. The browser opens on Etsy's consent page; the loopback
    on :3003 catches the redirect and etsy_auth.py writes the token file. Nothing comes back here."""
    if not env.get("ETSY_KEYSTRING") or not env.get("ETSY_SHARED_SECRET"):
        return {"ok": False, "summary": "save the keystring and shared secret first"}
    global LOGIN_CHILD
    end_login()   # an earlier click, or an earlier run, still waiting: it ends and this one takes over
    if not port_free(LOGIN_PORT):
        return {"ok": False, "summary": f"port {LOGIN_PORT} is held by another program, probably an earlier sign-in "
                                        "from a previous run; it gives up on its own within 5 minutes"}
    log = store_dir() / "integrations.etsy-login.log"
    log.write_bytes(b"")
    log.chmod(0o600)
    child_env = {**os.environ, **env, "DAPILOT_STORE_DIR": str(store_dir())}
    with log.open("ab") as fh:
        # --no-browser: the page opens the consent URL itself, from the click, which every browser allows.
        # The listener on :3003 runs in this child until Etsy redirects back or 5 minutes pass.
        child = subprocess.Popen([sys.executable, str(HERE / "etsy_auth.py"), "login", "--scopes", ETSY_SCOPES,
                                  "--no-browser", "--port", str(LOGIN_PORT), "--timeout", "300"],
                                 env=child_env, stdin=subprocess.DEVNULL, stdout=fh, stderr=fh, start_new_session=True)
    LOGIN_CHILD = child
    login_pid_path().write_text(str(child.pid))
    url = None
    for _ in range(25):   # the URL is printed within a second; a dead child (port 3003 busy, bad keys) exits before that
        time.sleep(0.2)
        for line in log.read_text(errors="replace").splitlines():
            if line.startswith("https://www.etsy.com/"):
                url = line.strip()
        if url or child.poll() is not None:
            break
    if not url:
        tail = [l for l in log.read_text(errors="replace").splitlines() if l.strip()][-2:]
        return {"ok": False, "summary": "could not start the Etsy sign-in: " + (" / ".join(tail) or "no output")[:200]}
    return {"ok": True, "url": url, "summary": "Sign in to the shop on the Etsy page and click Allow. Then click Test here."}


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
    fields, parts, ok, detail = {}, [], True, None
    if tok:
        status, body, _ = http_json("GET", "https://graph.instagram.com/me?fields=username,account_type",
                                    {"Authorization": f"Bearer {tok}"})
        t_ok, note = auth_result(status)
        if status == 400:
            t_ok, note = False, "rejected: the token is invalid or expired"
        fields["INSTAGRAM_ACCESS_TOKEN"] = t_ok
        user = body.get("username") if t_ok and isinstance(body, dict) else None
        parts.append(f"token {note}" + (f" for @{user}" if user else ""))
        if user:
            detail = f"@{user}"
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
    return {"ok": ok, "summary": "; ".join(parts), "detail": detail, "fields": fields}


INTEGRATIONS = [
    {
        "id": "dapi", "name": "3DAPI", "required": True, "icon": "/logo.png",
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
        "id": "etsy", "name": "Etsy", "required": True, "icon": "https://cdn.simpleicons.org/etsy/F1641E",
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
        "id": "fal", "name": "fal.ai", "required": True, "icon": None,
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
        "id": "claude", "name": "Claude (headless workers)", "required": False, "hidden": True,
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
        "id": "instagram", "name": "Instagram (API)", "required": False, "icon": "https://cdn.simpleicons.org/instagram/E4405F",
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
        any_set = any(fl["set"] for fl in fields)
        if res.get("ok"):
            status = "done"
        elif not any_set:
            status = "empty"        # nothing entered: not connected, whatever an earlier check said
        elif res.get("checked_at"):
            status = "failed"
        else:
            status = "untested"
        rows.append({"id": integ["id"], "name": integ["name"], "required": integ["required"], "hidden": integ.get("hidden", False), "icon": integ.get("icon"), "what": integ["what"],
                     "steps": integ["steps"], "fields": fields, "actions": integ.get("actions", []),
                     "status": status, "result": {k: res.get(k) for k in ("ok", "summary", "detail", "checked_at", "fields", "action")}})
    return {"store": str(store_dir()), "env_file": str(store_dir() / ".env"), "integrations": rows, "etsy_login": login_state(),
            "settings": settings_state()}


# --------------------------------------------------------------------------- the page

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="dapilot-integrate" content="%%VERSION%%">
<title>3DAPI Integrations</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Montserrat:wght@600&display=swap" rel="stylesheet">
<style>
/* 3DAPI palette only: glacier white + navy. Rust marks a failed check. */
:root{--navy:#1c3254;--navy-deep:#142540;--blue:#8eb0e2;--ink:#080b10;--rust:#813a39;
      --g50:#fbfdff;--g100:#f4f8fc;--g200:#eaf1f9;--g300:#dde8f4;--g400:#cbdcee;--white:#fff;
      --mute:rgba(8,11,16,.55);--radius:8px;--radius-sm:5px}
*{box-sizing:border-box}
body{margin:0;background:var(--g100);color:var(--ink);font:14px/1.5 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:960px;margin:0 auto;padding:48px 20px 64px}
.brand{display:flex;align-items:center;gap:14px;margin:0 0 28px}.brand img{height:22px;width:auto;display:block}
h1{font:600 20px/1.2 Montserrat,Inter,sans-serif;color:var(--navy);margin:0}
#list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start}
@media (max-width:720px){#list{grid-template-columns:1fr}}
.row{background:var(--white);border:1px solid var(--g300);border-radius:var(--radius);box-shadow:0 8px 32px -12px rgba(28,50,84,.14);padding:18px 20px}
.top{display:flex;align-items:center;gap:12px}
.name{font-weight:500;color:var(--navy);font-size:15px;flex:1;display:flex;align-items:center;gap:10px}
.icon{width:28px;height:28px;border-radius:7px;background:var(--g100);border:1px solid var(--g200);display:flex;align-items:center;justify-content:center;flex:none;font:600 12px Montserrat,Inter,sans-serif;color:var(--navy);overflow:hidden}
.icon img{width:18px;height:18px;object-fit:contain;display:block}.icon img.wide{width:22px;height:auto}
.opt{color:var(--mute);font-size:12px;font-weight:400;margin-left:8px}
.status{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--mute);white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--g400);flex:none}
.done .dot{background:var(--navy)}.failed .dot{background:var(--rust)}.failed .status{color:var(--rust)}
.done .status{color:var(--ink)}
.fields{display:grid;gap:8px;margin-top:12px}
input{width:100%;font:inherit;font-size:13px;padding:8px 10px;border:1px solid var(--g300);border-radius:var(--radius-sm);background:var(--g50);color:var(--ink)}
input::placeholder{color:rgba(8,11,16,.38)}
input:focus{outline:0;border-color:var(--navy);box-shadow:0 0 0 3px rgba(142,176,226,.45);background:var(--white)}
.done .fields{display:none}.done.edit .fields{display:grid}
.bar{display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin-top:12px}
button.b{font:inherit;font-size:13px;font-weight:500;padding:8px 14px;white-space:nowrap;line-height:1.3;align-self:center;border-radius:var(--radius-sm);border:1px solid var(--g400);background:var(--white);color:var(--navy);cursor:pointer}
button.b:hover{background:var(--g100);border-color:var(--blue)}
button.b.primary{background:var(--navy);color:var(--white);border-color:var(--navy)}button.b.primary:hover{background:var(--navy-deep)}
button.b:disabled{opacity:.45;cursor:default}
.link{font:inherit;font-size:12px;color:var(--mute);background:none;border:0;padding:0;cursor:pointer;text-decoration:underline;text-underline-offset:2px;margin-left:auto}
.err{font-size:12px;color:var(--rust);margin-top:8px}.note{font-size:12px;color:var(--mute);margin-top:8px}
.help{display:none;margin:12px 0 0;padding:12px 14px;background:var(--g50);border:1px solid var(--g200);border-radius:var(--radius-sm);font-size:13px}
.open .help{display:block}.help ol{margin:0;padding-left:18px}.help li{margin:3px 0}.help a{color:var(--navy)}
footer{display:flex;justify-content:space-between;align-items:center;margin-top:20px;color:var(--mute);font-size:13px}
.tabs{display:flex;gap:2px;margin:-8px 0 20px;border-bottom:1px solid var(--g300)}
.tab{font:inherit;font-size:13px;font-weight:500;padding:8px 14px;background:none;border:0;border-bottom:2px solid transparent;color:var(--mute);cursor:pointer;margin-bottom:-1px}
.tab:hover{color:var(--navy)}.tab.on{color:var(--navy);border-bottom-color:var(--navy)}
#settings{display:none}body.settings #settings{display:block}body.settings #list,body.settings footer #summary{display:none}body.settings footer{justify-content:flex-end}
.setting .what{font-size:13px;color:var(--mute);margin:4px 0 0}
.seg{display:flex;gap:8px;margin-top:14px}.seg button.b{flex:1;padding:10px 14px}
.seg button.b.on{background:var(--navy);color:var(--white);border-color:var(--navy)}
.seg button.b.on:hover{background:var(--navy)}
.desc{font-size:13px;color:var(--ink);margin-top:12px;padding:10px 12px;background:var(--g50);border:1px solid var(--g200);border-radius:var(--radius-sm)}
</style></head><body><main>
<div class="brand"><img src="/logo.png" alt="3DAPI"><h1 id="title">Integrations</h1></div>
<div class="tabs"><button class="tab on" data-tab="integrations">Integrations</button><button class="tab" data-tab="settings">Settings</button></div>
<div id="list"></div>
<div id="settings"></div>
<footer><span id="summary"></span><button class="b primary" id="finish">Done</button></footer>
</main>
<script>
const TOKEN = "%%TOKEN%%";
const api = (path, body) => fetch(path, {method: body ? "POST" : "GET", headers: {"content-type": "application/json", "x-token": TOKEN}, body: body ? JSON.stringify(body) : undefined}).then(r => r.json());
const phrase = {done: "Connected", failed: "Not working", untested: "Not checked", empty: "Not connected"};
let state; const open = new Set(), edit = new Set(), notes = {};
function esc(s){return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
const MODES = {
  live: "Live. When you approve a price, the listing is created on Etsy with its photos and goes live, and the social posts go out as configured.",
  sandbox: "Sandbox. Every stage runs, including the photos and the listing text, but nothing is created on Etsy and nothing is posted. Switch to Live when you are ready to sell."};
function showTab(name){
  document.body.classList.toggle("settings", name === "settings");
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("on", t.dataset.tab === name);
  document.getElementById("title").textContent = name === "settings" ? "Settings" : "Integrations";
  if (location.hash !== (name === "settings" ? "#settings" : "")) history.replaceState(null, "", name === "settings" ? "#settings" : location.pathname);
}
function renderSettings(){
  const st = state.settings || {}; const mode = st.publish_mode || "live";
  document.getElementById("settings").innerHTML = `
    <div class="row setting">
      <div class="top"><span class="name">Publishing</span><span class="status">${mode === "live" ? "Live" : "Sandbox"}</span></div>
      <p class="what">What happens on Etsy and on social platforms once you approve a price.</p>
      <div class="seg">${["live", "sandbox"].map(m => `<button class="b ${m === mode ? "on" : ""}" data-mode="${m}">${m === "live" ? "Live" : "Sandbox"}</button>`).join("")}</div>
      <div class="desc">${esc(MODES[mode])}</div>
    </div>`;
}
function render(){
  renderSettings();
  const list = document.getElementById("list"); list.innerHTML = "";
  let done = 0, required = 0;
  for (const it of state.integrations){
    if (it.hidden) continue;
    if (it.required){required++; if (it.status === "done") done++;}
    const r = it.result || {};
    const row = document.createElement("div"); row.dataset.id = it.id;
    row.className = "row " + it.status + (open.has(it.id) ? " open" : "") + (edit.has(it.id) ? " edit" : "");
    const detail = it.status === "done" && r.detail ? ` · ${esc(r.detail)}` : "";
    const needAuth = it.status !== "done" && r.action === "authorize";
    row.innerHTML = `
      <div class="top"><span class="name"><span class="icon">${it.icon ? `<img src="${esc(it.icon)}" alt="" class="${it.icon === "/logo.png" ? "wide" : ""}" onerror="this.replaceWith(document.createTextNode('${esc(it.name[0])}'))">` : esc(it.name.slice(0, 3))}</span>${esc(it.name)}${it.required ? "" : '<span class="opt">optional</span>'}</span>
        <span class="status"><span class="dot"></span>${phrase[it.status]}${detail}</span></div>
      <div class="fields">${it.fields.map(f => `
        <input name="${f.key}" type="${f.secret ? "password" : "text"}" autocomplete="off" spellcheck="false" aria-label="${esc(f.label)}"
          placeholder="${f.set ? (f.secret ? "••••••••••••" : "") : esc(f.label) + (f.optional ? " (optional)" : "")}" value="${f.value ? esc(f.value) : ""}">`).join("")}</div>
      ${it.status === "failed" && r.summary ? `<div class="err">${esc(r.summary)}</div>` : ""}
      ${it.id === "etsy" && state.etsy_login && state.etsy_login.waiting ? `<div class="note">Waiting for you to click Allow on Etsy…</div>` : (notes[it.id] ? `<div class="${notes[it.id].ok ? "note" : "err"}">${esc(notes[it.id].text)}</div>` : "")}
      <div class="bar">
        ${it.status === "done" && !edit.has(it.id) ? `<button class="b" data-act="edit">Change</button>` : `<button class="b primary" data-act="save">Save and test</button>`}
        <button class="b" data-act="test" title="Checks the saved values">Test</button>
        ${needAuth ? `<button class="b primary" data-act="authorize">Sign in to Etsy</button>` : ""}
        ${it.status === "done" && !edit.has(it.id) ? "" : `<button class="link" data-act="how">${open.has(it.id) ? "Hide help" : "Where do I get this?"}</button>`}</div>
      <div class="help"><ol>${it.steps.map(s => `<li>${esc(s).replace(/(https?:\/\/[^\s)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>')}</li>`).join("")}</ol></div>`;
    list.appendChild(row);
  }
  document.getElementById("summary").textContent = `${done} of ${required} connected`;
}
async function refresh(){ state = await api("/api/state"); render();
  if (state.etsy_login && state.etsy_login.waiting) setTimeout(async () => { const before = state.etsy_login.waiting; await refresh(); if (before && !state.etsy_login.waiting) { await api("/api/test", {integration: "etsy"}); delete notes.etsy; await refresh(); } }, 3000); }
document.getElementById("list").addEventListener("input", e => {
  const row = e.target.closest(".row"); if (!row) return;
  const typed = [...row.querySelectorAll("input")].some(i => i.value.trim());
  const test = row.querySelector('[data-act="test"]'); if (test) test.disabled = typed;   // Test checks saved values only
});
document.getElementById("list").addEventListener("click", async e => {
  const btn = e.target.closest("button"); if (!btn) return;
  const row = btn.closest(".row"); const id = row.dataset.id; const act = btn.dataset.act;
  if (act === "how"){ open.has(id) ? open.delete(id) : open.add(id); render(); return; }
  if (act === "edit"){ edit.add(id); render(); return; }
  for (const b of row.querySelectorAll("button")) b.disabled = true;
  try {
    if (act === "save"){
      const values = {}; for (const inp of row.querySelectorAll("input")) if (inp.value.trim()) values[inp.name] = inp.value.trim();
      await api("/api/values", {integration: id, values}); edit.delete(id);
    } else if (act === "test"){ await api("/api/test", {integration: id}); }
    else if (act === "authorize"){
      const w = window.open("about:blank", "_blank");   // opened on the click, so no popup blocker
      const r = await api("/api/action", {integration: id, action: "authorize"});
      if (r.url && w){ w.location = r.url; } else if (w) { w.close(); }
      notes[id] = {ok: !!r.ok, text: r.summary || ""};
    }
  } finally { await refresh(); }
});
document.querySelector(".tabs").addEventListener("click", e => { const t = e.target.closest(".tab"); if (t) showTab(t.dataset.tab); });
document.getElementById("settings").addEventListener("click", async e => {
  const btn = e.target.closest("button[data-mode]"); if (!btn) return;
  for (const b of document.querySelectorAll("#settings button")) b.disabled = true;
  try { state.settings = await api("/api/settings", {publish_mode: btn.dataset.mode}); } finally { renderSettings(); }
});
showTab(location.hash === "#settings" ? "settings" : "integrations");
document.getElementById("finish").addEventListener("click", async () => { await api("/api/finish", {}); document.body.innerHTML = '<main><div class="brand"><img src="/logo.png" alt="3DAPI"><h1>Done</h1></div><p style="color:var(--mute)">You can close this tab.</p></main>'; });
refresh();
setInterval(() => api("/api/ping").catch(() => {}), 3000);                       // heartbeat: the server ends when it stops
addEventListener("pagehide", () => navigator.sendBeacon("/api/bye", new Blob([JSON.stringify({token: TOKEN})], {type: "application/json"})));
</script></body></html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = UA
    token = ""
    last_seen = time.time()   # any request: the idle timeout
    opened = False            # the page was loaded at least once
    last_ping = 0.0           # the page's heartbeat, every 3 s while a tab is open
    bye_at = 0.0              # the page said it was closing; a reload sends a ping within the grace period

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
            Handler.opened, Handler.last_ping, Handler.bye_at = True, time.time(), 0.0
            return self._send(200, PAGE.replace("%%TOKEN%%", self.token).replace("%%VERSION%%", plugin_version()).encode(),
                              "text/html; charset=utf-8")
        if self.path == "/api/ping" and self.headers.get("x-token") == self.token:
            Handler.last_ping, Handler.bye_at = time.time(), 0.0
            return self._json(200, {"ok": True})
        if self.path == "/logo.png" and LOGO.exists():
            return self._send(200, LOGO.read_bytes(), "image/png")
        if self.path == "/api/state" and self.headers.get("x-token") == self.token:
            return self._json(200, state())
        self._json(404, {"error": "not found"})

    def do_POST(self):
        Handler.last_seen = time.time()
        if not self._local() or (self.headers.get("x-token") != self.token and self.path != "/api/bye"):
            return self._json(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad json"})
        if self.path == "/api/bye":   # sent by the closing tab as a beacon, which cannot set headers: the token rides in the body
            if body.get("token") == self.token:
                Handler.bye_at = time.time()
            return self._json(200, {"ok": True})
        if self.path == "/api/settings":
            try:
                return self._json(200, write_settings(body))
            except ValueError as e:
                return self._json(400, {"error": str(e)})
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


def _read_version() -> str:
    try:
        return json.loads((HERE.parent / ".claude-plugin" / "plugin.json").read_text()).get("version") or "0"
    except (OSError, ValueError):
        return "0"


VERSION = _read_version()   # fixed when the process starts: a running server reports the code it loaded, not the file on disk


def plugin_version() -> str:
    return VERSION


def our_page(port: int) -> tuple[str, str] | None:
    """(version, token) when an Integrations page from some run of this script is on the port, else None.
    An older run reports its version as "old": it predates the version tag."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            html = r.read(65536).decode(errors="replace")
    except (OSError, ValueError):
        return None
    if "Integrations</title>" not in html or 'TOKEN = "' not in html:
        return None
    m = re.search(r'<meta name="dapilot-integrate" content="([^"]*)">', html)
    tok = re.search(r'TOKEN = "([^"]*)"', html)
    return (m.group(1) if m else "old"), (tok.group(1) if tok else "")


def stop_page(port: int, token: str) -> None:
    """Ask an earlier run on this port to finish, then wait for the port to free."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/finish", data=b"{}", method="POST",
                                 headers={"content-type": "application/json", "x-token": token})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except (OSError, ValueError):
        pass
    for _ in range(20):
        if our_page(port) is None:
            return
        time.sleep(0.2)


WAIT_BUDGET = 540   # seconds a --wait may hold a command: the Bash tool allows 600


def wait_then_status(port: int) -> int:
    """Hold until the page on `port` is closed (Done, or the tab closed), then print the table."""
    deadline = time.time() + WAIT_BUDGET
    while time.time() < deadline:
        if our_page(port) is None:
            return status(argparse.Namespace(json=False, all=False))
        time.sleep(2)
    print("The page is still open. Say when you are done and the connections will be checked again.")
    return 0


def serve(args) -> int:
    store = store_dir()
    store.mkdir(parents=True, exist_ok=True)
    if not args.foreground:
        for port in range(args.port, args.port + 6):   # a busy port is skipped; this version's page is reused, an older one replaced
            url = f"http://127.0.0.1:{port}/"
            found = our_page(port)
            if found and found[0] == plugin_version():
                if not args.no_browser:
                    webbrowser.open(url)
                print(json.dumps({"ok": True, "url": url, "store": str(store), "note": "the page was already open; reused"}) if not args.wait
                      else f"Your Integrations page is open: {url}\nFill in each account there, then close the page or press Done.\n", flush=True)
                return wait_then_status(port) if args.wait else 0
            if found:
                stop_page(port, found[1])   # an earlier version's page: replaced by this one on the same port
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", "--foreground", "--port", str(port)]
                                     + (["--no-browser"] if args.no_browser else []),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True, env={**os.environ, "DAPILOT_STORE_DIR": str(store)})
            time.sleep(0.6)
            if child.poll() is None:
                print(json.dumps({"ok": True, "url": url, "store": str(store),
                                  "note": "the page runs until its tab is closed; then run `integrate.py status`"}) if not args.wait
                      else f"Your Integrations page is open: {url}\nFill in each account there, then close the page or press Done.\n", flush=True)
                return wait_then_status(port) if args.wait else 0
        print(json.dumps({"ok": False, "reason": f"ports {args.port} to {args.port + 5} are all in use"}) if not args.wait
              else f"The page could not start: ports {args.port} to {args.port + 5} are all in use. Try: integrate.py serve --wait --port {args.port + 6}")
        return 2
    Handler.token = secrets.token_urlsafe(24)
    try:
        srv = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        print(json.dumps({"ok": False, "reason": f"port {args.port} is in use"}), file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{args.port}/"

    def idle_watch():
        """The server lives as long as the page: it ends 4 s after the tab says goodbye (a reload pings again
        within that), or 12 s after the heartbeat stops (a crashed tab), or after the idle time if the page
        was never opened. Finish on the page ends it at once."""
        while True:
            time.sleep(1)
            now = time.time()
            if Handler.opened:
                if Handler.bye_at and now - Handler.bye_at > 4 and Handler.last_ping < Handler.bye_at:
                    srv.shutdown(); return
                if now - Handler.last_ping > 12:
                    srv.shutdown(); return
            if now - Handler.last_seen > IDLE_SECONDS:
                srv.shutdown(); return
    threading.Thread(target=idle_watch, daemon=True).start()
    if not args.no_browser:
        threading.Timer(0.3, webbrowser.open, [url]).start()
    srv.serve_forever()
    end_login()   # Finish or the idle timeout: no sign-in listener outlives the page
    return 0


def banner(args) -> int:
    """The wordmark, navy when stdout is a terminal. The command shows it inside a code block."""
    colour = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    print((NAVY + BANNER + "\033[0m") if colour else BANNER, end="")
    return 0


def status(args) -> int:
    st = state()
    if args.json:
        print(json.dumps(st, indent=1))
    else:
        rows = [i for i in st["integrations"] if args.all or (not i["hidden"] and (i["required"] or i["status"] != "empty"))]
        w = max(len(i["name"]) for i in rows)
        for i in rows:
            mark = {"done": "●", "failed": "✕", "untested": "○", "empty": " "}[i["status"]]
            phrase = {"done": "connected", "failed": "check failed", "untested": "saved, not tested", "empty": "not connected"}[i["status"]]
            r = i["result"]
            extra = f"  {r['detail']}" if i["status"] == "done" and r.get("detail") else (f"  {r['summary']}" if i["status"] == "failed" and r.get("summary") else "")
            print(f"{mark} {i['name']:<{w}}  {phrase}{'' if i['required'] else '  (optional)'}{extra}")
        mode = st["settings"]["publish_mode"]
        print(f"Publishing: {mode}" + ("  (nothing is created on Etsy or posted)" if mode == "sandbox" else ""))
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
    s.add_argument("--wait", action="store_true", help="after opening the page, wait until it is closed, then print the status table")
    st = sub.add_parser("status")
    st.add_argument("--json", action="store_true")
    st.add_argument("--all", action="store_true", help="every integration, hidden and unset optional ones included")
    t = sub.add_parser("test")
    t.add_argument("--integration", choices=[i["id"] for i in INTEGRATIONS])
    sub.add_parser("banner", help="print the 3DAPI wordmark (navy when stdout is a terminal)")
    a = ap.parse_args()
    return {"serve": serve, "status": status, "test": test, "banner": banner}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
