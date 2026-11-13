# Project Laya Selection Service

Independent C service for the new ProjectBS architecture. It classifies one user intent into one active service/action and returns the smallest execution handoff needed by Codex. It does not execute tools, mutate project data, open SQLite, or scan legacy documents.

## API

```text
GET  /health
POST /v1/selections
```

Request:

```json
{"intent":"테스트 비디오를 분해하고 알파를 적용해줘","inputs":{"jobId":"job.1"}}
```

Response fields:

- `serviceId`, `actionId`: the single selected catalog records
- `contextEntryIds`: IDs only; no knowledge body
- `templateId`, `templateSlots`: Codex artifact contract
- `prerequisiteGet`, `firstTool`: mandatory read-before-write handoff
- `missingInputs`: fields Codex must obtain, never guess
- `nextStep`: exactly one of `collect-input`, `call-prerequisite-get`, `compose-codex-artifact`, `ready-for-selected-action`
- `selectionReason`: short auditable reason

## Boundary

Production reads only Project Catalog Service's public endpoints:

```text
GET /v1/catalog/services?status=active
GET /v1/catalog/actions?status=active
GET /v1/catalog/execution-context/{actionId}
```

The Catalog Service remains responsible for combining fixed, action, and template knowledge. Laya never reads the Data Service, SQLite, file storage, or legacy catalogs directly.

## Run and test

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 run.py --catalog-url http://127.0.0.1:8031 --port 8040
```

No LaunchAgent, persistent process, or Codex MCP registration is installed by this project.
