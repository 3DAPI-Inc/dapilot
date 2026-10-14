"""Instagram through Meta's own API (Instagram API with Instagram Login). Free, and no app
review for an account that holds a role on the app: a Meta app in development mode with the
shop's Business or Creator account added as Instagram Tester.

Session: a long-lived user token, valid 60 days, renewable once it is a day old, so a
keepalive that runs at least every few weeks keeps it alive for good. It is obtained by
`social_post.py session login --platform instagram`, one of two ways:

  OAuth    INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET are set (the app's Instagram app id and
           secret). The browser opens Instagram's consent page, a person signs in and allows,
           a loopback HTTPS listener catches the code, and it is exchanged for the 60-day
           token. Register the redirect URI on the app first, under Business login settings:
           https://localhost:3004/oauth/instagram . The listener's certificate is self-signed,
           so the browser warns once; if it refuses, copy the address it was sent to and pass
           it with --redirected-url.
  Pasted   only INSTAGRAM_ACCESS_TOKEN is set: a token generated in the app dashboard.

A token that can no longer be renewed (revoked, or left alone for 60 days) needs a person's
consent again; `session keepalive --reconnect` opens that page by itself when it finds one.

Posting is container, wait, publish. Instagram fetches the media itself, so every file needs
a public URL; ours are the listing's own photos and video on Etsy's CDN, from publish.json.
The container id is checkpointed before media_publish, the one call that cannot be undone.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import ssl
import subprocess
import threading
import time
import urllib.error
import webbrowser
import urllib.parse
import urllib.request
from pathlib import Path

from .base import (CHALLENGE, EXPIRING, NEEDS_LOGIN, OK, Integration, Limits, Post, Published, Session,
                   SessionStatus, SocialError)

HOST = "https://graph.instagram.com"
AUTHORIZE = "https://www.instagram.com/oauth/authorize"
CODE_EXCHANGE = "https://api.instagram.com/oauth/access_token"
REDIRECT = "https://localhost:3004/oauth/instagram"
SCOPES = "instagram_business_basic,instagram_business_content_publish,instagram_business_manage_insights"
RENEW_WHEN_DAYS_LEFT = 14
CONTAINER_WAIT_S = 300


def call(method: str, path: str, params: dict, version: str | None, timeout: int = 60) -> dict:
    base = f"{HOST}/{version}" if version else HOST
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{base}{path}"
    req = urllib.request.Request(f"{url}?{query}" if method == "GET" else url, method=method,
                                 data=query.encode() if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()[:600].decode(errors="replace")
        try:
            err = json.loads(body).get("error", {})
        except ValueError:
            err = {"message": body}
        # 190: the token is invalid, expired or revoked. 10 and 200: a permission is missing.
        code = 4 if err.get("code") == 190 else 1
        raise SocialError(f"instagram HTTP {e.code} on {path}: {err.get('message')}", code, error=err)


class InstagramSession(Session):
    platform = "instagram"

    def __init__(self, account: str, store_dir: Path, version: str | None = None):
        super().__init__(account, store_dir)
        self.version = version

    def token(self) -> str:
        tok = self.load().get("access_token")
        if not tok:
            raise SocialError(self.login_help(), 4)
        return tok

    def _catch_code(self, state: str, timeout: int) -> str:
        """Open the consent page and wait for Instagram to send the browser back to our loopback listener."""
        port = urllib.parse.urlparse(REDIRECT).port
        cert, key = self.dir / "loopback.crt", self.dir / "loopback.key"
        self.dir.mkdir(parents=True, exist_ok=True)
        if not cert.exists():
            made = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
                                   "-subj", "/CN=localhost", "-keyout", str(key), "-out", str(cert)], capture_output=True)
            if made.returncode:
                raise SocialError("openssl could not make the loopback certificate; use --redirected-url", 2)
            key.chmod(0o600)
        caught: dict = {}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                caught.update(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Instagram is connected. You can close this tab.")

            def log_message(self, *_):
                pass

        server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = AUTHORIZE + "?" + urllib.parse.urlencode({"client_id": os.environ["INSTAGRAM_APP_ID"], "redirect_uri": REDIRECT,
                                                         "response_type": "code", "scope": SCOPES, "state": state})
        webbrowser.open(url)
        deadline = time.time() + timeout
        while "code" not in caught and "error" not in caught and time.time() < deadline:
            time.sleep(0.5)
        server.shutdown()
        if "code" not in caught:
            raise SocialError(f"no consent arrived: {caught.get('error_description') or 'timed out'}. Open this yourself, allow, "
                              f"and pass the address you land on with --redirected-url: {url}", 4)
        if caught.get("state", [None])[0] != state:
            raise SocialError("the consent came back with a different state; refusing it", 1)
        return caught["code"][0]

    def oauth_login(self, redirected_url: str | None = None, timeout: int = 300) -> SessionStatus:
        app_id, secret = os.environ.get("INSTAGRAM_APP_ID"), os.environ.get("INSTAGRAM_APP_SECRET")
        if not app_id or not secret:
            raise SocialError("INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET must be set for the OAuth login", 2)
        if redirected_url:
            code = (urllib.parse.parse_qs(urllib.parse.urlparse(redirected_url).query).get("code") or [None])[0]
            if not code:
                raise SocialError("that address carries no code", 2)
        else:
            code = self._catch_code(secrets.token_urlsafe(16), timeout)
        code = code.split("#")[0]
        body = urllib.parse.urlencode({"client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
                                       "redirect_uri": REDIRECT, "code": code}).encode()
        try:
            with urllib.request.urlopen(urllib.request.Request(CODE_EXCHANGE, data=body), timeout=60) as r:
                short = json.load(r)
        except urllib.error.HTTPError as e:
            raise SocialError(f"instagram refused the code: {e.read()[:300].decode(errors='replace')}", 1)
        short = (short.get("data") or [short])[0]  # the answer is wrapped in data[] on newer API versions
        long = call("GET", "/access_token", {"grant_type": "ig_exchange_token", "client_secret": secret,
                                             "access_token": short["access_token"]}, self.version)
        return self._store(long["access_token"], int(long.get("expires_in") or 60 * 86400), "oauth")

    def _store(self, tok: str, expires_in: int, how: str) -> SessionStatus:
        me = call("GET", "/me", {"fields": "user_id,username,account_type", "access_token": tok}, self.version)
        self.save({"access_token": tok, "user_id": me.get("user_id"), "username": me.get("username"),
                   "account_type": me.get("account_type"), "obtained_at": time.time(), "obtained_by": how,
                   "expires_at": time.time() + expires_in})
        return self.status()

    def login(self, redirected_url: str | None = None) -> SessionStatus:
        if os.environ.get("INSTAGRAM_APP_ID"):
            return self.oauth_login(redirected_url)
        tok = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
        if not tok:
            raise SocialError("set INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET for the sign-in flow, or "
                              "INSTAGRAM_ACCESS_TOKEN for a pasted token. " + self.login_help(), 2)
        me = call("GET", "/me", {"fields": "user_id,username,account_type", "access_token": tok}, self.version)
        # The dashboard's token is already long-lived; its true expiry is learned at the first refresh.
        self.save({"access_token": tok, "user_id": me.get("user_id"), "username": me.get("username"),
                   "account_type": me.get("account_type"), "obtained_at": time.time(),
                   "expires_at": time.time() + 60 * 86400})
        return self.status()

    def status(self) -> SessionStatus:
        doc = self.load()
        if not doc.get("access_token"):
            return SessionStatus(NEEDS_LOGIN, self.account, self.platform, note=self.login_help())
        try:
            call("GET", "/me", {"fields": "user_id", "access_token": doc["access_token"]}, self.version)
        except SocialError as e:
            state = NEEDS_LOGIN if e.code == 4 else CHALLENGE
            return SessionStatus(state, self.account, self.platform, doc.get("expires_at"), note=e.reason)
        left = (doc.get("expires_at") or 0) - time.time()
        state = EXPIRING if left < RENEW_WHEN_DAYS_LEFT * 86400 else OK
        return SessionStatus(state, self.account, self.platform, doc.get("expires_at"),
                             note=f"@{doc.get('username')}, {left / 86400:.0f} days left")

    def refresh(self) -> SessionStatus:
        doc = self.load()
        if time.time() - (doc.get("obtained_at") or 0) < 86400:
            return self.status()  # Instagram refuses to renew a token younger than a day
        res = call("GET", "/refresh_access_token",
                   {"grant_type": "ig_refresh_token", "access_token": self.token()}, self.version)
        doc.update(access_token=res["access_token"], obtained_at=time.time(),
                   expires_at=time.time() + int(res.get("expires_in") or 0))
        self.save(doc)
        return self.status()

    def login_help(self) -> str:
        if os.environ.get("INSTAGRAM_APP_ID"):
            return "run: social_post.py session login --platform instagram  (opens Instagram's sign-in page)"
        return ("Meta app dashboard, Instagram product, 'Generate token' for the tester account; then run "
                "INSTAGRAM_ACCESS_TOKEN=<token> social_post.py session login --platform instagram. With "
                "INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET set, the same command signs in through the browser instead")


class Instagram(Integration):
    platform = "instagram"
    kind = "api"
    limits = Limits(caption_max=2200, hashtags_max=30, links_clickable=False, formats={
        "reel": {"media": "video", "min": 1, "max": 1},
        "image": {"media": "image", "min": 1, "max": 1},
        "carousel": {"media": "image", "min": 2, "max": 10},
    })

    def _ids(self) -> tuple[str, str, str | None]:
        doc = self.session.load()
        return str(doc.get("user_id")), self.session.token(), self.config.get("api_version")

    def _container(self, params: dict) -> str:
        uid, tok, ver = self._ids()
        return call("POST", f"/{uid}/media", {**params, "access_token": tok}, ver)["id"]

    def _wait(self, container: str) -> None:
        _, tok, ver = self._ids()
        deadline = time.time() + CONTAINER_WAIT_S
        while True:
            st = call("GET", f"/{container}", {"fields": "status_code,status", "access_token": tok}, ver)
            if st.get("status_code") in ("FINISHED", "PUBLISHED"):
                return
            if st.get("status_code") in ("ERROR", "EXPIRED"):
                raise SocialError(f"instagram could not process the media: {st.get('status')}", 1)
            if time.time() > deadline:
                raise SocialError("instagram is still processing the media; run post again", 3)
            time.sleep(5)

    def submit(self, post: Post, save, resume: dict | None = None) -> dict:
        uid, tok, ver = self._ids()
        quota = call("GET", f"/{uid}/content_publishing_limit", {"fields": "quota_usage,config", "access_token": tok}, ver)
        row = (quota.get("data") or [{}])[0]
        if row.get("quota_usage", 0) >= (row.get("config") or {}).get("quota_total", 100):
            raise SocialError("instagram's 24 hour publishing limit is used up", 3)

        container = (resume or {}).get("container_id")
        if not container:
            text = post.text()
            if post.format == "reel":
                container = self._container({"media_type": "REELS", "video_url": post.media[0].url, "caption": text,
                                             "share_to_feed": "true"})
            elif post.format == "image":
                container = self._container({"image_url": post.media[0].url, "caption": text,
                                             "alt_text": post.media[0].alt_text})
            else:
                children = [self._container({"image_url": m.url, "is_carousel_item": "true", "alt_text": m.alt_text})
                            for m in post.media]
                for child in children:
                    self._wait(child)
                container = self._container({"media_type": "CAROUSEL", "children": ",".join(children), "caption": text})
        checkpoint = {"container_id": container, "submitted_at": time.time()}
        save(checkpoint)
        self._wait(container)
        res = call("POST", f"/{uid}/media_publish", {"creation_id": container, "access_token": tok}, ver)
        checkpoint["media_id"] = res.get("id")
        save(checkpoint)
        return checkpoint

    def confirm(self, post: Post, submitted: dict) -> Published | None:
        uid, tok, ver = self._ids()
        media_id = submitted.get("media_id")
        if not media_id:
            # A crash between media_publish and the checkpoint: the container says whether it went out.
            st = call("GET", f"/{submitted['container_id']}", {"fields": "status_code", "access_token": tok}, ver)
            if st.get("status_code") != "PUBLISHED":
                return None
            recent = call("GET", f"/{uid}/media", {"fields": "id,caption,timestamp", "limit": 10, "access_token": tok}, ver)
            media_id = next((m["id"] for m in recent.get("data", []) if (m.get("caption") or "") == post.text()), None)
            if not media_id:
                return None
        m = call("GET", f"/{media_id}", {"fields": "permalink,timestamp,media_type", "access_token": tok}, ver)
        return Published(post_id=str(media_id), url=m.get("permalink"), extra={"media_type": m.get("media_type")})

    def metrics(self, published: dict) -> dict:
        _, tok, ver = self._ids()
        res = call("GET", f"/{published['post_id']}/insights",
                   {"metric": "views,reach,likes,comments,saved,shares", "access_token": tok}, ver)
        return {row["name"]: (row.get("values") or [{}])[0].get("value") for row in res.get("data", [])}


def build(account: str, store_dir: Path, config: dict) -> Integration:
    return Instagram(InstagramSession(account, store_dir, config.get("api_version")), config)
