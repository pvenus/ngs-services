# Workflow persistence contract

Execution Service는 SQLite와 파일 경로에 접근하지 않습니다. 운영 adapter는 Project Data Service의 다음
도메인 API만 사용합니다.

```text
GET /v1/workflows/sessions/{sessionId}
PUT /v1/workflows/sessions/{sessionId}
GET /v1/workflows/sessions/{sessionId}/steps
PUT /v1/workflows/sessions/{sessionId}/steps/{stepId}
GET /v1/workflows/sessions/{sessionId}/artifacts/{artifactId}
PUT /v1/workflows/sessions/{sessionId}/artifacts/{artifactId}
```

모든 GET record 응답은 다음 envelope입니다.

```json
{
  "id": "exec.001",
  "revision": 2,
  "resource": {},
  "createdAt": "2026-09-28T00:00:00Z",
  "updatedAt": "2026-09-28T00:01:00Z"
}
```

모든 PUT은 read-before-write로 얻은 revision을 사용합니다.

```json
{
  "requestId": "stable-idempotency-key",
  "expectedRevision": 2,
  "actor": "project-execution-service",
  "reason": "advance workflow step",
  "resource": {}
}
```

생성은 `expectedRevision: 0`, 갱신은 현재 revision을 사용합니다. `409`는 revision 충돌, `422`는
상태 전이 또는 schema 오류입니다. Data Service가 session/step/artifact 쓰기를 하나의 transaction으로
묶는 batch endpoint를 제공하기 전까지 production adapter는 aggregate session payload를 정본으로 쓰고,
step/artifact endpoint는 조회 projection으로 취급합니다.

필요한 A 확장:

- 위 workflow allowlist endpoint
- session aggregate revision과 requestId 멱등성
- session + step + artifact atomic batch write
- 임의 SQL/테이블/경로를 받지 않는 도메인 검증
