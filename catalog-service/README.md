# Project Catalog Service

Independent B-domain implementation for the new ProjectBS architecture. It contains a service/action registry, six coarse operational knowledge categories, and structured Codex artifact templates. It does not read AgentDocs, legacy Laya catalogs, or any previous service database.

## Boundaries

- The shared Project Data Service is the only SQLite owner.
- This service depends on the `CatalogRepository` port.
- Tests use `InMemoryCatalogRepository` and create no database files.
- Production uses `HttpCatalogRepository` and reaches persistence only through the Data Service HTTP API.
- No LaunchAgent, MCP registration, or automatic startup is included.
- The minimum catalog is new operational data, not an import or summary of legacy documents.

## Coarse knowledge categories

1. `common-execution`
2. `storage-file`
3. `service-fixed`
4. `action-execution`
5. `codex-template`
6. `validation`

Details are added only when implementing the corresponding workflow slice.

## Domain API

```text
GET /health
GET|PUT /v1/catalog/services
GET|PUT /v1/catalog/actions
GET|PUT /v1/catalog/knowledge
GET /v1/catalog/knowledge/{id}
POST /v1/catalog/knowledge/query
GET|PUT /v1/catalog/templates
GET /v1/catalog/execution-context/{actionId}
```

`execution-context` returns the selected service, action, fixed knowledge, action knowledge, and optional Codex template. It does not scan files.

`GET /v1/catalog/knowledge/{id}` returns one snake_case domain knowledge object for an explicitly selected ID. IDs are limited to letters, digits, `.`, `_`, and `-` (maximum 128 characters). A missing ID returns `404`; an unsafe ID returns `422`. This endpoint calls only `repository.get("knowledge", id)` and never lists the catalog or reads files.

For a small explicit selection, `POST /v1/catalog/knowledge/query` accepts only this body:

```json
{"ids": ["rule.execution.common", "missing.entry"]}
```

The list must contain 1–20 safe IDs. The response keeps available entries in request order and reports unavailable IDs explicitly:

```json
{"entries": [{"id": "rule.execution.common"}], "missingIds": ["missing.entry"]}
```

All IDs are validated before any read. The endpoint performs one repository `get` per requested ID; it never calls `list`, expands the selection, or accesses SQLite/files directly.

Mutating or external-effect actions are invalid unless they declare a prerequisite read action or a previous-step output. A prerequisite GET must reference an existing read-only action.

## Test

```sh
cd catalog-service
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

See [DATA_SERVICE_CONTRACT.md](DATA_SERVICE_CONTRACT.md) for the persistence boundary required from parallel work A.

## Production repository adapter

```python
from catalog_service import CatalogService, HttpCatalogRepository

repository = HttpCatalogRepository("http://127.0.0.1:8030")
catalog = CatalogService(repository)
```

The adapter allowlists `services`, `actions`, `knowledge`, and `templates`. Each write first reads the current revision and then submits a controlled PUT containing `requestId`, `expectedRevision`, `reason`, and `resource`. It never opens SQLite or resolves a filesystem path.

## Run the HTTP service

Python 3.9 or newer is supported. The Data Service URL is mandatory; the catalog service does not open SQLite itself.

```sh
cd catalog-service
PYTHONPATH=. python3 run.py \
  --host 127.0.0.1 \
  --port 8040 \
  --data-service-url http://127.0.0.1:8030
```

Normal startup performs no catalog writes. For a deliberate first-time seed or explicit refresh, add `--bootstrap`:

```sh
PYTHONPATH=. python3 run.py \
  --port 8040 \
  --data-service-url http://127.0.0.1:8030 \
  --bootstrap
```

The `--bootstrap` option writes the minimum catalog through the Data Service and is intentionally not enabled by default. C and other clients can check `GET /health` on port 8040 before requesting catalog endpoints.
