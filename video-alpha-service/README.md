# ProjectBS Video Alpha Service (F)

`assetId`만 받아 원본 비디오를 프레임으로 추출하고 RGBA 프레임을 생성합니다. `model-matting`은
BiRefNet으로 초기 마스크를 만들고 SAM2로 추적한 뒤 BiRefNet으로 경계를 정밀화합니다. API 입력으로
경로나 파일 바이트를 받지 않으며 A/E 공개 HTTP API만 사용합니다.

## API

- `POST /v1/jobs` — `projectId`, `sourceAssetId`, 검증된 `processingSpec`으로 작업 생성. 상태는 `awaiting-confirmation`.
- `GET /v1/jobs/{id}` — read-before-write용 현재 revision/status 조회.
- `POST /v1/jobs/{id}/run` — `{observedRevision, confirmed:true, processingSpec}`으로 단 한 작업 실행.
- `GET /health` — ffmpeg/ffprobe 가용성.

처리 사양은 `alphaMethod: model-matting` 또는 `chroma-key`, `outputFormat: rgba-png`를 지원합니다.
`model-matting`은 `fps`, `width`, `height`, `temporalStrength`, `maxFrames`, `trackingModel`,
`mattingModel`을 설정할 수 있습니다. `chroma-key`는 선택적
`chromaKey.color`(`green`, `blue`, `0xRRGGBB`)를 허용합니다. 색상을 생략하면 green입니다. 실행 시
전달된 processingSpec은 생성 시 확정된 값과 일치해야 합니다. 원본 프레임 ZIP, RGBA 프레임 ZIP,
manifest JSON은 E에 각각 별도 asset으로 업로드됩니다. manifest는 fps/frameCount/입출력 asset ID/두
ZIP의 SHA-256을 담습니다.

실패 상태는 `failed`이고 `lastError`를 저장합니다. 동일 작업 재시도는 GET으로 최신 revision을 읽은 뒤 다시 확인하여 실행합니다. `running`/`completed` 작업은 재실행할 수 없습니다.

```sh
PYTHONPATH=. python3 run.py --data-service-url http://127.0.0.1:8030 --file-service-url http://127.0.0.1:8040
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

## 경계

- A: `/v1/workflows/sessions/{jobId}` aggregate를 작업 정본으로 사용합니다.
- E: 서명 다운로드 링크와 `/v1/uploads`만 사용합니다. 저장 경로는 알지 못합니다.
- D: processing-spec과 sourceAssetId를 전달하고, F 완료 결과의 asset ID만 tool result로 받습니다.
- G: 현재 run 계약(`/v1/jobs`, `observedRevision`, `confirmed`, `processingSpec`)과 호환됩니다. create에는
  프로젝트 격리를 위해 `projectId`가 필수이므로 G의 `job_create` 스키마와 전달 payload에도
  `projectId`를 required string으로 추가해야 합니다.

상시 실행, LaunchAgent, MCP 등록은 포함하지 않습니다.
