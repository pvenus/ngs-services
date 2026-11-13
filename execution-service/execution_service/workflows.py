from __future__ import annotations

from copy import deepcopy


VIDEO_ALPHA_WORKFLOW = {
    "type": "video-alpha.v1",
    "steps": [
        {
            "id": "get-job",
            "phase": "get",
            "operation": "video_job_get",
            "permission": "read_only",
            "requiredInputs": ["jobId"],
        },
        {
            "id": "compose-processing-spec",
            "phase": "compose",
            "templateId": "codex.processing-spec.v1",
            "artifactType": "processing-spec",
            "requiredInputs": ["sourceAssetId", "alphaMethod", "outputFormat"],
            "submissionRequired": ["sourceAssetId", "alphaMethod", "outputFormat"],
        },
        {
            "id": "prepare-execution",
            "phase": "execute",
            "operation": "video-alpha.apply",
            "permission": "controlled_write",
            "requiredArtifacts": ["processing-spec"],
        },
        {
            "id": "validate-result",
            "phase": "validate",
            "operation": "video-alpha.validate",
            "requiredInputs": ["resultAssetId"],
        },
    ],
}


NODE_IMAGE_WORKFLOW = {'type':'story-node-image.v1','steps':[{'id':'native-image-handoff','phase':'native','operation':'image_gen.imagegen','permission':'controlled_write','requiredInputs':['projectId','prompt','nodeImage']},{'id':'validate-node-image','phase':'validate','operation':'node-image.validate','requiredInputs':['resultAssetId'] }]}

def workflow_definition(workflow_type: str) -> dict:
    if workflow_type == 'episode-visual-asset.v1':return {'type':workflow_type,'steps':[{'id':'worker-generation','phase':'worker','operation':'episode-visual.worker','requiredInputs':['projectId','visualReceipt']}]}
    if workflow_type == NODE_IMAGE_WORKFLOW['type']:return deepcopy(NODE_IMAGE_WORKFLOW)
    if workflow_type != VIDEO_ALPHA_WORKFLOW["type"]:
        raise KeyError(workflow_type)
    return deepcopy(VIDEO_ALPHA_WORKFLOW)
