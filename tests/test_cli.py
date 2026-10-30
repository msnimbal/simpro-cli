"""End-to-end tests for simpro-cli against a local mock simPRO server.

Run:  python -m unittest discover -s tests -v
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import mock_simpro  # noqa: E402

CLI = [sys.executable, str(ROOT / "simpro_cli.py")]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CliTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.server = HTTPServer(("127.0.0.1", cls.port), mock_simpro.H)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("SIMPRO_")},
            "SIMPRO_CONFIG_DIR": self.tmp.name,
            "SIMPRO_BUILD_URL": f"http://127.0.0.1:{self.port}",
            "SIMPRO_COMPANY_ID": "0",
            "SIMPRO_CLIENT_ID": "abc",
            "SIMPRO_CLIENT_SECRET": "secret",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, env=None):
        p = subprocess.run(CLI + list(args), capture_output=True, text=True,
                           env={**self.env, **(env or {})})
        out = json.loads(p.stdout) if p.stdout.strip().startswith(("{", "[")) else p.stdout
        err = json.loads(p.stderr) if p.stderr.strip().startswith("{") else p.stderr
        return p.returncode, out, err

    def test_connection(self):
        code, out, _ = self.run_cli("test")
        self.assertEqual(code, 0)
        self.assertTrue(out["ok"])

    def test_get_all_pages_with_rate_limit_retry(self):
        mock_simpro.STATE["rate_limit_once"] = True
        code, out, _ = self.run_cli("get", "jobs", "--all", "--page-size", "3", "--meta")
        self.assertEqual(code, 0)
        self.assertEqual(out["meta"]["count"], 7)

    def test_single_record_and_404(self):
        code, out, _ = self.run_cli("get", "jobs/2")
        self.assertEqual((code, out["ID"]), (0, 2))
        code, _, err = self.run_cli("get", "jobs/99")
        self.assertEqual((code, err["status"]), (4, 404))

    def test_write_is_dry_run_by_default(self):
        before = len(mock_simpro.STATE["invoices"])
        code, out, _ = self.run_cli("post", "customerInvoices", "-d", '{"Jobs":[2]}')
        self.assertEqual(code, 0)
        self.assertTrue(out["dry_run"])
        self.assertEqual(len(mock_simpro.STATE["invoices"]), before)

    def test_execute_writes_and_audits(self):
        code, out, _ = self.run_cli("post", "customerInvoices", "-d", '{"Jobs":[2],"Stage":"Pending"}',
                                    "--execute", "--note", "test")
        self.assertEqual((code, out["status"]), (0, 201))
        log = (Path(self.tmp.name) / "audit.log").read_text().strip().splitlines()
        self.assertEqual(json.loads(log[-1])["note"], "test")

    def test_validation_error(self):
        code, _, err = self.run_cli("post", "customerInvoices", "-d", "{}", "--execute")
        self.assertEqual((code, err["status"]), (5, 422))

    def test_read_only_blocks_writes(self):
        code, _, err = self.run_cli("delete", "customerInvoices/900", "--execute",
                                    env={"SIMPRO_READ_ONLY": "true"})
        self.assertEqual(code, 7)

    def test_bad_credentials(self):
        code, _, err = self.run_cli("test", env={"SIMPRO_CLIENT_SECRET": "wrong"})
        self.assertEqual(code, 3)

    def test_refuses_foreign_host(self):
        code, _, _ = self.run_cli("get", "https://example.com/x")
        self.assertEqual(code, 2)

    def test_stale_token_is_refreshed(self):
        self.run_cli("test")
        p = Path(self.tmp.name) / "token.json"
        data = json.loads(p.read_text())
        data["access_token"] = "stale"
        p.write_text(json.dumps(data))
        code, out, _ = self.run_cli("get", "jobs/3")
        self.assertEqual((code, out["ID"]), (0, 3))


if __name__ == "__main__":
    unittest.main()
