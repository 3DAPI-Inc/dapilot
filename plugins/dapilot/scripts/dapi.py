#!/usr/bin/env python3
"""One client for every dapi public API route, driven by the OpenAPI contract.

The public API serves its own contract at <DAPI_API_URL>/openapi.json. This
script reads it and uses it for everything: listing operations, describing
what a request must contain, validating a payload before it is sent, and
sending it. Nothing about a route is hard-coded here except two things the
spec does not carry: which permission scope a route needs, and which
mutating routes an agent may call unattended (see GATES).

Subcommands (all print JSON on stdout; exit 0 ok, 1 failure, 2 usage):

  spec [--refresh]                      fetch or reuse the cached spec
  ops [--tag T] [--mutating]            list operations
  describe <op>                         params, resolved body schema, success schema
  validate <op> [opts]                  check a request without sending it
  call <op> [opts] [--confirm]          validate, then send
  upload <presigned_url> <file>         PUT a file to a presigned URL

Options for validate/call:
  --path k=v      path parameter (repeatable)
  --query k=v     query parameter (repeatable)
  --body FILE     JSON body file, or - for stdin
  --spec FILE     use this spec file instead of the cached/fetched one

Env: DAPI_API_URL (required unless --spec), DAPI_API_KEY (required for call),
DAPILOT_CACHE_DIR (optional).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERSION = "0.1.0"
USER_AGENT = f"dapilot-dapi-request/{VERSION}"
MUTATING_METHODS = {"post", "put", "patch", "delete"}

# Gate per operation, for mutating routes only. "auto": an agent may call it
# unattended. "propose": the call needs --confirm, which only a person passes.
# Anything mutating and not listed is "propose". Change this table by commit.
GATES = {
    "uploadQuoteFile": "auto",     # reserves an upload slot; no spend, no state a customer sees
    "createQuote": "auto",         # one slicer run; the intake pipeline depends on it
    "estimateShipping": "auto",    # POST in shape, read-only in effect
    "createOrder": "propose",
    "payOrder": "propose",
    "updatePrintFarmOrderStatus": "propose",
    "addPrintFarmStockLevelItemsMade": "propose",
}

# Permission scope per route prefix. Derived from the controller decorators in
# public-api/v1, because the spec does not carry them. "ANY" means any valid key.
def scope_for(method: str, path: str) -> str:
    p = path.removeprefix("/v1")
    if p.startswith("/me") or p.startswith("/print-farm/courier-options"):
        return "ANY"
    if p.startswith("/quotes"):
        return "PRICING"
    if p.startswith("/designer/orders") or p.startswith("/orders"):
        return "ORDER"
    if p.startswith("/print-farm/orders"):
        return "READ_ORDERS" if method == "get" else "WRITE_ORDERS"
    if p.startswith("/print-farm/stock"):
        return "READ_STOCK" if method == "get" else "WRITE_STOCK"
    if p.startswith("/print-farm/"):
        return "SKU"
    return "UNKNOWN"


# ----------------------------------------------------------------- output

def out(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj, indent=2, default=str))
    return code


def fail(reason: str, code: int = 1, **extra) -> int:
    return out({"ok": False, "reason": reason, **extra}, code)


# ------------------------------------------------------------------- spec

def base_url() -> str:
    url = os.environ.get("DAPI_API_URL", "").strip().rstrip("/")
    if not url:
        raise SystemExit(fail("DAPI_API_URL is not set", 2))
    if url.endswith("/v1"):
        raise SystemExit(fail("DAPI_API_URL must be the origin without /v1; spec paths already include it", 2))
    return url


def cache_path() -> Path:
    root = Path(os.environ.get("DAPILOT_CACHE_DIR") or Path.home() / ".cache" / "dapilot")
    root.mkdir(parents=True, exist_ok=True)
    host = urllib.parse.urlparse(base_url()).netloc.replace(":", "_")
    return root / f"openapi.{host}.json"


def fetch_spec(dest: Path) -> dict:
    req = urllib.request.Request(base_url() + "/openapi.json", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
    dest.write_text(json.dumps(data))
    return data


def load_spec(spec_file: str | None, refresh: bool = False) -> tuple[dict, str]:
    if spec_file:
        return json.loads(Path(spec_file).read_text()), spec_file
    path = cache_path()
    if refresh or not path.exists():
        return fetch_spec(path), str(path)
    return json.loads(path.read_text()), str(path)


def index_ops(spec: dict) -> dict:
    ops = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            oid = op.get("operationId") or f"{method}_{path}"
            ops[oid] = {"id": oid, "method": method.lower(), "path": path, "op": op}
    return ops


def resolve(node, spec: dict, depth: int = 0):
    """Inline $ref pointers so a schema can be validated standalone."""
    if depth > 30:
        return node
    if isinstance(node, dict):
        if "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#/"):
                return node
            target = spec
            for part in ref[2:].split("/"):
                target = target[part]
            merged = {k: v for k, v in node.items() if k != "$ref"}
            merged.update(copy.deepcopy(target))
            return resolve(merged, spec, depth + 1)
        return {k: resolve(v, spec, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve(v, spec, depth + 1) for v in node]
    return node


def body_schema(entry: dict, spec: dict) -> dict | None:
    rb = entry["op"].get("requestBody")
    if not rb:
        return None
    content = rb.get("content", {}).get("application/json", {})
    schema = resolve(content.get("schema", {}), spec)
    # The server runs class-validator with forbidNonWhitelisted, so an unknown
    # top-level field is a 400. Mirror that here, at the top level only.
    if schema.get("type") == "object" and "properties" in schema and "additionalProperties" not in schema:
        schema["additionalProperties"] = False
    return schema


def success_schema(entry: dict, spec: dict) -> dict | None:
    for code, resp in entry["op"].get("responses", {}).items():
        if code.startswith("2"):
            schema = resp.get("content", {}).get("application/json", {}).get("schema")
            return resolve(schema, spec) if schema else None
    return None


def example_body(schema: dict | None) -> dict | None:
    if not schema or schema.get("type") != "object":
        return None
    ex = {}
    for name, prop in schema.get("properties", {}).items():
        if "example" in prop:
            ex[name] = prop["example"]
        elif "default" in prop:
            ex[name] = prop["default"]
        elif name in schema.get("required", []):
            ex[name] = f"<{prop.get('type', 'value')}>"
    return ex


# ------------------------------------------------------------- validation

def coerce(value: str, schema: dict):
    t = schema.get("type")
    try:
        if t == "integer":
            return int(value)
        if t == "number":
            return float(value)
        if t == "boolean":
            if value.lower() in {"true", "1", "yes"}:
                return True
            if value.lower() in {"false", "0", "no"}:
                return False
            return value
    except ValueError:
        return value
    return value


def kv_pairs(items: list[str] | None) -> dict:
    result = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(fail(f"expected k=v, got {item!r}", 2))
        k, v = item.split("=", 1)
        result[k] = v
    return result


def read_body(arg: str | None):
    if arg is None:
        return None
    raw = sys.stdin.read() if arg == "-" else Path(arg).read_text()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(fail(f"body is not valid JSON: {e}", 2))


def validate_request(entry: dict, spec: dict, path_params: dict, query: dict, body) -> tuple[list[str], dict, dict]:
    """Return (errors, coerced path params, coerced query)."""
    try:
        import jsonschema
    except ImportError:
        raise SystemExit(fail("the jsonschema package is missing: pip install jsonschema", 2))

    errors: list[str] = []
    params = [resolve(p, spec) for p in entry["op"].get("parameters", [])]
    declared_path = {p["name"] for p in params if p["in"] == "path"}
    declared_query = {p["name"]: p for p in params if p["in"] == "query"}

    coerced_path = {}
    for p in params:
        if p["in"] != "path":
            continue
        if p["name"] not in path_params:
            errors.append(f"missing path parameter: {p['name']}")
            continue
        v = coerce(path_params[p["name"]], p.get("schema", {}))
        for err in jsonschema.Draft7Validator(p.get("schema", {})).iter_errors(v):
            errors.append(f"path.{p['name']}: {err.message}")
        coerced_path[p["name"]] = v
    for name in path_params:
        if name not in declared_path:
            errors.append(f"unknown path parameter: {name}")

    coerced_query = {}
    for name, raw in query.items():
        if name not in declared_query:
            errors.append(f"unknown query parameter: {name}")
            continue
        v = coerce(raw, declared_query[name].get("schema", {}))
        for err in jsonschema.Draft7Validator(declared_query[name].get("schema", {})).iter_errors(v):
            errors.append(f"query.{name}: {err.message}")
        coerced_query[name] = v
    for name, p in declared_query.items():
        if p.get("required") and name not in query:
            errors.append(f"missing query parameter: {name}")

    schema = body_schema(entry, spec)
    if schema is None and body is not None:
        errors.append("this operation takes no body")
    if schema is not None:
        required = bool(entry["op"]["requestBody"].get("required"))
        if body is None:
            if required:
                errors.append("body is required")
        else:
            for err in sorted(jsonschema.Draft7Validator(schema).iter_errors(body), key=lambda e: list(e.path)):
                where = ".".join(str(x) for x in err.path) or "body"
                errors.append(f"{where}: {err.message}")
    return errors, coerced_path, coerced_query


def build_url(entry: dict, path_params: dict, query: dict) -> str:
    path = entry["path"]
    for k, v in path_params.items():
        path = path.replace("{" + k + "}", urllib.parse.quote(str(v), safe=""))
    url = base_url() + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


# ------------------------------------------------------------------ http

def send(method: str, url: str, body, key: str | None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if key:
        headers["x-api-key"] = key
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, raw, hdrs = r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        status, raw, hdrs = e.code, e.read(), e.headers
    except urllib.error.URLError as e:
        return {"ok": False, "status": None, "reason": f"connection failed: {e.reason}"}
    try:
        parsed = json.loads(raw.decode()) if raw else None
    except json.JSONDecodeError:
        parsed = raw.decode(errors="replace")
    rate = {k.lower(): v for k, v in hdrs.items() if k.lower().startswith("x-ratelimit")}
    return {"ok": 200 <= status < 300, "status": status, "body": parsed, "rate_limit": rate}


# ----------------------------------------------------------- subcommands

def cmd_spec(a) -> int:
    spec, where = load_spec(a.spec, refresh=a.refresh)
    return out({"ok": True, "source": where, "title": spec.get("info", {}).get("title"),
                "version": spec.get("info", {}).get("version"), "operations": len(index_ops(spec))})


def cmd_ops(a) -> int:
    spec, _ = load_spec(a.spec)
    rows = []
    for e in index_ops(spec).values():
        mutating = e["method"] in MUTATING_METHODS
        if a.tag and a.tag not in e["op"].get("tags", []):
            continue
        if a.mutating and not mutating:
            continue
        rows.append({"id": e["id"], "method": e["method"].upper(), "path": e["path"],
                     "tags": e["op"].get("tags", []), "scope": scope_for(e["method"], e["path"]),
                     "mutating": mutating,
                     "gate": (GATES.get(e["id"], "propose") if mutating else "auto"),
                     "summary": e["op"].get("summary")})
    return out({"ok": True, "operations": rows})


def cmd_describe(a) -> int:
    spec, _ = load_spec(a.spec)
    ops = index_ops(spec)
    if a.op not in ops:
        return fail(f"unknown operation {a.op!r}; run `ops` to list them")
    e = ops[a.op]
    params = [resolve(p, spec) for p in e["op"].get("parameters", [])]
    schema = body_schema(e, spec)
    mutating = e["method"] in MUTATING_METHODS
    return out({
        "ok": True, "id": e["id"], "method": e["method"].upper(), "path": e["path"],
        "summary": e["op"].get("summary"), "description": e["op"].get("description"),
        "scope": scope_for(e["method"], e["path"]), "mutating": mutating,
        "gate": GATES.get(e["id"], "propose") if mutating else "auto",
        "parameters": [{"name": p["name"], "in": p["in"], "required": p.get("required", False),
                        "schema": p.get("schema"), "description": p.get("description")} for p in params],
        "body_required": bool((e["op"].get("requestBody") or {}).get("required")),
        "body_schema": schema, "example_body": example_body(schema),
        "success_schema": success_schema(e, spec),
    })


def cmd_validate(a, do_call: bool = False) -> int:
    spec, _ = load_spec(a.spec)
    ops = index_ops(spec)
    if a.op not in ops:
        return fail(f"unknown operation {a.op!r}; run `ops` to list them")
    e = ops[a.op]
    body = read_body(a.body)
    errors, path_params, query = validate_request(e, spec, kv_pairs(a.path), kv_pairs(a.query), body)
    mutating = e["method"] in MUTATING_METHODS
    gate = GATES.get(e["id"], "propose") if mutating else "auto"
    request = {"method": e["method"].upper(), "path": e["path"], "path_params": path_params,
               "query": query, "body": body, "scope": scope_for(e["method"], e["path"]), "gate": gate}
    if errors:
        return out({"ok": False, "valid": False, "errors": errors, "request": request}, 1)
    if not do_call:
        return out({"ok": True, "valid": True, "request": request})

    url = build_url(e, path_params, query)
    request["url"] = url
    if gate == "propose" and not a.confirm:
        return out({"ok": True, "valid": True, "dry_run": True,
                    "reason": "this operation is gated; re-run with --confirm to send it",
                    "request": request})
    key = os.environ.get("DAPI_API_KEY", "").strip()
    if not key:
        return fail("DAPI_API_KEY is not set", 2)
    result = send(e["method"], url, body, key)
    result["request"] = {k: v for k, v in request.items() if k != "body"} | {"body_bytes": len(json.dumps(body)) if body is not None else 0}
    return out(result, 0 if result["ok"] else 1)


def cmd_upload(a) -> int:
    path = Path(a.file)
    if not path.is_file():
        return fail(f"no such file: {path}")
    data = path.read_bytes()
    req = urllib.request.Request(a.url, data=data, method="PUT",
                                 headers={"Content-Type": "application/octet-stream", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            status = r.status
    except urllib.error.HTTPError as e:
        return fail(f"upload rejected: HTTP {e.code}", status=e.code)
    except urllib.error.URLError as e:
        return fail(f"connection failed: {e.reason}")
    return out({"ok": 200 <= status < 300, "status": status, "bytes": len(data), "file": str(path)})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dapi.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spec", help="use this OpenAPI file instead of fetching")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("spec"); s.add_argument("--refresh", action="store_true"); s.set_defaults(fn=cmd_spec)
    s = sub.add_parser("ops"); s.add_argument("--tag"); s.add_argument("--mutating", action="store_true"); s.set_defaults(fn=cmd_ops)
    s = sub.add_parser("describe"); s.add_argument("op"); s.set_defaults(fn=cmd_describe)
    for name in ("validate", "call"):
        s = sub.add_parser(name)
        s.add_argument("op")
        s.add_argument("--path", action="append")
        s.add_argument("--query", action="append")
        s.add_argument("--body")
        s.add_argument("--confirm", action="store_true")
        s.set_defaults(fn=lambda a, _n=name: cmd_validate(a, do_call=(_n == "call")))
    s = sub.add_parser("upload"); s.add_argument("url"); s.add_argument("file"); s.set_defaults(fn=cmd_upload)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except SystemExit as e:
        return int(e.code or 0)
    except urllib.error.URLError as e:
        return fail(f"connection failed: {e.reason}")
    except Exception as e:  # noqa: BLE001 - surface as JSON, never a traceback
        return fail(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
