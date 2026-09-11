#!/usr/bin/env python
"""Etsy OAuth 2.0 (authorization code + PKCE) for a local CLI.

One-time browser login, then silent refresh. The token file lives at
$DAPILOT_STORE_DIR/etsy_token.json unless --token-file says otherwise; it is a
credential and must stay out of git. The keystring comes from ETSY_KEYSTRING in
the environment or --env.

Moved from the repo's scripts/ on 2026-09-09 when research-comparables started
depending on it; the copy there is the development original.

    scripts/etsy_auth.py login  --env store/.env [--scopes "listings_r shops_r"] [--port 3003]
    scripts/etsy_auth.py token  --env store/.env       # print a valid access token, refreshing if needed
    scripts/etsy_auth.py whoami --env store/.env       # GET /users/me with the token
    scripts/etsy_auth.py refresh --env store/.env

Before `login`, register exactly this callback URL on your app at
https://www.etsy.com/developers/your-apps :   http://localhost:<port>/oauth/redirect
"""
import argparse
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
API = "https://openapi.etsy.com/v3/application"
DEFAULT_SCOPES = "listings_r shops_r"

def default_token_file():
    d = os.environ.get("DAPILOT_STORE_DIR")
    return os.path.join(d, "etsy_token.json") if d else None


def die(msg, code=1):
    print(json.dumps({"ok": False, "error": msg}), file=sys.stderr)
    sys.exit(code)


def load_env(path):
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def keystring():
    ks = os.environ.get("ETSY_KEYSTRING")
    if not ks:
        die("ETSY_KEYSTRING must be set (or pass --env store/.env)")
    return ks


def api_key_header():
    ks, sec = os.environ.get("ETSY_KEYSTRING"), os.environ.get("ETSY_SHARED_SECRET")
    if not ks or not sec:
        die("ETSY_KEYSTRING and ETSY_SHARED_SECRET must be set")
    return f"{ks}:{sec}"


def post_token(body):
    data = urllib.parse.urlencode(body).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        die(f"token endpoint HTTP {e.code}: {e.read()[:400].decode(errors='replace')}")
    tok["obtained_at"] = int(time.time())
    tok["expires_at"] = tok["obtained_at"] + int(tok.get("expires_in", 3600))
    return tok


def save(tok, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(tok, f, indent=1)
    os.chmod(path, 0o600)


def load(path):
    try:
        return json.load(open(path))
    except FileNotFoundError:
        die(f"{path} not found; run `etsy_auth.py login` first")


def login(args):
    ks = keystring()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    redirect = f"http://localhost:{args.port}/oauth/redirect"
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": ks,
        "redirect_uri": redirect,
        "scope": args.scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path != "/oauth/redirect":
                self.send_response(404); self.end_headers(); return
            q = dict(urllib.parse.parse_qsl(u.query))
            got.update(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            msg = "Etsy authorised. You can close this tab." if "code" in q else f"Etsy returned an error: {q}"
            self.wfile.write(msg.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("localhost", args.port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    print(f"Callback URL that must be registered on the app: {redirect}", file=sys.stderr)
    print("Opening the browser. If it does not open, visit:\n" + url, file=sys.stderr)
    webbrowser.open(url)

    deadline = time.time() + args.timeout
    while "code" not in got and "error" not in got and time.time() < deadline:
        time.sleep(0.2)
    srv.shutdown()

    if "code" not in got:
        die(f"no authorization code received: {got or 'timed out'}")
    if got.get("state") != state:
        die("state mismatch; refusing the code")

    tok = post_token({
        "grant_type": "authorization_code",
        "client_id": ks,
        "redirect_uri": redirect,
        "code": got["code"],
        "code_verifier": verifier,
    })
    tok["scope"] = tok.get("scope") or args.scopes
    save(tok, args.token_file)
    print(json.dumps({"ok": True, "token_file": args.token_file, "user_id": tok["access_token"].split(".")[0],
                      "scope": tok["scope"], "expires_in": tok.get("expires_in")}))


def refresh(args, tok=None):
    tok = tok or load(args.token_file)
    new = post_token({
        "grant_type": "refresh_token",
        "client_id": keystring(),
        "refresh_token": tok["refresh_token"],
    })
    new["scope"] = new.get("scope") or tok.get("scope")
    save(new, args.token_file)
    return new


def valid_token(args):
    tok = load(args.token_file)
    if time.time() > tok.get("expires_at", 0) - 60:
        tok = refresh(args, tok)
    return tok


def cmd_token(args):
    print(valid_token(args)["access_token"])


def cmd_refresh(args):
    new = refresh(args)
    print(json.dumps({"ok": True, "expires_in": new.get("expires_in"), "scope": new.get("scope")}))


def cmd_whoami(args):
    tok = valid_token(args)
    req = urllib.request.Request(f"{API}/users/me", headers={
        "x-api-key": api_key_header(),
        "Authorization": f"Bearer {tok['access_token']}",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(json.dumps(json.load(r), indent=1))
    except urllib.error.HTTPError as e:
        die(f"HTTP {e.code}: {e.read()[:400].decode(errors='replace')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="path to a .env file holding ETSY_KEYSTRING and ETSY_SHARED_SECRET")
    ap.add_argument("--token-file", help="default: $DAPILOT_STORE_DIR/etsy_token.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("login")
    l.add_argument("--scopes", default=DEFAULT_SCOPES, help="space-separated Etsy scopes")
    l.add_argument("--port", type=int, default=3003)
    l.add_argument("--timeout", type=int, default=300)
    sub.add_parser("token")
    sub.add_parser("refresh")
    sub.add_parser("whoami")
    args = ap.parse_args()
    if args.env:
        load_env(args.env)
    args.token_file = args.token_file or default_token_file()
    if not args.token_file:
        die("set DAPILOT_STORE_DIR or pass --token-file: the OAuth token has to live somewhere outside the plugin")
    {"login": login, "token": cmd_token, "refresh": cmd_refresh, "whoami": cmd_whoami}[args.cmd](args)


if __name__ == "__main__":
    main()
