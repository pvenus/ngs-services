# ProjectBS Project Data Service

새 ProjectBS 서비스들이 공유하는 **단일 SQLite writer와 이동 가능한 Storage Root 경계**입니다.
기존 ProjectBS 서비스·MCP·문서는 모두 레거시로 간주하며 이 서비스가 읽거나 실행하지 않습니다.

## 원칙

- SQLite를 직접 여는 프로세스는 이 서비스 하나입니다.
- 다른 백엔드는 HTTP API만 사용합니다.
- 파일 바이트는 SQLite BLOB가 아니라 Storage Root 안의 실제 파일로 보존합니다.
- DB에는 Storage Root 기준 상대 경로, SHA-256, 크기, MIME만 저장합니다.
- 절대 경로, `..`, 저장소 밖으로 나가는 심볼릭 링크를 거부합니다.
- 쓰기는 `requestId`, `actor`, `reason`을 요구하며 갱신은 `expectedRevision`도 요구합니다.
- 모든 성공한 쓰기는 감사 로그를 남기고 `requestId` 재전송은 저장된 응답을 재생합니다.

## Storage Root

```text
<storage-root>/
├─ storage.json
├─ database/projectbs.sqlite3
├─ assets/
├─ jobs/
├─ temporary/
├─ backups/
└─ project-data-service 파일은 포함하지 않아도 됨
```

DB에 외장 SSD의 볼륨명이나 사용자 홈 경로를 저장하지 않으므로 전체 폴더를 새 Mac으로 옮길 수
있습니다. 서비스는 새 위치를 `--storage-root`로 전달받아 같은 상대 경로를 해석합니다. 이동 전에는
서비스를 정상 종료하여 SQLite WAL을 닫아야 합니다.

## 실행

추가 패키지 설치 없이 Python 3.9 이상으로 실행합니다.

```sh
cd data-service
python3 run.py --storage-root "$HOME/NGS-data/backend" --port 8030
```

자동 실행이나 Codex MCP 등록은 하지 않습니다.

## 초기 API

| Method | Path | 역할 |
|---|---|---|
| GET | `/health` | 서비스와 스키마 상태 |
| GET | `/api/storage/status` | Storage Root 상태 |
| GET | `/api/schema/version` | DB 스키마 버전 |
| POST | `/api/projects` | 프로젝트 생성 |
| GET | `/api/projects/{id}` | 프로젝트 조회 |
| PUT | `/api/projects/{id}` | expectedRevision 기반 갱신 |
| POST | `/api/assets/register` | Storage Root에 이미 저장된 실제 파일을 자산으로 등록 |
| GET | `/api/assets/{id}` | 자산 메타데이터 조회 |
| GET | `/api/audit/{resourceType}/{resourceId}` | 변경 감사 로그 |

### Workflow aggregate API

Execution Service가 사용하는 정본은 세션 하나에 `steps` 배열과 `artifacts` 맵을 포함한 aggregate입니다.
세 값은 단일 SQLite 행과 단일 트랜잭션으로 함께 저장되므로 일부만 갱신되는 상태가 없습니다.

| Method | Path | 역할 |
|---|---|---|
| GET | `/v1/workflows/sessions/{sessionId}` | 세션 aggregate 조회 |
| PUT | `/v1/workflows/sessions/{sessionId}` | 세션 aggregate 생성·개정 갱신 |
| GET | `/v1/workflows/sessions/{sessionId}/steps` | 저장된 aggregate의 단계 projection |
| GET | `/v1/workflows/sessions/{sessionId}/artifacts/{artifactId}` | 저장된 aggregate의 산출물 projection |

PUT은 `requestId`, `expectedRevision`, `actor`, `reason`, `resource`를 요구합니다. 생성은
`expectedRevision: 0`, 갱신은 현재 revision을 사용합니다. 세션 GET/PUT 응답은 다음 고정 envelope입니다.

```json
{
  "id": "exec.001",
  "revision": 2,
  "resource": {},
  "createdAt": "2026-09-28T00:00:00+00:00",
  "updatedAt": "2026-09-28T00:01:00+00:00"
}
```

세션·단계·산출물 ID는 안전한 식별자만 허용합니다. workflow payload는 실제 파일 대신 Asset ID를
사용해야 하며 SQL, 테이블, DB 또는 파일 경로 필드는 거부합니다. projection endpoint는 조회 전용이고
모든 상태 변경은 세션 aggregate PUT으로 수행합니다.

### Allowlist 데이터 API

