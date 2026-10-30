# simPRO CLI: instructions for Claude Code

This folder holds `simpro`, a command-line tool for the simPRO REST API (v1.0).
Use it to read and manage this business's simPRO data. It is the only approved way
to reach simPRO from this machine, so don't write ad-hoc scripts that call the API directly.

## Ground rules

1. **Never read, print or ask for the credentials.** They are in the file shown by
   `simpro config path`. Do not open that file, `cat` it or echo the environment. If auth fails,
   tell the user to check the file themselves. If settings are missing or invalid, tell the user to run
   `simpro setup` in their own terminal (it asks for the secret; never run it for them and never ask
   them to paste the secret into chat).
2. **Reads are free.** `simpro get ...` can be run without asking.
3. **Writes need the user's explicit OK.** For any `post`, `put`, `patch` or `delete`:
   - first run it **without** `--execute` (dry run) and show the user the method, URL and body
     in plain language
   - only add `--execute` after the user has clearly said yes **to that specific change**
   - always add `--note "<short reason>"` so the audit log says why
4. **Never approve, email or delete invoices**, and never delete jobs, customers or payments,
   unless the user asks for that exact action in this conversation. Invoices you create must be
   left at the Pending (draft) stage for the user to review in simPRO.
5. **Don't guess field names.** Before creating or updating something, `get` an existing record of
   the same type to see the real field names and shapes, and check
   https://developer.simprogroup.com/apidoc/ when unsure.
6. If a command returns `"exit_code": 7`, writes are switched off (`SIMPRO_READ_ONLY=true`).
   Tell the user. Don't try to work around it.

## Command reference

```
simpro test                                   # check credentials + list companies
simpro endpoints                              # map of common resource paths
simpro get <path> [options]                   # read
simpro post|put|patch|delete <path> -d JSON   # write (dry run unless --execute)
simpro audit --tail 20                        # recent executed writes
simpro config show                            # which settings are set (secrets masked)
```

Paths are relative to `/api/v1.0/companies/{companyID}/`:
`jobs/1234` → `/api/v1.0/companies/0/jobs/1234/`. Start with `/` for the API root (`/companies/`).

`get` options:

- `-q KEY=VALUE` (repeatable) for query parameters, e.g. `-q Stage=Complete`
- `--columns ID,Name,Total` to return only these fields (simPRO `columns` parameter)
- `--all [--limit N]` to follow every page (250 per page)
- `--page N --page-size N` for manual paging
- `--meta` to include total and page counts
- `--format json|jsonl|table`

Write body: `-d '{"Field": "value"}'`, `-d @file.json` or `-d -` (stdin).
For anything longer than a line, write the JSON to a temp file and use `-d @file.json`.

Exit codes: 0 ok · 2 usage · 3 auth/permission · 4 not found · 5 rejected data (422/400; read
`body.errors`) · 6 server/rate limit · 7 writes disabled. Errors go to stderr as JSON.

## Main workflow: draft invoice from job notes

1. Find the job: `simpro get jobs/<id>`, or search the list with
   `simpro get jobs --all --columns ID,Name,Customer,Site,Stage --limit 500` and filter.
2. Gather what the user normally copies by hand:
   - the job record (`Description`, `Notes` and similar fields)
   - `simpro get jobs/<id>/notes`
   - sections and cost centres: `simpro get jobs/<id>/sections`, then
     `simpro get jobs/<id>/sections/<sid>/costCenters`, then the labour, materials (`catalogs`)
     and work orders under each cost centre. Technician notes are often on work orders.
3. Check for existing invoices so nothing is billed twice:
   `simpro get customerInvoices -q Jobs.ID=<id>` (filter syntax may vary; otherwise list recent
   invoices and match on the job).
4. Write a clean, customer-facing description from the notes. Fix spelling, use one line per
   task, and leave out internal remarks. Show it to the user.
5. Learn the invoice payload shape by fetching one existing invoice:
   `simpro get customerInvoices --page-size 1`, then `simpro get customerInvoices/<thatID>`.
6. Build the payload with the job, the description and the Pending stage. Dry-run it:
   `simpro post customerInvoices -d @invoice.json`
7. After the user approves: rerun with `--execute --note "Draft invoice for job <id>"`.
   Report the new invoice ID and remind the user to review and approve it in simPRO.

## Tips

- simPRO limits request rates. The CLI retries automatically on HTTP 429, so don't loop
  `--all` over huge collections without `--limit`.
- Use `--columns` to keep responses small.
- Dates are ISO `YYYY-MM-DD`. Money fields are numbers, not strings.
