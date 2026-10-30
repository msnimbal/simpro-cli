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

    def test_body_file_from_windows_powershell_encodings(self):
        for name, enc in (("utf16.json", "utf-16"), ("bom.json", "utf-8-sig")):
            f = Path(self.tmp.name) / name
            f.write_text('{"Name": "Caf\u00e9"}', encoding=enc)
            code, out, _ = self.run_cli("post", "customers/companies", "-d", f"@{f}")
            self.assertEqual(code, 0, name)
            self.assertEqual(out["body"]["Name"], "Caf\u00e9")

    def test_env_file_with_bom_is_read(self):
        env_file = Path(self.tmp.name) / "win.env"
        env_file.write_text(f"SIMPRO_BUILD_URL=http://127.0.0.1:{self.port}\n"
                            "SIMPRO_CLIENT_ID=abc\nSIMPRO_CLIENT_SECRET=secret\n", encoding="utf-8-sig")
        bare = {k: v for k, v in self.env.items() if not k.startswith("SIMPRO_")}
        p = subprocess.run(CLI + ["test"], capture_output=True, text=True,
                           env={**bare, "SIMPRO_ENV_FILE": str(env_file),
                                "SIMPRO_CONFIG_DIR": self.tmp.name})
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_setup_refuses_without_a_terminal(self):
        code, _, err = self.run_cli("setup")
        self.assertEqual(code, 2)
        self.assertIn("normal terminal", err["error"])


class SetupTest(unittest.TestCase):
    """`simpro setup` is interactive, so drive it in-process with scripted answers."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.server = HTTPServer(("127.0.0.1", cls.port), mock_simpro.H)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def run_setup(self, answers, secret, existing=False):
        import argparse
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest import mock

        import simpro_cli

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            if existing:
                env_path.write_text("SIMPRO_BUILD_URL=https://old.example\n")
            clean = {k: v for k, v in os.environ.items() if not k.startswith("SIMPRO_")}
            clean.update(SIMPRO_CONFIG_DIR=tmp)
            class Tty(io.StringIO):
                def isatty(self):
                    return True

            out, err = Tty(), Tty()
            with mock.patch.dict(os.environ, clean, clear=True), \
                    mock.patch("sys.stdin", Tty()), \
                    mock.patch("builtins.input", side_effect=answers), \
                    mock.patch("getpass.getpass", return_value=secret), \
                    redirect_stdout(out), redirect_stderr(err):
                code = simpro_cli.cmd_setup(argparse.Namespace(verbose=False))
            text = env_path.read_text() if env_path.exists() else ""
            mode = env_path.stat().st_mode & 0o777 if env_path.exists() else None
            return code, out.getvalue(), err.getvalue(), text, mode

    def test_saves_private_file_and_connects(self):
        code, out, _, text, mode = self.run_setup(
            [f"http://127.0.0.1:{self.port}/some/path", "", "abc", ""], "secret")
        self.assertEqual(code, 0)
        self.assertIn(f"SIMPRO_BUILD_URL=http://127.0.0.1:{self.port}\n", text)
        self.assertIn("SIMPRO_COMPANY_ID=0\n", text)
        self.assertIn("SIMPRO_CLIENT_SECRET=secret\n", text)
        self.assertIn("SIMPRO_READ_ONLY=true\n", text)  # safe default
        self.assertNotIn("secret", out)  # never echoed
        if os.name != "nt":
            self.assertEqual(mode, 0o600)

    def test_missing_value_saves_nothing(self):
        import simpro_cli
        with self.assertRaises(simpro_cli.CliError) as cm:
            self.run_setup([f"http://127.0.0.1:{self.port}", "", "", ""], "")
        self.assertEqual(cm.exception.code, 2)

    def test_existing_file_kept_unless_confirmed(self):
        code, _, _, text, _ = self.run_setup([""], "", existing=True)
        self.assertEqual(code, 0)
        self.assertEqual(text, "SIMPRO_BUILD_URL=https://old.example\n")

    def test_bad_credentials_keep_file_but_report_failure(self):
        code, _, err, text, _ = self.run_setup(
            [f"http://127.0.0.1:{self.port}", "", "bad", "n"], "wrong")
        self.assertEqual(code, 3)
        self.assertIn("connection test failed", err)
        self.assertIn("SIMPRO_CLIENT_ID=bad", text)
        self.assertNotIn("wrong", err)


if __name__ == "__main__":
    unittest.main()
