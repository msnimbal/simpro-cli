# simpro-cli

> **Unofficial.** This project is not affiliated with, endorsed by or supported by Simpro Group.
> "simPRO" is a trademark of its owner. You use this tool with your own API credentials, at your own risk.

A small command-line tool that lets **Claude Code** (or you) work with your simPRO account
through the simPRO REST API. It needs Python only, with no other dependencies.

- Reads anything in simPRO (jobs, customers, quotes, invoices, schedules, stock and more)
- Creates and updates records, but only after a dry-run preview and an explicit `--execute`
- Logs every change it makes to an audit log
- Has an optional read-only switch that blocks every change

---

## 1. Create an API application in simPRO (admin, about 5 minutes)

1. In simPRO go to **Setup → System Setup → Integrations → API** (wording may vary slightly by version).
2. Add a new API application that uses the **Client Credentials** (machine-to-machine) grant.
3. Copy the **Client ID** and **Client Secret**. Keep them private and don't paste them into any chat.
4. Note your **build URL**, e.g. `https://yourcompany.simprosuite.com`, and your **Company ID**
   (usually `0`).
5. Recommended: link the API app to a simPRO user that can view jobs and create invoices but
   **cannot approve** them.

## 2. Install (2 minutes)

You need Python 3.9 or newer (`python3 --version`) and Git.

```bash
git clone https://github.com/msnimbal/simpro-cli.git ~/simpro-cli
cd ~/simpro-cli
pipx install .        # recommended; or:  pip install --user .
simpro --version
```

To update later: `cd ~/simpro-cli && git pull && pipx install --force .`

No pipx? Install it with `brew install pipx` on Mac or `python -m pip install --user pipx` on Windows.
Or skip installing and run `python3 ~/simpro-cli/simpro_cli.py ...` instead of `simpro ...`.

## 3. Add your credentials (in a text editor, not in chat)

```bash
simpro config init     # creates the private settings file and prints where it is
```

Open the file it prints in a text editor:

- Mac/Linux: `~/.config/simpro-cli/.env` (e.g. `open -e ~/.config/simpro-cli/.env`)
- Windows: `%APPDATA%\simpro-cli\.env` (e.g. `notepad %APPDATA%\simpro-cli\.env`)

Fill in `SIMPRO_BUILD_URL`, `SIMPRO_COMPANY_ID`, `SIMPRO_CLIENT_ID` and `SIMPRO_CLIENT_SECRET`,
then save. Then run:

```bash
simpro test
```

You should see `"ok": true` and your company name.

> Tip: set `SIMPRO_READ_ONLY=true` in the same file while you're trying it out.
> Change it to `false` when you're ready to let it create draft invoices.

## 4. Use it with Claude Code

Open Claude Code **in this folder** so it picks up `CLAUDE.md`, which teaches it the tool and the
safety rules:

```bash
cd ~/simpro-cli
claude
```

To make it available from any folder, copy the rules into your global Claude Code memory:

```bash
mkdir -p ~/.claude && cat CLAUDE.md >> ~/.claude/CLAUDE.md
```

### The `/simpro` command

This repo ships a Claude Code skill at `.claude/skills/simpro/`, so inside this folder you can type
`/simpro` followed by what you want:

```
/simpro jobs completed this week that haven't been invoiced
/simpro draft invoice for job 1234
```

To use `/simpro` from any folder, install the skill globally:

```bash
mkdir -p ~/.claude/skills && cp -R .claude/skills/simpro ~/.claude/skills/
```

Read-only `simpro` commands run without prompting. Anything that changes data still shows a dry run
first and needs your explicit yes.

Or skip the command and just ask in plain English, for example:

- *"Show me jobs completed this week that haven't been invoiced."*
- *"Get everything on job 1234 and draft an invoice from the notes."*
- *"Which customers have quotes still pending?"*

For any change, Claude shows you a preview first and only sends it after you say yes.

## Command cheat sheet

| Task | Command |
|---|---|
| Check connection | `simpro test` |
| Common endpoints | `simpro endpoints` |
| One job | `simpro get jobs/1234` |
| List jobs (selected fields) | `simpro get jobs --columns ID,Name,Stage --format table` |
| Every customer | `simpro get customers/companies --all` |
| Filter | `simpro get jobs -q Stage=Complete` |
| Preview a change | `simpro post customerInvoices -d @invoice.json` |
| Apply it | `simpro post customerInvoices -d @invoice.json --execute --note "why"` |
| What has been changed | `simpro audit --format table` |
| Forget cached login | `simpro config logout` |

## Security notes

- The credentials file and the token cache are readable only by your user account.
- The CLI refuses to send your token to any host other than your simPRO build.
- Changing or revoking the Client Secret in simPRO immediately cuts off access.
  Update the `.env` file afterwards.
- The audit log (`simpro config path` shows where it is) records every executed write.

## Development

```bash
python -m unittest discover -s tests -v   # runs against a local mock simPRO server
```

Issues and pull requests are welcome. Please don't include real credentials, customer data or
API responses from a live account in issues.

## License

MIT. See [LICENSE](LICENSE).
