# ProjectBS Execution Service

완성된 계획을 한 번에 실행하지 않고, 매 요청마다 **다음 단계 하나**만 결정하는 독립 서비스입니다.
SQLite를 직접 열지 않으며 운영 환경에서는 Project Data Service HTTP API만 사용합니다.

## 책임

- 실행 session과 step 상태 전이
- read-before-write와 optimistic revision
- GET / COMPOSE / EXECUTE / VALIDATE 단계 분리
- Codex 또는 사용자가 채워야 할 parameter slot 유지
- Codex가 제출한 artifact 검증
- 다음 도구 호출 하나 또는 필요한 입력 질문만 반환

실제 비디오 처리, MCP 등록, 상시 실행은 담당하지 않습니다.

## 실행

```sh
python3 run.py --port 8050
```

기본 실행은 개발용 in-memory 저장소입니다. 운영에서는 `HttpWorkflowRepository`를 주입합니다.

## API

### 실행 생성

```http
POST /v1/executions
```

```json
{
  "requestId": "req.create.001",
  "workflowType": "video-alpha.v1",
  "goal": "테스트 비디오를 프레임으로 분해하고 알파 처리",
  "inputs": {"jobId": "job.001"}
}
```

### 실행 조회

```http
GET /v1/executions/{sessionId}
```

### 한 단계 진행

```http
POST /v1/executions/{sessionId}/advance
```

```json
{
  "requestId": "req.advance.001",
  "expectedRevision": 1,
  "event": {
    "type": "tool_result",
    "stepId": "get-job",
    "output": {"sourceAssetId": "asset.video.001"}
  }
}
```

지원 event:

- `tool_result`: GET 또는 EXECUTE 결과 제출
- `input_supplied`: 사용자/Codex 입력 슬롯 채움
- `artifact_submitted`: Codex 작성물 제출
- `validation_result`: 검증 결과 제출

`expectedRevision`이 현재 session revision과 다르면 `409`입니다. 같은 `requestId`와 같은 요청의 재전송은
기존 응답을 재생하고, 다른 요청에 재사용하면 `422`입니다.

## 다음 행동 계약

응답의 `nextAction`은 항상 하나입니다.

- `execute_tool`: 지정된 GET 또는 실행 도구 호출
- `collect_input`: 누락 슬롯만 질문
- `compose_artifact`: Codex가 템플릿에 맞춰 artifact 작성
- `validate_result`: 결과 검증 수행
- `completed`: workflow 완료

## Data Service 연동

[DATA_SERVICE_CONTRACT.md](DATA_SERVICE_CONTRACT.md)에 session/step/artifact 저장 계약을 정의합니다.

## 테스트

```sh
python3 -m unittest discover -s tests -v
```
