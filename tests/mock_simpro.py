"""Tiny fake simPRO server used by the test suite."""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

STATE = {"tokens_issued": 0, "fail_next_401": False, "rate_limit_once": True, "invoices": []}
JOBS = [{"ID": i, "Name": f"Job {i}", "Stage": "Progress"} for i in range(1, 8)]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, code, obj, headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if STATE["fail_next_401"]:
            STATE["fail_next_401"] = False
            self._send(401, {"errors": [{"message": "expired"}]}); return False
        if self.headers.get("Authorization", "") != f"Bearer tok{STATE['tokens_issued']}":
            self._send(401, {"errors": [{"message": "bad token"}]}); return False
        return True

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        if u.path == "/oauth2/token":
            q = parse_qs(raw)
            if q.get("client_secret") != ["secret"]:
                return self._send(401, {"error": "invalid_client"})
            STATE["tokens_issued"] += 1
            return self._send(200, {"access_token": f"tok{STATE['tokens_issued']}", "expires_in": 3600})
        if not self._auth(): return
        if u.path == "/api/v1.0/companies/0/customerInvoices/":
            body = json.loads(raw)
            if "Jobs" not in body:
                return self._send(422, {"errors": [{"path": "Jobs", "message": "required"}]})
            inv = {"ID": 900 + len(STATE["invoices"]), **body}
            STATE["invoices"].append(inv)
            return self._send(201, inv, {"Location": f"/api/v1.0/companies/0/customerInvoices/{inv['ID']}"})
        self._send(404, {"errors": [{"message": "nope"}]})

    def do_GET(self):
        u = urlparse(self.path)
        if not self._auth(): return
        q = parse_qs(u.query)
        if u.path == "/api/v1.0/companies/":
            return self._send(200, [{"ID": 0, "Name": "Test Co"}])
        if u.path == "/api/v1.0/companies/0/jobs/":
            if STATE["rate_limit_once"]:
                STATE["rate_limit_once"] = False
                return self._send(429, {"errors": []}, {"Retry-After": "0.5"})
            ps = int(q.get("pageSize", ["30"])[0]); pg = int(q.get("page", ["1"])[0])
            pages = -(-len(JOBS) // ps)
            return self._send(200, JOBS[(pg-1)*ps: pg*ps],
                              {"Result-Total": str(len(JOBS)), "Result-Pages": str(pages)})
        if u.path.startswith("/api/v1.0/companies/0/jobs/") and u.path.count("/") == 7:
            jid = int(u.path.split("/")[6])
            for j in JOBS:
                if j["ID"] == jid: return self._send(200, {**j, "Notes": "Replaced tap washer"})
            return self._send(404, {"errors": [{"message": "Job not found"}]})
        self._send(404, {"errors": [{"message": "nope"}]})


if __name__ == "__main__":
    srv = HTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    srv.serve_forever()
