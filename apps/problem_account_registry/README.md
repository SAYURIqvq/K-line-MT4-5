# Problem Account Registry

Local web ledger for maintaining verified problem accounts.

## Service

```text
http://127.0.0.1:8776
```

Start only this service:

```powershell
powershell -ExecutionPolicy Bypass -File apps\problem_account_registry\start_account_registry_web.ps1
```

Open from this folder:

```text
open_account_registry_web.cmd
```

## Data File

The ledger Excel file is read from:

```text
local_data\problem_account_registry\problematic_accounts.xlsx
```

The web UI writes CRUD changes and version history back to this local Excel file.

## Main Features

- Add, edit, delete, and search accounts.
- Default sort by effective time from newest to oldest.
- Keep version history after edits.
- Show a vertical history timeline for each account.
- Export daily journal as Word: `journal_MMDD.docx`.
- Link accounts to generated K-line charts when matching charts exist.

## Safety Boundary

This app only reads and writes the local ledger file. It does not connect to MT4/MT5 Manager and must not modify any server-side account or trade state.
