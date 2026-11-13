# ProjectBS Workflow Service

Internal HTTP orchestration service. It calls Laya, Execution, File, and execution backends one step at a time.
Codex does not call the component services directly.

At workflow start, Laya selects one action and the service fetches only that action's execution context from
Catalog. The compact `codexContext` contains the selected instruction and knowledge bodies; no catalog scan is
exposed to Codex.

Public endpoints:

- `POST /v1/workflows`
- `GET /v1/workflows/{sessionId}`
- `POST /v1/workflows/{sessionId}/submit`
- `POST /v1/workflows/{sessionId}/execute`
- `GET /v1/workflows/{sessionId}/result`

Long model jobs are asynchronous: the first confirmed execute call returns `operation.status=accepted` or
`running`. Repeating `workflow_execute_next` polls and advances the workflow only after the backend job completes.

Run with `PYTHONPATH=. python3 run.py`. The default port is `8060`.
