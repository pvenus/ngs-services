# OPS official requests and services

This owner-local view binds only to `127.0.0.1:8090`. It does not start legacy
consumers or submit or cancel requests. The request list reads the Request
Service's MCP `request_list`; request detail reads the dedicated Workflow
ledger without writing to it.

The **알람** tab at `/alarms` shows only Workflow tasks whose status is
`failed` and whose `retry_count` is at least 3, newest first (up to 200).
A third retry that is running and any completed task are excluded. The page
loads on open or manual refresh; it sends no push or other notifications.
Before the retry migration, a valid ledger without `retry_count` returns an
empty list; missing or unreadable ledgers are shown as an error.

Staging verification:

```sh
/opt/homebrew/bin/python3.12 -B -m unittest -q
node --check app.js
node --test test_alarms_ui.mjs
```
