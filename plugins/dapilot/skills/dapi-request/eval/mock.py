# Local mock of the two routes eval case 6 needs. Usage: mock.py <port> <openapi.json>
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
SPEC = open(sys.argv[2]).read()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/openapi.json":
            body = SPEC.encode(); code = 200
        elif self.path == "/v1/me":
            body = json.dumps({"ok": True, "seen_key": self.headers.get("x-api-key"), "seen_ua": self.headers.get("user-agent")}).encode(); code = 200
        elif self.path.startswith("/v1/quotes/"):
            body = json.dumps({"statusCode": 404, "message": "quote not found"}).encode(); code = 404
        else:
            body = b'{"statusCode":401}'; code = 401
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("X-RateLimit-Limit", "500"); self.send_header("X-RateLimit-Remaining", "499"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
