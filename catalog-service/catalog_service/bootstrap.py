from __future__ import annotations

from .models import ActionDefinition, CodexTemplate, KnowledgeEntry, ServiceDefinition
from .service import CatalogService


def load_minimum_catalog(service: CatalogService) -> None:
    """Create only the six coarse-grained records required for the first workflow slice."""
    template = CodexTemplate(
        id="codex.processing-spec.v1", artifact_type="processing-spec", purpose="단계 실행용 구조화 설정 작성",
        required_context_ids=["template.processing-spec.contract", "validation.processing-spec"],
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
