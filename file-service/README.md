# ProjectBS File Service

실제 파일 바이트를 이동 가능한 Storage Root에 보존하고, 외부에는 내부 경로 대신 서명된 만료형
다운로드 URL을 제공하는 독립 서비스입니다. SQLite를 열지 않으며 자산 메타데이터는 Project Data
Service HTTP API로만 등록·조회합니다.

## 1차 API

- `POST /v1/uploads`: `application/octet-stream` 단일 파일 업로드
  - 필수 헤더: `X-Project-Id`, `X-Filename`, `X-Asset-Kind`, `X-Purpose`
  - 선택 헤더: `X-File-Mime`
- `POST /v1/assets/{assetId}/download-links`
  - JSON: `expiresInSeconds`, 선택 `requestedFilename`
- `GET|HEAD /v1/downloads/{signedToken}`
  - 실제 바이트 스트리밍, `Content-Disposition`, 단일 HTTP Range 지원
- `GET /health`
- `GET|HEAD /v1/assets/{assetId}`
  - 공개 가능한 자산 메타데이터만 반환
  - `relativePath`와 Storage Root 등 내부 저장 위치는 반환하지 않음

외부 경로 또는 URL만 받아 자산으로 등록하는 API는 제공하지 않습니다. 내부 생성 서비스는
`FileService.register_internal` 경계를 사용하며, 파일이 먼저 `assets/generated/` 아래에 실제로
존재해야 합니다. 향후 내부 HTTP API를 열 때는 서비스 인증을 필수로 추가해야 합니다.

## 안전 경계

- 임시 파일에 스트리밍한 뒤 fsync, MIME/파일명/크기 검증, SHA-256 계산, 원자 이동
- 절대 경로, `..`, 역슬래시, 심볼릭 링크 탈출 거부
- 토큰은 HMAC-SHA256 서명되며 상대 경로를 포함하지 않음
- 다운로드마다 Data Service에서 현재 자산 상태를 다시 확인
- 최대 업로드 512 MiB, 최대 다운로드 4 GiB 기본값
- 토큰 최대 유효기간 7일
- 응답에 `nosniff`, 다운로드에 `Content-Disposition`과 `Accept-Ranges`

1차 범위에서는 인증/권한과 HTTPS가 상위 프록시 책임입니다. 외부 공개 전에 팀 인증, 프로젝트
권한, 속도 제한, 감사 로그가 반드시 추가되어야 합니다.

## 실행

```sh
export PROJECTBS_FILE_TOKEN_SECRET='32바이트 이상의 비밀값'
python3 run.py --storage-root $HOME/NGS-data/backend --data-service http://127.0.0.1:8030 --port 8040
```

자동 실행과 MCP 등록은 하지 않습니다.

## 후속 계약

- A Data Service: 현재 `/api/assets/register`, `/api/assets/{id}` 계약을 사용합니다. 다운로드 감사와
  프로젝트 권한 검증 API는 후속 확장이 필요합니다.
- D Orchestrator: 파일 경로나 URL 대신 `assetId`만 단계 입력/출력으로 저장해야 합니다.
- 분할 업로드: 세션 생성→순번 파트 PUT→전체 SHA-256 확인→complete 계약으로 추가합니다.
- ZIP: 프레임 묶음을 즉석 응답하지 않고 `assets/generated/`의 파생 자산으로 먼저 생성·등록합니다.

## 테스트

```sh
python3 -m unittest discover -s tests -v
```
