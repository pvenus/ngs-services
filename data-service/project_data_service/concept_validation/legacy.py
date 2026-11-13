import json
from pathlib import Path
from .concept_groups import validate_hierarchy
SEED=Path(__file__).parent/"seed.json"
ALLOWED_STATUS={"source","proposal","open","confirmed"}
def validate_summary(value):
    if isinstance(value, str):
        return 1 <= len(value.strip()) <= 600
    return (isinstance(value, dict) and set(value) == {"schemaVersion", "keywords", "sentence"}
            and value["schemaVersion"] == "npc-concept-summary.v1"
            and isinstance(value["keywords"], list) and 1 <= len(value["keywords"]) <= 12
            and all(isinstance(x, str) and x == x.strip() and 1 <= len(x) <= 100 for x in value["keywords"])
            and len(set(value["keywords"])) == len(value["keywords"])
            and isinstance(value["sentence"], str) and value["sentence"] == value["sentence"].strip()
            and 1 <= len(value["sentence"]) <= 600)


def validate_legacy(document: object) -> dict:
    if not isinstance(document, dict) or document.get("schemaVersion") != 1 or document.get("projectId") != "ProjectBS":
        raise ValueError("지원하지 않는 문서 형식입니다.")
    if not isinstance(document.get("title"), str) or not document["title"].strip() or len(document["title"]) > 120:
        raise ValueError("제목을 확인하세요.")
    sections = document.get("sections")
    if not isinstance(sections, list) or len(sections) != 8:
        raise ValueError("필수 영역 8개가 필요합니다.")
    section_priorities = [section.get("priority") for section in sections if isinstance(section, dict)]
    if len(section_priorities) != 8 or any(type(p) is not int for p in section_priorities) or sorted(section_priorities) != list(range(1, 9)):
        raise ValueError("영역 우선순위는 1부터 8까지 중복 없이 지정하세요.")
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    for section, expected in zip(sections, seed["sections"]):
        if not isinstance(section, dict) or section.get("id") != expected["id"]:
            raise ValueError("영역 ID 또는 순서를 변경할 수 없습니다.")
        fields = section.get("fields")
        if not isinstance(fields, list) or len(fields) != len(expected["fields"]):
            raise ValueError("필수 항목을 삭제할 수 없습니다.")
        field_priorities = [field.get("priority") for field in fields if isinstance(field, dict)]
        if len(field_priorities) != len(fields) or any(type(p) is not int for p in field_priorities) or sorted(field_priorities) != list(range(1, len(fields) + 1)):
            raise ValueError("항목 우선순위는 영역 안에서 중복 없이 지정하세요.")
        for field, original in zip(fields, expected["fields"]):
            if not isinstance(field, dict) or field.get("id") != original["id"]:
                raise ValueError("항목 ID 또는 순서를 변경할 수 없습니다.")
            if not isinstance(field.get("value"), str) or len(field["value"]) > 12000:
                raise ValueError("항목 내용은 12,000자 이내여야 합니다.")
            if field.get("status") not in ALLOWED_STATUS:
                raise ValueError("항목 상태가 올바르지 않습니다.")
            sources = field.get("sources")
            if not isinstance(sources, list) or len(sources) > 20 or any(not isinstance(s, str) or len(s) > 300 for s in sources):
                raise ValueError("출처 목록을 확인하세요.")
            if not isinstance(field.get("label"), str) or not field["label"].strip() or len(field["label"]) > 100:
                raise ValueError("항목 이름을 확인하세요.")
        for key in ("title", "description"):
            if not isinstance(section.get(key), str) or len(section[key]) > 200:
                raise ValueError("영역 설명을 확인하세요.")
    for section in sections:
        for item in [section] + section["fields"]:
            if "summary" in item and not validate_summary(item["summary"]):
                raise ValueError("요약 데이터 형식을 확인하세요.")
    if 'hierarchy' in document:
        from .concept_groups import validate_hierarchy
        validate_hierarchy(document['hierarchy'], [section['id'] for section in sections])
    return document


