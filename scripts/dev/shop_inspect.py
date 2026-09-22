#!/usr/bin/env python3
"""Read-only inspection for scripts/dev/shop.sh: is the setup ready, and where is each item.

  shop_inspect.py check [--offline]        env vars, files, tools, live credentials, per stage
  shop_inspect.py status [ITEM ...] [--run RUN]   every stage of every item, from the files on disk
  shop_inspect.py gate ITEM                 the price proposal a person approves or overrides

Changes nothing on disk except what a credential check does on its own (etsy_auth.py
whoami refreshes an expired Etsy token, as any Etsy call would).

check exits 0 when every enabled stage can run, 1 when some stage is blocked,
2 when nothing can start (no python deps, no dapi key, no claude CLI).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "dapilot"
SCRIPTS = PLUGIN / "scripts"
STAGES_FILE = PLUGIN / "skills" / "run-intake" / "reference" / "stages.json"
WORK = ROOT / "work"
TTY = (sys.stdout.isatty() or bool(os.environ.get("FORCE_COLOR"))) and not os.environ.get("NO_COLOR")


def c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if TTY else s


OK, BAD, WARN, DIM = (lambda s: c("32", s)), (lambda s: c("31", s)), (lambda s: c("33", s)), (lambda s: c("2", s))


def read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None


def load_env() -> Path:
    """store/.env, never over what the shell already set. Same rule as run_intake.py."""
    store = Path(os.environ.get("DAPILOT_STORE_DIR") or ROOT / "store")
    if not store.is_absolute():
        store = (ROOT / store).resolve()
    env = store / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #", 1)[0].strip().strip('"').strip("'")
                if v and k.strip() not in os.environ:
                    os.environ[k.strip()] = v
    os.environ["DAPILOT_STORE_DIR"] = str(store)
    return store


def stages() -> list[dict]:
    return read(STAGES_FILE)["stages"]


# ------------------------------------------------------------------ check

PLACEHOLDER = re.compile(r"^(<.*>|x+|changeme|todo|your[-_ ].*|\.\.\.)$", re.I)

# name, stages it gates, required, secret, note
ENV = [
    ("DAPI_API_URL", ["quote"], True, False, "origin of the public API, no /v1"),
    ("DAPI_API_KEY", ["quote"], True, True, "scoped key, PRICING for intake"),
    ("DAPI_STORE_ID", ["quote"], False, False, "only when the dapi account has more than one store"),
    ("ANTHROPIC_API_KEY", ["workers"], False, True, "unset: workers bill to the logged-in Claude subscription"),
    ("CLAUDE_CODE_OAUTH_TOKEN", ["workers"], False, True, "claude setup-token; signs workers in without a browser login"),
    ("DAPILOT_BILLING", ["workers"], False, False, "auto | api | subscription"),
    ("ETSY_KEYSTRING", ["research", "publish"], True, True, "Etsy Open API v3 app"),
    ("ETSY_SHARED_SECRET", ["research", "publish"], True, True, "Etsy Open API v3 app"),
    ("BLENDER", ["preview"], False, False, "Blender binary; default /Applications/Blender.app"),
    ("FAL_KEY", ["design"], True, True, "or FAL_API_KEY; lifestyle photos and clip, paid"),
    ("CHROME", ["design"], False, False, "Chrome binary for studio captures; found on its own on macOS"),
    ("DAPILOT_STUDIO_URL", ["design"], False, False, "default http://localhost:8081"),
    ("INSTAGRAM_ACCESS_TOKEN", ["publish"], False, True, "or INSTAGRAM_APP_ID + INSTAGRAM_APP_SECRET; the session file is what counts"),
    ("DAPILOT_SOCIAL_ADAPTER_DIR", ["publish"], False, False, "browser adapters (X, TikTok, LinkedIn), outside the plugin"),
]


class Checks:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []   # group, name, state, detail
        self.blocks: dict[str, list[str]] = {}             # stage -> what blocks it

    def add(self, group: str, name: str, state: str, detail: str, gates: list[str] | None = None) -> None:
        self.rows.append((group, name, state, detail))
        if state == "missing":
            for g in gates or []:
                self.blocks.setdefault(g, []).append(name)


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return p.returncode, (p.stdout.strip() or p.stderr.strip())
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def last_json(text: str) -> dict:
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    try:
        return json.loads(text)
    except ValueError:
        return {}


def which_all(name: str) -> list[str]:
    seen = []
    for d in os.environ.get("PATH", "").split(os.pathsep):
        f = os.path.join(d, name)
        if os.access(f, os.X_OK) and f not in seen:
            seen.append(f)
    return seen


def version(cmd: list[str]) -> str:
    code, out = run(cmd, timeout=20)
    line = out.splitlines()[0] if out else ""
    return line[:60] if code == 0 and line else "version unknown"


def missing_keys(example: dict, actual: dict, prefix: str = "") -> list[str]:
    out = []
    for k, v in example.items():
        if k not in actual:
            out.append(prefix + k)
        elif isinstance(v, dict) and isinstance(actual[k], dict) and k not in ("palette", "platforms"):
            out += missing_keys(v, actual[k], f"{prefix}{k}.")
    return out


def reachable(url: str) -> bool:
    try:
        urllib.request.urlopen(url, timeout=3)
        return True
    except Exception:
        return False


def check(offline: bool) -> int:
    store = load_env()
    ck = Checks()
    py = os.environ.get("DAPILOT_PYTHON") or str(ROOT / ".venv" / "bin" / "python")
    py = str((ROOT / py).resolve()) if not Path(py).is_absolute() else py
    cfg = read(store / "config.json") or {}
    enabled = [s["stage"] for s in stages() if s.get("enabled")]

    # files
    ck.add("files", "store dir", "ok" if store.is_dir() else "missing", str(store), ["all"])
    ck.add("files", "store/.env", "ok" if (store / ".env").exists() else "missing",
           str(store / ".env") if (store / ".env").exists() else "cp store/.env.example store/.env", ["all"])
    ck.add("files", "config.json", "ok" if cfg else "missing",
           str(store / "config.json") if cfg else "cp store/config.example.json store/config.json", ["all"])
    pol = store / "reference" / "pricing-policy.json"
    ck.add("files", "pricing-policy", "ok" if read(pol) else "missing", str(pol) if pol.exists() else "scripts/dev/shop.sh setup copies store/pricing-policy.example.json", ["research", "price"])
    fees = store / "reference" / "platform-fees.json"
    fees = fees if fees.exists() else PLUGIN / "skills" / "research-comparables" / "reference" / "platform-fees.json"
    ck.add("files", "platform-fees", "ok" if fees.exists() else "missing", str(fees.relative_to(ROOT) if fees.is_relative_to(ROOT) else fees), ["research", "price"])
    if cfg and read(ROOT / "store" / "config.example.json"):
        drift = missing_keys(read(ROOT / "store" / "config.example.json"), cfg)
        ck.add("files", "config keys", "warn" if drift else "ok",
               ("in config.example.json, not in config.json (the scripts use their defaults): " + ", ".join(drift[:12])
                + (f" and {len(drift) - 12} more" if len(drift) > 12 else "")) if drift else "every key of config.example.json is set")
    listing = read(store / "reference" / "etsy-listing.json")
    unset = [k for k in ("shipping_profile_id", "return_policy_id", "readiness_state_id") if not (listing or {}).get(k)]
    ck.add("files", "etsy-listing.json", "missing" if listing is None else "warn" if unset else "ok",
           "no shop-wide listing defaults; scripts/dev/shop.sh setup writes them after the Etsy sign-in" if listing is None
           else f"{', '.join(unset)} not set: a draft works, activating fails; etsy_listing.py profiles lists the ids" if unset
           else f"shipping {listing['shipping_profile_id']}, returns {listing['return_policy_id']}", ["publish"])
    seller = store / "reference" / "etsy_seller_taxonomy.json"
    ck.add("files", "seller taxonomy", "ok" if seller.exists() else "warn",
           "present" if seller.exists() else "missing: copy checks the category id without its path; shop.sh setup fetches it")
    tok = store / "etsy_token.json"
    ck.add("files", "etsy_token.json", "ok" if tok.exists() else "missing",
           str(tok) if tok.exists() else "run scripts/dev/shop.sh setup in a terminal: it opens the Etsy sign-in", ["research", "publish"])

    # tools
    code, out = run([py, "-c", "import trimesh, scipy, networkx, jsonschema, fastapi, uvicorn, sys; print(sys.version.split()[0], 'trimesh', trimesh.__version__)"])
    ck.add("tools", "python venv", "ok" if code == 0 else "missing",
           f"{py} ({out})" if code == 0 else f"{py}: {out.splitlines()[-1] if out else 'not found'}; scripts/dev/shop.sh setup",
           ["all"])
    code, out = run([py, "-c", "import fal_client; print(getattr(fal_client, '__version__', 'present'))"])
    ck.add("tools", "fal-client", "ok" if code == 0 else "missing", f"fal_client {out}" if code == 0 else "scripts/dev/shop.sh setup", ["design"])
    claude = shutil.which("claude")
    ck.add("tools", "claude CLI", "ok" if claude else "missing",
           f"{claude} ({version([claude, '--version'])})" if claude else "install Claude Code; every worker is claude -p", ["all"])
    blender = next((b for b in (os.environ.get("BLENDER"), "/Applications/Blender.app/Contents/MacOS/Blender", shutil.which("blender"),
                                "/snap/bin/blender", "/opt/blender/blender") if b and os.access(b, os.X_OK)), None)
    bv = version([blender, "--version"]) if blender else ""
    bm = re.match(r"Blender (\d+)\.", bv)
    ck.add("tools", "blender", "ok" if bm and int(bm[1]) >= 4 else "missing",
           f"{blender} ({bv})" if bm and int(bm[1]) >= 4
           else f"{blender} ({bv}): the preview render needs Blender 4.0+; scripts/dev/shop.sh setup installs 4.5 LTS" if blender
           else "scripts/dev/shop.sh setup installs it, or set BLENDER", ["preview"])
    chrome = next((b for b in (os.environ.get("CHROME"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                               *(shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")))
                   if b and os.access(b, os.X_OK)), None)
    ck.add("tools", "chrome", "ok" if chrome else "missing",
           f"{chrome} ({version([chrome, '--version'])})" if chrome else "install Chrome or set CHROME", ["design"])
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        ck.add("tools", tool, "ok" if path else "missing", f"{path} ({version([path, '-version'])})" if path else "scripts/dev/shop.sh setup offers the install", ["design"])
    node = shutil.which("node")
    nv = version([node, "--version"]) if node else ""
    m = re.match(r"v(\d+)\.(\d+)", nv)
    node_ok = bool(m) and ((int(m[1]) == 20 and int(m[2]) >= 19) or (int(m[1]) == 22 and int(m[2]) >= 12) or int(m[1]) > 22)
    ck.add("tools", "node", "ok" if node_ok else "missing",
           f"{node} ({nv})" if node_ok else f"{node} ({nv}): vite 8 needs 20.19+ or 22.12+; scripts/dev/shop.sh setup installs one"
           + (f" (other node on PATH: {', '.join(others)})" if (others := [o for o in which_all('node') if o != node]) else "") if node
           else "scripts/dev/shop.sh setup installs it", ["design"])
    nm = (ROOT / "frontend" / "node_modules").is_dir()
    ck.add("tools", "frontend deps", "ok" if nm else "missing", "frontend/node_modules" if nm else "cd frontend && npm install", ["design"])

    # env
    for name, gates, required, secret, note in ENV:
        val = os.environ.get(name) or (os.environ.get("FAL_API_KEY") if name == "FAL_KEY" else None)
        if name == "INSTAGRAM_ACCESS_TOKEN" and not val and os.environ.get("INSTAGRAM_APP_ID") and os.environ.get("INSTAGRAM_APP_SECRET"):
            val = "app id + secret"
        if not val:
            ck.add("env", name, "missing" if required else "unset", note, gates if required else None)
            continue
        if PLACEHOLDER.match(val):
            ck.add("env", name, "missing", f"looks like a placeholder: {val!r}", gates)
            continue
        problem = None
        if name == "DAPI_API_URL":
            origin = val.rstrip("/").removesuffix("/v1")
            if not re.match(r"^https?://[^/]+$", origin):
                problem = f"{val}: give the origin only, e.g. https://api.example.com"
            elif origin != val:
                ck.add("env", name, "warn", f"{val}: works (dapi.py drops /v1), but the documented form is {origin}")
                continue
        if name == "DAPILOT_BILLING" and val not in ("auto", "api", "subscription"):
            problem = f"{val!r} is not auto, api or subscription"
        if name in ("BLENDER", "CHROME") and not os.access(val, os.X_OK):
            problem = f"{val} is not an executable"
        if problem:
            ck.add("env", name, "missing", problem, gates)
        else:
            ck.add("env", name, "ok", f"set, {len(val)} chars" if secret else val)

    # live
    if offline:
        ck.add("live", "credentials", "skipped", "--offline")
    else:
        if os.environ.get("DAPI_API_URL") and os.environ.get("DAPI_API_KEY"):
            code, out = run([py, str(SCRIPTS / "dapi.py"), "call", "getMe"])
            j = last_json(out)
            body = j.get("body") or j.get("data") or j
            who = body.get("name") or body.get("email") or body.get("keyName") or body.get("id") if isinstance(body, dict) else None
            ck.add("live", "dapi GET /me", "ok" if code == 0 else "missing",
                   f"{os.environ['DAPI_API_URL']} answers" + (f" as {who}" if who else "") + (f", {body.get('permissions')}" if isinstance(body, dict) and body.get("permissions") else "") if code == 0 else (j.get("reason") or j.get("error") or out)[:160],
                   ["quote"])
        if tok.exists() and os.environ.get("ETSY_KEYSTRING"):
            code, out = run([py, str(SCRIPTS / "etsy_auth.py"), "--env", str(store / ".env"), "whoami"])
            j = last_json(out)
            ck.add("live", "etsy /users/me", "ok" if code == 0 else "missing",
                   f"user {j.get('user_id')}, shop {j.get('shop_id')}" if code == 0 else out[-160:], ["research", "publish"])
        if tok.exists():
            scope = set(((read(tok) or {}).get("scope") or "").split())
            need = {"listings_r", "listings_w", "shops_r"}
            ck.add("live", "etsy token scope", "ok" if need <= scope else "missing", " ".join(sorted(scope)) if need <= scope else
                   f"has '{' '.join(sorted(scope))}', publish needs {' '.join(sorted(need))}; scripts/dev/shop.sh setup logs in again", ["publish"])
        if claude:
            code, out = run([claude, "auth", "status"])
            j = last_json(out)
            via = [n for n in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY") if os.environ.get(n)]
            ok = j.get("loggedIn") or bool(via)
            ck.add("live", "claude auth", "ok" if ok else "missing",
                   (" ".join(str(x) for x in (j.get("authMethod"), j.get("email") and f"as {j['email']}", j.get("subscriptionType")) if x) if j.get("loggedIn") else "not logged in")
                   + (f"; env: {', '.join(via)}" if via else "") + ("" if ok else "; claude auth login"), ["workers"])
        social = {p: v for p, v in ((cfg.get("social") or {}).get("platforms") or {}).items() if v.get("enabled")}
        if social and (cfg.get("publish") or {}).get("auto_post", True):
            code, out = run([py, str(SCRIPTS / "social_post.py"), "session", "status"])
            for s in last_json(out).get("sessions") or [{"platform": ",".join(social), "state": "error", "note": out[-160:]}]:
                usable = s.get("state") in ("ok", "expiring")   # SessionStatus.usable in social/base.py
                ck.add("live", f"{s.get('platform')} session", "ok" if usable else "missing",
                       f"{s.get('state')}: {s.get('note') or ''}" + ("" if usable else "; social_post.py session login"), ["publish"])
        api_up = reachable("http://localhost:3100/health")
        studio = os.environ.get("DAPILOT_STUDIO_URL") or (cfg.get("design") or {}).get("studio_url") or "http://localhost:8081"
        ck.add("live", "job API :3100", "ok" if api_up else "down", "running" if api_up else "shop.sh starts it")
        ck.add("live", "studio", "ok" if reachable(studio) else "down", studio + ("" if reachable(studio) else ", shop.sh starts it"))

    # print
    marks = {"ok": OK("ok     "), "warn": WARN("warn   "), "missing": BAD("MISSING"), "unset": DIM("unset  "), "down": WARN("down   "), "skipped": DIM("skipped")}
    group = None
    for g, name, state, detail in ck.rows:
        if g != group:
            print(f"\n  {g}")
            group = g
        print(f"    {marks.get(state, state):8} {name:24} {detail}")

    core = ck.blocks.get("all", [])
    print("\n  stages")
    blocked = 0
    for st in stages():
        s = st["stage"]
        if not st.get("enabled"):
            print(f"    {DIM('off    ')} {s}")
            continue
        why = core + ck.blocks.get(s, []) + (ck.blocks.get("workers", []) if "script" not in st else [])
        blocked += bool(why)
        print(f"    {(BAD('BLOCKED') if why else OK('ready  '))} {s:24} {('needs ' + ', '.join(dict.fromkeys(why))) if why else ''}")
    fatal = core or ck.blocks.get("quote")
    if fatal:
        print(BAD("\n  nothing can run until the MISSING lines above are fixed"))
        return 2
    if blocked:
        print(WARN(f"\n  {blocked} of {len(enabled)} stages blocked; an intake run stops at the first of them"))
        return 1
    print(OK("\n  every enabled stage can run"))
    return 0


# ------------------------------------------------------------------ status

def exists(wd: Path, pattern: str) -> bool:
    return any(wd.glob(pattern))


def item_status(wd: Path) -> tuple[list[tuple[str, str]], str]:
    """[(stage, mark)], one line saying where the item is. Mirrors server/app.py status_of."""
    lock = read(wd / ".lock") if (wd / ".lock").exists() else None
    rows, where = [], None
    for st in stages():
        s = st["stage"]
        if not st.get("enabled"):
            rows.append((s, "off")); continue
        if all(exists(wd, p) for p in st["produces"]):
            rows.append((s, "done")); continue
        mark, why = "todo", None
        if s == "analyze" and (wd / "analysis.error.json").exists():
            mark, why = "failed", (read(wd / "analysis.error.json") or {}).get("reason") or "analysis failed"
        elif s == "quote" and (wd / "quote.error.json").exists():
            q = read(wd / "quote.error.json") or {}
            mark, why = "failed", (q.get("raw") or {}).get("errorMessage") or q.get("status") or "slicer error"
        elif s == "quote" and (wd / "quote.pending.json").exists():
            mark, why = "running", "slicing on dapi"
        elif s == "price" and (wd / "price.rejected.json").exists():
            r = read(wd / "price.rejected.json") or {}
            mark, why = "blocked", f"no sellable price: floor {r.get('floor')} vs market median {(r.get('market') or {}).get('median')}"
        elif s == "design" and (wd / "price.json").exists() and not (wd / "price.approved.json").exists():
            mark, why = "waiting", "a person approves the price: scripts/dev/shop.sh approve " + wd.name[:12]
        elif lock and lock.get("stage") == s:
            mark, why = "running", f"worker since {lock.get('started') or lock.get('at') or '?'}"
        elif all(exists(wd, p) for p in st["requires"]):
            mark, why = "ready", "inputs present, next run picks it up"
        rows.append((s, mark))
        if where is None and mark != "todo":
            where = f"{s}: {mark}" + (f", {why}" if why else "")
        elif where is None:
            missing = [p for p in st["requires"] if not exists(wd, p)]
            where = f"{s}: waiting for {', '.join(missing)}"
    if where is None:
        pub = read(wd / "publish.json") or {}
        where = "published" + (f": {pub.get('url')}" if pub.get("url") else "")
    return rows, where


def status(items: list[str], run: str | None) -> int:
    if run:
        state = read(ROOT / "runs" / run / "state.json")
        if not state:
            print(f"no run {run}"); return 1
        items = list(state["items"])
    dirs = [d for d in sorted(WORK.iterdir()) if d.is_dir() and (d / "meta.json").exists()] if WORK.exists() else []
    if items:
        dirs = [d for d in dirs if any(d.name.startswith(i) for i in items)]
    if not dirs:
        print("  no items"); return 1
    style = {"done": OK("✓"), "failed": BAD("✗"), "blocked": BAD("■"), "waiting": WARN("◆"), "running": WARN("▶"),
             "ready": WARN("·"), "todo": DIM("·"), "off": DIM("-")}
    for d in dirs:
        meta = read(d / "meta.json") or {}
        rows, where = item_status(d)
        print(f"  {d.name[:12]}  {meta.get('file', '?')}")
        print("    " + "  ".join(f"{style[m]} {s}" for s, m in rows))
        print(f"    now: {where}")
    print(DIM(f"\n  {style['done']} done  {style['running']} running  {style['waiting']} waiting on a person  "
              f"{style['blocked']} blocked  {style['failed']} failed  · to do  - disabled"))
    return 0


# ------------------------------------------------------------------ gate

def gate(item: str) -> int:
    load_env()
    wd = next((d for d in WORK.iterdir() if d.name.startswith(item)), None) if WORK.exists() else None
    if not wd:
        print(f"no work dir for {item}"); return 1
    meta, prop = read(wd / "meta.json") or {}, read(wd / "price.json")
    cands = read(wd / "price.candidates.json") or read(wd / "price.rejected.json") or {}
    approved = read(wd / "price.approved.json")
    print(f"  item        {wd.name}\n  file        {meta.get('file')}")
    print(f"  product     {cands.get('product')}   verdict {cands.get('verdict')}")
    m = cands.get("market") or {}
    if m:
        print(f"  market      n={m.get('n')}  p25 {m.get('p25')}  median {m.get('median')}  p75 {m.get('p75')}  ({m.get('basis')})")
    print(f"  unit cost   {cands.get('unit_cost')} {cands.get('currency')} ({cands.get('cost_basis', 'one_off')})   floor {cands.get('floor')}  (break-even, nothing below it is accepted)")
    for cd in cands.get("candidates") or []:
        print(f"  candidate   {cd['label']:8} {cd['price']:>7}  margin {cd.get('margin')} ({round((cd.get('margin_pct') or 0) * 100)}%)")
    if prop:
        print(f"  PROPOSED    {prop.get('price')} {prop.get('currency')} ({prop.get('candidate')})")
        print(f"  why         {prop.get('rationale')}")
    else:
        print("  PROPOSED    nothing: " + ("every candidate was rejected; only an override with a reason can price it" if cands else "the price stage has not run"))
    if approved:
        print(f"  approved    {approved.get('price')} by {approved.get('by')} at {approved.get('at')}")
    return 0 if prop or cands else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check"); s.add_argument("--offline", action="store_true")
    s = sub.add_parser("status"); s.add_argument("items", nargs="*"); s.add_argument("--run")
    s = sub.add_parser("gate"); s.add_argument("item")
    a = ap.parse_args()
    if a.cmd == "check":
        return check(a.offline)
    if a.cmd == "status":
        return status(a.items, a.run)
    return gate(a.item)


if __name__ == "__main__":
    raise SystemExit(main())
