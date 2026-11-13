# NGS Services

Integrated local backend for NGS requests, execution and canonical data. Frontend: pvenus/ngs-platform. Functional generation workers: pvenus/ngs-workers.

## Services

| Service | Role | Port |
|---|---|---:|
| data-service | Canonical data and revisions | 8030 |
| catalog-service | Capability catalog | 8031 |
| execution-service | Execution status and results | 8032 |
| laya-service | Request routing and context selection | 8040 |
| file-service | File access and delivery | 8041 |
| video-alpha-service | Video alpha adapters | 8050 |
| workflow-service | Request orchestration | 8060 |

## Start

Install Python 3.12. HTTP startup uses the standard library, not a model environment.

```sh
git clone https://github.com/pvenus/ngs-services.git
cd ngs-services
python3.12 -B runtime/stack.py --storage-root "$HOME/NGS-data/backend"
```

Storage must be an absolute path outside this checkout. Existing data is reused, not reset. A new directory gets empty database schemas and a local file-signing secret. All services bind to loopback only. Ctrl-C stops only this group.

```sh
python3.12 -B runtime/stack.py --storage-root "$HOME/NGS-data/backend" --print-plan
# Parent must exist; destination must NOT exist. No production data is touched.
python3.12 -B tests/smoke_stack.py --storage-root "$HOME/NGS-backend-smoke-new" --port-base 28030
```

## Data and configuration

No generated images, audio, video, models, DBs, caches, logs or authentication files are included. Keep them outside source. NGS holds requests and copies; Data service owns canonical records.

Set NGS_PORTRAIT_CACHE for image-reference cache and NGS_KNOWLEDGE_SEED for optional knowledge seeds. Export these variables in your shell; .env.example is documentation and is not auto-loaded.

## Optional models

HTTP startup downloads no model and performs no inference. Video alpha inference requires an isolated Python environment selected by VIDEO_ALPHA_MODEL_PYTHON plus its model dependencies. Model weights are excluded: review official model licenses before installing. Generation workers belong in the separate consolidated Workers project.

## Scope

Initial independent snapshot from the running seven-service source at a368b4055d3b, with machine-specific paths made configurable. This publication does not switch production processes or data. The smoke test checks seven health endpoints and the empty Data-to-Workflow read path. Content generation, inference, public-network access and multi-user security are not covered.

No blanket open-source license is granted at this time. Public visibility does not itself grant unrestricted redistribution or relicensing. Model and third-party dependency rights remain with their owners.

Concept seed values and character-specific profile proposals have been removed; only empty structural templates are shipped.
