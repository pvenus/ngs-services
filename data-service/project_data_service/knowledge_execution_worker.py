"""Tool-free process worker: JSON stdin snapshot -> complete JSON stdout.
No Filex/PDS client or journal access. The caller owns publication and retirement.
"""
import json, sys
from pathlib import Path
from .knowledge_execution import SCHEMA, build_output
from .knowledge_authoring import CodexKnowledgeAuthor
from .knowledge_requests import canonical

def main():
    try:
        value = json.load(sys.stdin)
        if set(value) != {'schemaVersion','request','snapshot'} or value['schemaVersion'] != SCHEMA:
            raise ValueError('KNOWLEDGE_EXECUTION_INPUT_INVALID')
        result = build_output(value['request'], value['snapshot'], CodexKnowledgeAuthor(Path.cwd()/'authoring'))
        sys.stdout.write(canonical(result)); return 0
    except Exception as error:
        sys.stdout.write(canonical({'error':str(error)[:2000]})); return 1

if __name__ == '__main__': raise SystemExit(main())
