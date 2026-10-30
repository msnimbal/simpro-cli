#!/usr/bin/env python3
"""simpro - command-line access to the simPRO REST API (v1.0).

Designed to be driven by Claude Code (JSON in, JSON out) and usable by hand.
Standard library only - no dependencies.

Safety model
  * Reads (GET) run immediately.
  * Writes (POST/PUT/PATCH/DELETE) are DRY-RUN by default: they print the exact
    request and exit. Add --execute to actually send it.
  * SIMPRO_READ_ONLY=true blocks all writes, even with --execute.
  * Every executed write is appended to an audit log.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import getpass
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

API_PREFIX = "/api/v1.0"
MAX_PAGE_SIZE = 250

# Exit codes (Claude Code can branch on these)
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_VALIDATION = 5
EXIT_SERVER = 6
EXIT_BLOCKED = 7


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_ERROR, **extra: Any):
        super().__init__(message)
        self.code = code
        self.extra = extra


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def config_dir() -> Path:
    override = os.environ.get("SIMPRO_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "simpro-cli"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "simpro-cli"


def env_file_path() -> Path:
    override = os.environ.get("SIMPRO_ENV_FILE")
    return Path(override).expanduser() if override else config_dir() / ".env"


def _secure(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows / unusual filesystems


def read_text_file(path: Path) -> str:
    """Read a text file, tolerating the BOMs that Windows editors/PowerShell add."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def load_env_file() -> None:
    """Load KEY=VALUE lines from the .env file. Real env vars take precedence."""
    path = env_file_path()
    if not path.is_file():
        return
    for raw in read_text_file(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        build = os.environ.get("SIMPRO_BUILD_URL", "").strip().rstrip("/")
        if build and not build.startswith(("http://", "https://")):
            build = "https://" + build
        self.build_url = build
        self.company_id = os.environ.get("SIMPRO_COMPANY_ID", "0").strip() or "0"
        self.client_id = os.environ.get("SIMPRO_CLIENT_ID", "").strip()
        self.client_secret = os.environ.get("SIMPRO_CLIENT_SECRET", "").strip()
        self.access_token = os.environ.get("SIMPRO_ACCESS_TOKEN", "").strip()  # legacy API key
        self.read_only = _truthy(os.environ.get("SIMPRO_READ_ONLY"))
        self.timeout = float(os.environ.get("SIMPRO_TIMEOUT", "60"))

    def require(self) -> None:
        missing = []
        if not self.build_url:
            missing.append("SIMPRO_BUILD_URL")
        if not self.access_token and not (self.client_id and self.client_secret):
            missing.append("SIMPRO_CLIENT_ID + SIMPRO_CLIENT_SECRET (or SIMPRO_ACCESS_TOKEN)")
        if missing:
            raise CliError(
                "Missing configuration: " + ", ".join(missing)
                + f". Run `simpro setup` (or `simpro config init` and fill in {env_file_path()} by hand)",
                EXIT_AUTH,
            )


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #

class Response:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self.body.decode("utf-8", errors="replace")


class SimproClient:
    def __init__(self, settings: Settings, verbose: bool = False):
        self.s = settings
        self.verbose = verbose
        self._token: str | None = None

    # ---- auth ---------------------------------------------------------- #
    def _token_cache_path(self) -> Path:
        return config_dir() / "token.json"

    def _load_cached_token(self) -> str | None:
        path = self._token_cache_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("build_url") != self.s.build_url or data.get("client_id") != self.s.client_id:
            return None
        if float(data.get("expires_at", 0)) - 60 <= time.time():
            return None
        return data.get("access_token")

    def _save_token(self, token: str, expires_in: float) -> None:
        path = self._token_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "build_url": self.s.build_url,
            "client_id": self.s.client_id,
            "access_token": token,
            "expires_at": time.time() + float(expires_in),
        }), encoding="utf-8")
        _secure(path)

    def clear_token(self) -> None:
        self._token = None
        try:
            self._token_cache_path().unlink()
        except OSError:
            pass

    def _fetch_token(self) -> str:
        url = self.s.build_url + "/oauth2/token"
        form = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.s.client_id,
            "client_secret": self.s.client_secret,
        }).encode()
        req = urllib.request.Request(url, data=form, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": f"simpro-cli/{__version__}",
        })
        self._log(f"POST {url} (token request)")
        try:
            with urllib.request.urlopen(req, timeout=self.s.timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise CliError(
                f"Could not get an access token from simPRO (HTTP {e.code}). "
                "Check SIMPRO_BUILD_URL, SIMPRO_CLIENT_ID and SIMPRO_CLIENT_SECRET, and that the "
                "API application uses the client-credentials grant.",
                EXIT_AUTH, status=e.code, body=body,
            ) from None
        except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
            raise CliError(f"Cannot reach {url}: {getattr(e, 'reason', None) or e}", EXIT_ERROR) from None
        token = data.get("access_token")
        if not token:
            raise CliError("Token response did not contain access_token", EXIT_AUTH, body=data)
        self._save_token(token, data.get("expires_in", 3600))
        return token

    def token(self, force_refresh: bool = False) -> str:
        if self.s.access_token:
            return self.s.access_token
        if self._token and not force_refresh:
            return self._token
        if not force_refresh:
            cached = self._load_cached_token()
            if cached:
                self._token = cached
                return cached
        self._token = self._fetch_token()
        return self._token

    # ---- requests ------------------------------------------------------ #
    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[simpro] {msg}", file=sys.stderr)

    def resolve_url(self, path: str, params: list[tuple[str, str]] | None = None) -> str:
        path = path.strip()
        if path.startswith(("http://", "https://")):
            parsed = urllib.parse.urlparse(path)
            build = urllib.parse.urlparse(self.s.build_url)
            if parsed.netloc != build.netloc:
                raise CliError(f"Refusing to send credentials to a different host: {parsed.netloc}", EXIT_USAGE)
            full = path
        else:
            query = ""
            if "?" in path:
                path, query = path.split("?", 1)
            if path.startswith("/api/"):
                full_path = path
            elif path.startswith("/"):
                # e.g. "/companies/" -> /api/v1.0/companies/
                full_path = API_PREFIX + path
            else:
                full_path = f"{API_PREFIX}/companies/{self.s.company_id}/{path}"
            if not full_path.endswith("/"):
                full_path += "/"
            full = self.s.build_url + full_path + (("?" + query) if query else "")
        if params:
            sep = "&" if "?" in full else "?"
            full += sep + urllib.parse.urlencode(params)
        return full

    def request(self, method: str, url: str, body: Any = None, retries: int = 3) -> Response:
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": f"simpro-cli/{__version__}",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        idempotent = method in ("GET", "PUT", "DELETE")
        refreshed = False
        attempt = 0
        while True:
            attempt += 1
            headers["Authorization"] = f"Bearer {self.token()}"
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            self._log(f"{method} {url}")
            try:
                with urllib.request.urlopen(req, timeout=self.s.timeout) as r:
                    return Response(r.status, dict(r.headers), r.read())
            except urllib.error.HTTPError as e:
                resp = Response(e.code, dict(e.headers or {}), e.read())
                if e.code == 401 and not refreshed and not self.s.access_token:
                    self._log("401 - refreshing token and retrying once")
                    self.clear_token()
                    self.token(force_refresh=True)
                    refreshed = True
                    continue
                retryable = e.code == 429 or (e.code >= 500 and idempotent)
                if retryable and attempt <= retries:
                    wait = _retry_after(resp.headers.get("retry-after"), attempt)
                    self._log(f"HTTP {e.code} - retrying in {wait:.1f}s")
                    time.sleep(wait)
                    continue
                return resp
            except (urllib.error.URLError, http.client.HTTPException, OSError) as e:
                if idempotent and attempt <= retries:
                    self._log(f"network error ({e}) - retrying")
                    time.sleep(2 ** attempt)
                    continue
                reason = getattr(e, "reason", None) or e
                hint = "" if idempotent else " The write may or may not have been applied - check in simPRO before retrying."
                raise CliError(f"Network error calling {url}: {reason}.{hint}", EXIT_ERROR) from None


def _retry_after(value: str | None, attempt: int) -> float:
    try:
        return max(0.5, min(float(value), 60.0)) if value else float(2 ** attempt)
    except ValueError:
        return float(2 ** attempt)


def raise_for_status(resp: Response, method: str, url: str) -> None:
    if 200 <= resp.status < 300:
        return
    code = {
        400: EXIT_VALIDATION, 401: EXIT_AUTH, 403: EXIT_AUTH, 404: EXIT_NOT_FOUND,
        405: EXIT_USAGE, 409: EXIT_VALIDATION, 422: EXIT_VALIDATION, 429: EXIT_SERVER,
    }.get(resp.status, EXIT_SERVER if resp.status >= 500 else EXIT_ERROR)
    hint = {
        401: "Authentication failed - credentials wrong or token rejected.",
        403: "Forbidden - the API application/user lacks permission for this action.",
        404: "Not found - check the ID and the endpoint path.",
        422: "simPRO rejected the data - see 'body' for which fields are wrong.",
        400: "Bad request - see 'body' for details.",
        429: "Rate limited by simPRO - slow down and retry.",
    }.get(resp.status, "Request failed.")
    raise CliError(hint, code, status=resp.status, method=method, url=url, body=resp.json())


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #

def emit(data: Any, fmt: str = "json", meta: dict[str, Any] | None = None) -> None:
    if fmt == "jsonl" and isinstance(data, list):
        for item in data:
            print(json.dumps(item, ensure_ascii=False))
        return
    if fmt == "table":
        print(_as_table(data))
        return
    out = data if meta is None else {"meta": meta, "data": data}
    print(json.dumps(out, indent=2, ensure_ascii=False))


def _cell(value: Any, width: int = 40) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def _as_table(data: Any) -> str:
    rows = data if isinstance(data, list) else [data]
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return json.dumps(data, indent=2, ensure_ascii=False)
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    cols = cols[:8]
    cells = [[_cell(r.get(c)) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    line = "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    sep = "  ".join("-" * w for w in widths)
    body = ["  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in cells]
    return "\n".join([line, sep, *body, f"({len(rows)} rows)"])


def audit(entry: dict[str, Any]) -> None:
    path = config_dir() / "audit.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _dt.datetime.now().astimezone().isoformat(timespec="seconds"), **entry}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _secure(path)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def parse_params(pairs: list[str] | None, columns: str | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in pairs or []:
        if "=" not in p:
            raise CliError(f"Query parameter must be key=value, got: {p}", EXIT_USAGE)
        k, _, v = p.partition("=")
        out.append((k, v))
    if columns:
        out.append(("columns", columns))
    return out


def read_body(spec: str | None) -> Any:
    if spec is None:
        return None
    if spec == "-":
        text = sys.stdin.read()
    elif spec.startswith("@"):
        text = read_text_file(Path(spec[1:]).expanduser())
    else:
        text = spec
    try:
        return json.loads(text)
    except ValueError as e:
        raise CliError(f"Body is not valid JSON: {e}", EXIT_USAGE) from None


def make_client(args: argparse.Namespace) -> SimproClient:
    s = Settings()
    s.require()
    if getattr(args, "timeout", None):
        s.timeout = args.timeout
    return SimproClient(s, verbose=getattr(args, "verbose", False))


def cmd_get(args: argparse.Namespace) -> int:
    client = make_client(args)
    params = parse_params(args.query, args.columns)

    if not args.all:
        if args.page_size:
            params.append(("pageSize", str(args.page_size)))
        if args.page:
            params.append(("page", str(args.page)))
        url = client.resolve_url(args.path, params)
        resp = client.request("GET", url)
        raise_for_status(resp, "GET", url)
        data = resp.json()
        meta = None
        if args.meta:
            meta = {"url": url, "status": resp.status,
                    "total": resp.headers.get("result-total"),
                    "pages": resp.headers.get("result-pages")}
        emit(data, args.format, meta)
        return EXIT_OK

    # --all: walk every page
    page_size = min(args.page_size or MAX_PAGE_SIZE, MAX_PAGE_SIZE)
    results: list[Any] = []
    page = 1
    total = pages = None
    while True:
        url = client.resolve_url(args.path, params + [("pageSize", str(page_size)), ("page", str(page))])
        resp = client.request("GET", url)
        raise_for_status(resp, "GET", url)
        chunk = resp.json()
        if not isinstance(chunk, list):
            results = chunk  # not a collection endpoint
            break
        results.extend(chunk)
        total = resp.headers.get("result-total", total)
        pages = resp.headers.get("result-pages", pages)
        if args.limit and len(results) >= args.limit:
            results = results[: args.limit]
            break
        if pages is not None:
            try:
                if page >= int(pages):
                    break
            except ValueError:
                pass
        if len(chunk) < page_size:
            break
        page += 1
    meta = {"path": args.path, "count": len(results) if isinstance(results, list) else None,
            "total": total, "pages": pages} if args.meta else None
    emit(results, args.format, meta)
    return EXIT_OK


def cmd_write(args: argparse.Namespace) -> int:
    method = args.method.upper()
    s = Settings()
    s.require()
    client = SimproClient(s, verbose=args.verbose)
    params = parse_params(args.query, None)
    url = client.resolve_url(args.path, params)
    body = read_body(args.data)
    if method in ("POST", "PUT", "PATCH") and body is None:
        raise CliError(f"{method} needs a JSON body: use -d '{{...}}' or -d @file.json", EXIT_USAGE)

    if s.read_only:
        raise CliError("Writes are disabled (SIMPRO_READ_ONLY=true).", EXIT_BLOCKED,
                       method=method, url=url)

    if not args.execute:
        emit({
            "dry_run": True,
            "method": method,
            "url": url,
            "body": body,
            "note": "Nothing was sent. Re-run the same command with --execute to apply it.",
        })
        return EXIT_OK

    resp = client.request(method, url, body)
    audit({"method": method, "url": url, "status": resp.status, "body": body,
           "note": args.note or ""})
    raise_for_status(resp, method, url)
    result = resp.json()
    emit({"ok": True, "status": resp.status, "method": method, "url": url,
          "location": resp.headers.get("location"), "result": result})
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    client = make_client(args)
    client.token(force_refresh=not client.s.access_token)
    url = client.resolve_url("/companies/")
    resp = client.request("GET", url)
    raise_for_status(resp, "GET", url)
    emit({
        "ok": True,
        "build_url": client.s.build_url,
        "company_id_in_use": client.s.company_id,
        "auth": "access_token" if client.s.access_token else "client_credentials",
        "read_only": client.s.read_only,
        "companies": resp.json(),
    })
    return EXIT_OK


ENV_TEMPLATE = """# simPRO CLI credentials - keep this file private.
# Fill in the values below, save, then run:  simpro test

SIMPRO_BUILD_URL=https://YOURBUILD.simprosuite.com
SIMPRO_COMPANY_ID=0

# From simPRO: Setup > Integrations > API > (your API application)
SIMPRO_CLIENT_ID=
SIMPRO_CLIENT_SECRET=

# Optional: legacy API key instead of client id/secret
# SIMPRO_ACCESS_TOKEN=

# Set to true to block every create/update/delete
SIMPRO_READ_ONLY=false
"""


def _normalise_build_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parts = urllib.parse.urlsplit(value)
    return f"{parts.scheme}://{parts.netloc}"


def _ask(label: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    prompt = f"{label}{suffix}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    if any(ch in value for ch in "\r\n"):
        raise CliError("Values must be a single line.", EXIT_USAGE)
    return value or default


def cmd_setup(args: argparse.Namespace) -> int:
    """Interactive first-time setup: ask for the credentials, save them, test the connection."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise CliError(
            "`simpro setup` asks for your API secret, so it must be run by you in a normal terminal "
            "window (not through Claude). Open Terminal and run: simpro setup",
            EXIT_USAGE,
        )
    path = env_file_path()
    print("simPRO setup - your answers are saved only in a private file on this computer.")
    print(f"File: {path}\n")
    if path.exists():
        if _ask("Settings already exist. Replace them? (y/N)", "n").lower() not in {"y", "yes"}:
            print("Nothing changed. To edit by hand, open the file above in a text editor.")
            return EXIT_OK

    build = _normalise_build_url(_ask("simPRO build URL (e.g. yourcompany.simprosuite.com)"))
    company = _ask("Company ID", "0")
    client_id = _ask("Client ID")
    client_secret = _ask("Client secret (typing is hidden)", secret=True)
    read_only = _ask("Start in read-only mode, blocking all changes? (Y/n)", "y").lower() in {"y", "yes"}

    missing = [n for n, v in (("build URL", build), ("client ID", client_id),
                              ("client secret", client_secret)) if not v]
    if missing:
        raise CliError("Missing: " + ", ".join(missing) + ". Nothing was saved. Run `simpro setup` again.",
                       EXIT_USAGE)

    values = {
        "SIMPRO_BUILD_URL": build,
        "SIMPRO_COMPANY_ID": company,
        "SIMPRO_CLIENT_ID": client_id,
        "SIMPRO_CLIENT_SECRET": client_secret,
        "SIMPRO_READ_ONLY": "true" if read_only else "false",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# simPRO CLI credentials - keep this file private.\n"
                    + "".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    _secure(path)
    os.environ.update(values)
    SimproClient(Settings()).clear_token()
    print(f"\nSaved. Testing the connection to {build} ...")
    try:
        cmd_test(args)
    except CliError as e:
        print(f"\nSettings were saved, but the connection test failed: {e}\n"
              "Check the values (re-run `simpro setup`) and that the API application is enabled in simPRO.",
              file=sys.stderr)
        return e.code
    print("\nConnected. You can now use /simpro in Claude Code."
          + (" Read-only mode is ON; set SIMPRO_READ_ONLY=false in the file above to allow changes."
             if read_only else ""))
    return EXIT_OK


def cmd_config(args: argparse.Namespace) -> int:
    path = env_file_path()
    if args.action == "path":
        emit({"env_file": str(path), "config_dir": str(config_dir()),
              "audit_log": str(config_dir() / "audit.log")})
    elif args.action == "init":
        if path.exists() and not args.force:
            emit({"created": False, "env_file": str(path),
                  "note": "Already exists - edit it in a text editor (use --force to overwrite)."})
            return EXIT_OK
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ENV_TEMPLATE, encoding="utf-8")
        _secure(path)
        emit({"created": True, "env_file": str(path),
              "note": "Open this file in a text editor, fill in the values, save, then run `simpro test`."})
    elif args.action == "show":
        s = Settings()

        def mask(v: str) -> str:
            return "" if not v else ("set (…" + v[-4:] + ")" if len(v) > 8 else "set")
        emit({
            "env_file": str(path), "env_file_exists": path.exists(),
            "SIMPRO_BUILD_URL": s.build_url, "SIMPRO_COMPANY_ID": s.company_id,
            "SIMPRO_CLIENT_ID": mask(s.client_id), "SIMPRO_CLIENT_SECRET": "set" if s.client_secret else "",
            "SIMPRO_ACCESS_TOKEN": "set" if s.access_token else "",
            "SIMPRO_READ_ONLY": s.read_only,
        })
    elif args.action == "logout":
        SimproClient(Settings()).clear_token()
        emit({"ok": True, "note": "Cached access token removed."})
    return EXIT_OK


def cmd_audit(args: argparse.Namespace) -> int:
    path = config_dir() / "audit.log"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    entries = [json.loads(line) for line in lines[-args.tail:]] if lines else []
    emit(entries, args.format)
    return EXIT_OK


# Commonly used resource paths (relative to /api/v1.0/companies/{companyID}/).
# Verify against https://developer.simprogroup.com/apidoc/ - availability varies by build/version.
ENDPOINTS = {
    "Customers": ["customers/", "customers/companies/", "customers/companies/{id}/",
                  "customers/individuals/", "customers/individuals/{id}/",
                  "customers/{id}/contacts/", "contacts/"],
    "Sites": ["sites/", "sites/{id}/"],
    "Jobs": ["jobs/", "jobs/{id}/", "jobs/{id}/notes/", "jobs/{id}/attachments/files/",
             "jobs/{id}/sections/", "jobs/{id}/sections/{sid}/costCenters/",
             "jobs/{id}/sections/{sid}/costCenters/{cid}/",
             "jobs/{id}/sections/{sid}/costCenters/{cid}/labor/",
             "jobs/{id}/sections/{sid}/costCenters/{cid}/catalogs/",
             "jobs/{id}/sections/{sid}/costCenters/{cid}/schedules/",
             "jobs/{id}/sections/{sid}/costCenters/{cid}/workOrders/",
             "jobs/{id}/timelines/"],
    "Quotes": ["quotes/", "quotes/{id}/", "quotes/{id}/notes/", "quotes/{id}/sections/"],
    "Leads": ["leads/", "leads/{id}/"],
    "Invoicing": ["customerInvoices/", "customerInvoices/{id}/", "customerPayments/",
                  "creditNotes/", "recurringInvoices/", "contractorInvoices/"],
    "Scheduling": ["schedules/", "activitySchedules/", "employees/", "employees/{id}/",
                   "contractors/"],
    "Catalogue & stock": ["catalogs/", "catalogs/{id}/", "prebuilds/", "storageDevices/"],
    "Purchasing": ["vendors/", "vendorOrders/", "vendorOrders/{id}/"],
    "Assets": ["customerAssets/", "sites/{id}/assets/"],
    "Setup / reference": ["setup/", "setup/accounts/costCenters/", "setup/taxCodes/",
                          "setup/statusCodes/projects/", "setup/customFields/",
                          "setup/labor/serviceFees/", "setup/labor/laborRates/"],
    "Company (root, use leading slash)": ["/companies/", "/companies/{companyID}/"],
}


def cmd_endpoints(args: argparse.Namespace) -> int:
    emit({
        "note": ("Paths are relative to /api/v1.0/companies/{companyID}/ unless they start with '/'. "
                 "This is a starting map, not a complete list - verify fields and paths at "
                 "https://developer.simprogroup.com/apidoc/ and by GET-ing a real record first."),
        "endpoints": ENDPOINTS,
    })
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="log requests to stderr")
    common.add_argument("--timeout", type=float, help="request timeout in seconds (default 60)")

    p = argparse.ArgumentParser(
        prog="simpro",
        description="Command-line access to the simPRO REST API. Output is JSON.",
        epilog=(
            "Paths are relative to /api/v1.0/companies/{SIMPRO_COMPANY_ID}/ - e.g. 'jobs/123'.\n"
            "Start a path with '/' for the API root (e.g. '/companies/').\n\n"
            "Examples:\n"
            "  simpro test\n"
            "  simpro get jobs -q pageSize=5 --columns ID,Name,Stage\n"
            "  simpro get jobs/1234\n"
            "  simpro get customers/companies --all --limit 500\n"
            "  simpro post customerInvoices -d @invoice.json            # dry run\n"
            "  simpro post customerInvoices -d @invoice.json --execute  # really create\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"simpro-cli {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    g = sub.add_parser("get", parents=[common], help="read data (GET)")
    g.add_argument("path", help="resource path, e.g. jobs or jobs/1234")
    g.add_argument("-q", "--query", action="append", metavar="KEY=VALUE",
                   help="query parameter (repeatable), e.g. -q Stage=Progress")
    g.add_argument("--columns", help="comma-separated fields to return, e.g. ID,Name,Total")
    g.add_argument("--page", type=int)
    g.add_argument("--page-size", type=int, help=f"items per page (max {MAX_PAGE_SIZE})")
    g.add_argument("--all", action="store_true", help="fetch every page")
    g.add_argument("--limit", type=int, help="with --all, stop after N items")
    g.add_argument("--meta", action="store_true", help="wrap output with url/total/pages info")
    g.add_argument("--format", choices=["json", "jsonl", "table"], default="json")
    g.set_defaults(func=cmd_get)

    for method, helptext in [("post", "create (POST)"), ("put", "replace (PUT)"),
                             ("patch", "update (PATCH)"), ("delete", "delete (DELETE)")]:
        w = sub.add_parser(method, parents=[common], help=f"{helptext} - dry run unless --execute")
        w.add_argument("path")
        w.add_argument("-d", "--data", metavar="JSON|@FILE|-",
                       help="JSON body, @path/to/file.json, or - for stdin")
        w.add_argument("-q", "--query", action="append", metavar="KEY=VALUE")
        w.add_argument("--execute", action="store_true",
                       help="actually send the request (default is a dry run)")
        w.add_argument("--note", help="free-text reason recorded in the audit log")
        w.set_defaults(func=cmd_write, method=method)

    t = sub.add_parser("test", parents=[common], help="check credentials and connection")
    t.set_defaults(func=cmd_test)

    st = sub.add_parser("setup", parents=[common], help="first-time setup: enter credentials and test the connection")
    st.set_defaults(func=cmd_setup)

    c = sub.add_parser("config", help="manage the credentials file")
    c.add_argument("action", choices=["init", "path", "show", "logout"])
    c.add_argument("--force", action="store_true", help="with init: overwrite existing file")
    c.set_defaults(func=cmd_config)

    e = sub.add_parser("endpoints", help="list commonly used simPRO resource paths")
    e.set_defaults(func=cmd_endpoints)

    a = sub.add_parser("audit", help="show recent executed writes")
    a.add_argument("--tail", type=int, default=20)
    a.add_argument("--format", choices=["json", "jsonl", "table"], default="json")
    a.set_defaults(func=cmd_audit)
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to a legacy code page; keep JSON output intact.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    load_env_file()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return args.func(args)
    except CliError as e:
        err = {"error": str(e), "exit_code": e.code, **e.extra}
        print(json.dumps(err, indent=2, ensure_ascii=False, default=str), file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
