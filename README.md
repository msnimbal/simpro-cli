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

## 2. Install and connect (about 3 minutes)

You need Python 3.9 or newer and Git. Run these in a normal **terminal window** (Terminal on Mac,
PowerShell on Windows), not inside Claude.

**Mac / Linux**

```bash
git clone https://github.com/msnimbal/simpro-cli.git ~/simpro-cli
cd ~/simpro-cli
./install.sh
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/msnimbal/simpro-cli.git $HOME\simpro-cli
cd $HOME\simpro-cli
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Python missing on Windows? Install it from [python.org](https://www.python.org/downloads/) and tick
**Add python.exe to PATH**, then run the installer again.

The installer sets up the `simpro` command and the `/simpro` skill for Claude Code, then asks for:

1. your simPRO build URL (e.g. `yourcompany.simprosuite.com`)
2. Company ID (press Enter for `0`)
3. Client ID
4. Client secret (hidden as you type)
5. whether to start in read-only mode (the default, recommended while you try it)

It saves them to a private file on your computer and tests the connection. When you see
`Connected`, open a **new** terminal window (so it can find the `simpro` command), start Claude Code
and type `/simpro`.

The secret is typed into your own terminal and saved locally. It never goes through Claude, so
never paste it into a chat.

To change the details later, run `simpro setup` again. To switch off read-only mode, set
`SIMPRO_READ_ONLY=false` in the settings file (`simpro config path` shows where it is).
To update the tool: `cd` into the folder, `git pull`, and run the installer again.

### Manual install

```bash
git clone https://github.com/msnimbal/simpro-cli.git
cd simpro-cli
pipx install .        # or:  pip install --user .
simpro setup          # asks for the details above and tests the connection
```

No pipx? `python -m pip install --user pipx` (or `brew install pipx` on Mac). To get `/simpro` outside
this folder, copy `.claude/skills/simpro` into `~/.claude/skills/` (`%USERPROFILE%\.claude\skills\` on Windows).
You can also skip installing and run `python3 simpro_cli.py ...` instead of `simpro ...`.

Prefer editing a file? `simpro config init` creates the settings file and prints where it is; fill in
the values in a text editor, then run `simpro test`.

## 3. Use it with Claude Code

Open Claude Code in any folder and type `/simpro` followed by what you want:

```
/simpro jobs completed this week that haven't been invoiced
/simpro draft invoice for job 1234
```

`./install.sh` installs the skill globally, so it works from any folder. (Inside this repo folder it
is also picked up automatically from `.claude/skills/simpro/`, and `CLAUDE.md` teaches Claude the
same rules.)

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
| First-time setup (asks for your API details) | `simpro setup` |
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