B 서비스가 사용하는 데이터 경계는 네 컬렉션으로 고정됩니다.

```text
services
actions
knowledge
templates
```

| Method | Path | 역할 |
|---|---|---|
| GET | `/v1/data/{collection}/{id}` | 단일 리소스 조회 |
| GET | `/v1/data/{collection}?status=active` | 상태별 목록 조회 |
| PUT | `/v1/data/{collection}/{id}` | 생성 또는 개정 갱신 |

PUT 요청:

```json
{
  "requestId": "request.service.video-alpha.001",
  "expectedRevision": 0,
  "reason": "비디오 알파 서비스 등록",
  "resource": {
    "projectId": "ProjectBS",
    "title": "Video Alpha Service",
    "status": "active"
  }
}
```

- `expectedRevision: 0`은 새 리소스 생성입니다.
- 기존 리소스는 현재 revision과 일치해야 합니다.
- 응답은 `collection`, `id`, `revision`, `status`, `resource`, 생성·수정 시각을 반환합니다.
- `actor`는 선택 필드이며 생략하면 감사 로그에 `data-api`로 기록됩니다.
- `actions` 리소스에는 존재하는 `serviceId`가 필수입니다.
- `knowledge`에는 `category`, `entryType`이 필수입니다.
- `templates`에는 `artifactType`이 필수입니다.
- 허용되지 않은 컬렉션, 임의 테이블명, SQL, 파일 경로, DB 연결 정보는 API에서 접근할 수 없습니다.

파일 업로드 스트리밍은 이후 File Transfer Service가 담당합니다. File Service는 파일을
`temporary/`에 받고 검증 후 `assets/`로 원자적으로 옮긴 다음 `/api/assets/register`를 호출합니다.
외부 파일 경로나 URL을 등록 API에 넘기는 방식은 지원하지 않습니다.

## 공통 쓰기 Envelope

```json
{
  "requestId": "request.project.create.001",
  "actor": "service-registry",
  "reason": "ProjectBS 프로젝트 생성",
  "project": {
    "id": "ProjectBS",
    "name": "ProjectBS"
  }
}
```

갱신은 다음 필드도 필요합니다.

```json
{
  "expectedRevision": 1,
  "changes": {}
}
```

- 같은 `requestId`와 같은 요청은 기존 응답을 재생합니다.
- 같은 `requestId`를 다른 요청에 재사용하면 `422`입니다.
- `expectedRevision`이 다르면 `409`입니다.

## B 서비스 연동 경계

Service Registry와 Knowledge/Codex Template 담당은 다음 기반 테이블 위에 도메인 계약을 추가합니다.

- `service_records`
- `service_actions`
- `knowledge_entries`
- `codex_templates`

Workflow 후속 단계가 사용할 기반 테이블도 미리 존재합니다.

- `workflow_sessions`
- `workflow_steps`
- `artifacts`

B는 SQLite를 직접 열지 않고 Data Service에 도메인 API를 추가하거나 이 서비스의 저장소 계층을 통해
트랜잭션을 구현해야 합니다. 테이블의 `payload_json`은 초기 경계이며 실제 필수 필드는 B 작업에서
확정한 뒤 새 migration으로 정규화할 수 있습니다.

## 테스트

```sh
python3 -m unittest discover -s tests -v
```

테스트는 경로 탈출·심볼릭 링크 탈출, 개정 충돌, 멱등성, 감사 로그, 실제 파일 해시 등록,
Storage Root 이동, 기반 테이블, HTTP 계약을 검증합니다.

### Folder read contention

Project-wide and narrower reads have separate capacity. An empty scope
or one path component allows one scan and two file reads; narrower scopes
allow four compares and six file reads. MCP and HTTP share these limits.
Contention waits in arrival order within the appropriate pool for at most
five seconds, without marking the workflow failed or restarting the read.
Only an expired wait returns the existing `503 FOLDER_BUSY`; no snapshot,
hash, or deletion checks are relaxed. This wait covers admission to the
read, not the time allowed for an acquired operation to finish.

Logs identify each request and its blockers in `pds_folder_wait`, then record
`pds_folder_wait_acquired` or `pds_folder_busy` with elapsed wait time.
Match the blocker ID to `pds_folder_busy_holder_released` for its full hold time.
Operation names distinguish MCP and legacy HTTP calls. These IDs are local to
PDS (not workflow IDs). Normal uncontended reads, file contents, individual file
paths, hashes and credentials are not logged.
