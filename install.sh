#!/usr/bin/env bash
# One-step installer for macOS / Linux.
#   1. installs the `simpro` command (via pipx)
#   2. installs the /simpro skill for Claude Code (works from any folder)
#   3. asks for your simPRO API details and tests the connection
set -euo pipefail

cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nError: %s\n' "$*" >&2; exit 1; }

command -v python3 >/dev/null 2>&1 || die "Python 3.9 or newer is required. Install it from https://www.python.org/downloads/"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || die "Python 3.9 or newer is required (found $(python3 --version 2>&1))."

if ! command -v pipx >/dev/null 2>&1; then
  say "Installing pipx"
  if command -v brew >/dev/null 2>&1; then
    brew install pipx
  else
    python3 -m pip install --user pipx || die "Could not install pipx. See https://pipx.pypa.io/stable/installation/"
  fi
fi
# pipx may not be on PATH yet in this shell
PATH="$HOME/.local/bin:$PATH"
command -v pipx >/dev/null 2>&1 || die "pipx was installed but is not on your PATH. Open a new terminal and run ./install.sh again."
pipx ensurepath >/dev/null 2>&1 || true

say "Installing the simpro command"
pipx install --force .
command -v simpro >/dev/null 2>&1 || die "simpro installed but is not on your PATH. Open a new terminal and run: simpro setup"

say "Installing the /simpro skill for Claude Code"
mkdir -p "$HOME/.claude/skills"
rm -rf "$HOME/.claude/skills/simpro"
cp -R .claude/skills/simpro "$HOME/.claude/skills/simpro"

if [ -t 0 ] && [ -t 1 ]; then
  say "Connecting to simPRO"
  simpro setup
else
  say "Done. Now run this in a terminal to enter your API details:  simpro setup"
fi
