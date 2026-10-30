---
name: simpro
description: Work with the business's simPRO data through the `simpro` CLI. Use when the user types /simpro or asks about simPRO jobs, customers, quotes, invoices, schedules or stock, or wants a draft invoice created from job notes.
argument-hint: "<what you want, e.g. 'draft invoice for job 1234' or 'jobs completed this week'>"
allowed-tools: Bash(simpro get:*), Bash(simpro test:*), Bash(simpro endpoints:*), Bash(simpro audit:*), Bash(simpro config show:*), Bash(simpro config path:*)
---

# /simpro

The user's request: $ARGUMENTS

If the request above is empty, run `simpro test` to confirm the connection, then ask what they would like to do.

Use the `simpro` command-line tool, the only approved way to reach simPRO from this machine.
Don't write ad-hoc scripts that call the API directly. If `simpro` is not on PATH, run
`python3 <path-to-simpro-cli>/simpro_cli.py` instead (see the README for install steps).

## Ground rules

1. **Never read, print or ask for the credentials.** They live in the file shown by
   `simpro config path`. Don't open that file, `cat` it or echo the environment. If auth fails,
   tell the user to check the file themselves. If settings are missing or invalid, tell the user to run
   `simpro setup` themselves in a normal terminal window (it asks for the secret, so never run it for
   them and never ask them to paste the secret into chat).
2. **Reads are free.** `simpro get ...` can be run without asking.
3. **Writes need the user's explicit OK.** For any `post`, `put`, `patch` or `delete`:
   - first run it **without** `--execute` (dry run) and show the method, URL and body in plain language
   - add `--execute` only after the user has clearly said yes **to that specific change**
   - always add `--note "<short reason>"` so the audit log says why
4. **Never approve, email or delete invoices**, and never delete jobs, customers or payments,
   unless the user asks for that exact action in this conversation. Invoices you create must be
   left at the Pending (draft) stage for the user to review in simPRO.
5. **Don't guess field names.** Before creating or updating something, `get` an existing record of
   the same type to see the real field names, and check https://developer.simprogroup.com/apidoc/ when unsure.
6. If a command returns `"exit_code": 7`, writes are switched off (`SIMPRO_READ_ONLY=true`).
   Tell the user. Don't try to work around it.

## Commands

```
simpro test                                   # check credentials + list companies
simpro endpoints                              # map of common resource paths
simpro get <path> [options]                   # read
simpro post|put|patch|delete <path> -d JSON   # write (dry run unless --execute)
simpro audit --tail 20                        # recent executed writes
simpro config show                            # which settings are set (secrets masked)
```

Paths are relative to `/api/v1.0/companies/{companyID}/` (`jobs/1234`). Start with `/` for the API root.

`get` options: `-q KEY=VALUE` (repeatable), `--columns ID,Name,Total`, `--all [--limit N]`,
`--page N --page-size N`, `--meta`, `--format json|jsonl|table`.

Write body: `-d '{"Field": "value"}'`, `-d @file.json` or `-d -`. For anything longer than a line,
write the JSON to a temp file and use `-d @file.json`.

Exit codes: 0 ok · 2 usage · 3 auth/permission · 4 not found · 5 rejected data (read `body.errors`) ·
6 server/rate limit · 7 writes disabled.

## Draft invoice from job notes

1. Find the job: `simpro get jobs/<id>`, or search with
   `simpro get jobs --all --columns ID,Name,Customer,Site,Stage --limit 500` and filter.
2. Gather the job record (`Description`, `Notes`), `simpro get jobs/<id>/notes`, and the sections →
   cost centres → labour, materials and work orders under each. Technician notes are often on work orders.
3. Check for existing invoices so nothing is billed twice: `simpro get customerInvoices -q Jobs.ID=<id>`
   (otherwise list recent invoices and match on the job).
4. Write a clean, customer-facing description: fix spelling, one line per task, no internal remarks.
   Show it to the user.
5. Learn the payload shape from one existing invoice (`simpro get customerInvoices --page-size 1`,
   then `simpro get customerInvoices/<ID>`).
6. Build the payload with the job, description and Pending stage; dry-run it with
   `simpro post customerInvoices -d @invoice.json`.
7. After approval, rerun with `--execute --note "Draft invoice for job <id>"`. Report the new invoice
   ID and remind the user to review and approve it in simPRO.

## Tips

- The CLI retries on HTTP 429, but don't loop `--all` over huge collections without `--limit`.
- Use `--columns` to keep responses small.
- Dates are ISO `YYYY-MM-DD`. Money fields are numbers, not strings.
