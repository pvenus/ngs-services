from __future__ import annotations

from .models import ActionDefinition, CodexTemplate, KnowledgeEntry, ServiceDefinition
from .service import CatalogService


def load_minimum_catalog(service: CatalogService) -> None:
    """Create only the six coarse-grained records required for the first workflow slice."""
    entries = [
        KnowledgeEntry("rule.execution.common", "common-execution", "공통 실행 규칙", "단계 실행의 공통 경계",
                       {"rules": ["한 번에 다음 단계 하나만 실행", "부족한 입력은 추측하지 않음", "대형 결과는 Asset ID로 연결"]}, status="active"),
        KnowledgeEntry("rule.storage.files", "storage-file", "스토리지·파일 규칙", "이동 가능한 실제 파일 저장 규칙",
                       {"rules": ["실제 파일은 관리 스토리지에 저장", "DB에는 상대 경로와 해시 저장", "외부에는 다운로드 링크 제공"]}, status="active"),
        KnowledgeEntry("service.video-alpha.fixed", "service-fixed", "Video Alpha 고정 지식", "비디오 알파 서비스의 불변 경계",
                       {"rules": ["원본 보존", "프레임과 RGBA 결과 분리", "실행 전에 현재 Job 조회"]}, status="active"),
        KnowledgeEntry("action.video-alpha.compose", "action-execution", "처리 설정 작성 지식", "Codex가 처리 설정을 작성할 때의 최소 기준",
                       {"required": ["sourceAssetId", "alphaMethod", "outputFormat"]}, status="active"),
        KnowledgeEntry("template.processing-spec.knowledge", "codex-template", "Processing Spec 템플릿 지식", "Codex 작성물의 역할",
                       {"artifactType": "processing-spec", "policy": "구조화 슬롯과 제출 JSON을 함께 작성"}, status="active"),
        KnowledgeEntry("validation.processing-spec", "validation", "Processing Spec 검증", "실행 전 필수 검증",
                       {"checks": ["필수 입력 존재", "출처 연결", "금지 행동 미포함"]}, status="active"),
    ]
    for entry in entries: service.put_knowledge(entry)
    template = CodexTemplate(
        id="codex.processing-spec.v1", artifact_type="processing-spec", purpose="단계 실행용 구조화 설정 작성",
        required_context_ids=["template.processing-spec.knowledge", "validation.processing-spec"],
        required_inputs=["sourceAssetId", "alphaMethod", "outputFormat"],
        compose_instruction="조회 결과와 사용자 입력만 사용해 처리 설정을 작성한다.",
        user_question_conditions=[{"whenMissing": "alphaMethod", "question": "알파 처리 방식을 선택해 주세요."}],
        prerequisite_get="video-alpha.job-get", execution_tool="video-alpha.apply",
        forbidden_actions=["레거시 문서 탐색", "입력값 임의 확정", "원본 파일 덮어쓰기"],
        submission_schema={"type": "object", "required": ["sourceAssetId", "alphaMethod", "outputFormat"]},
        status="active",
    )
    service.put_template(template)
    service.put_service(ServiceDefinition(
        "video-alpha", "Video Alpha Service", "비디오 프레임 분해와 알파 처리",
        status="active", fixed_knowledge_ids=["rule.execution.common", "rule.storage.files", "service.video-alpha.fixed"],
    ))
    service.put_action(ActionDefinition(
        "video-alpha.job-get", "video-alpha", "현재 작업 상태 조회", "read_only", "video_job_get",
        required_inputs=["jobId"], output_type="video-job-summary", status="active",
    ))
    service.put_action(ActionDefinition(
        "video-alpha.compose-spec", "video-alpha", "Codex 처리 설정 작성", "controlled_write", "artifact_submit",
        prerequisite_get="video-alpha.job-get", required_inputs=["jobId"], output_type="processing-spec",
        knowledge_ids=["action.video-alpha.compose"], template_id="codex.processing-spec.v1", status="active",
    ))
