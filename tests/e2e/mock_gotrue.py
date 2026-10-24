"""A stand-in for Supabase Auth, only for testing the browser's sign-in code end to end.
It answers the few GoTrue endpoints supabase-js uses and signs tokens with the same secret the API is given."""
import json, sys, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import jwt

SECRET = "e2e-jwt-secret-which-is-long-enough-32b!"
PASSWORD = "correct-horse-1"

def session_for(email):
    sub = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))
    role = "moderator" if email.startswith("mod@") else None
    app = {"provider": "email"}
    if role: app["role"] = role
    now = int(time.time())
    claims = {"sub": sub, "aud": "authenticated", "role": "authenticated", "email": email, "exp": now + 3600, "iat": now, "app_metadata": app, "user_metadata": {}}
    user = {"id": sub, "aud": "authenticated", "role": "authenticated", "email": email, "app_metadata": app, "user_metadata": {}, "created_at": "2026-01-01T00:00:00Z", "email_confirmed_at": "2026-01-01T00:00:00Z"}
    return {"access_token": jwt.encode(claims, SECRET, algorithm="HS256"), "token_type": "bearer", "expires_in": 3600, "expires_at": now + 3600, "refresh_token": "r-" + sub, "user": user}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body=None):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "authorization, apikey, content-type, x-client-info, x-supabase-api-version")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        if body is not None:
            data = json.dumps(body).encode()
            self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data)
        else:
            self.send_header("Content-Length", "0"); self.end_headers()
    def do_OPTIONS(self): self._send(204)
    def do_GET(self):
        if urlparse(self.path).path == "/auth/v1/user":
            tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
            try:
                c = jwt.decode(tok, SECRET, algorithms=["HS256"], audience="authenticated"); return self._send(200, session_for(c["email"])["user"])
            except Exception: return self._send(401, {"code": 401, "msg": "bad token"})
        self._send(404, {"msg": "not found"})
    def do_POST(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/auth/v1/token" and q.get("grant_type") == ["password"]:
            if body.get("password") != PASSWORD: return self._send(400, {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"})
            return self._send(200, session_for(body["email"]))
        if u.path == "/auth/v1/token" and q.get("grant_type") == ["refresh_token"]:
            return self._send(200, session_for("user@example.com"))
        if u.path == "/auth/v1/logout": return self._send(204)
        self._send(404, {"msg": "not found"})

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
