#!/usr/bin/env python3
import argparse
import os

from workflow_service.client import ServiceClient
from workflow_service.http_server import serve
from workflow_service.service import WorkflowService


parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8060)
parser.add_argument("--workflow-decision", "--laya", dest="workflow_decision", default=os.environ.get("WORKFLOW_DECISION_URL", "http://127.0.0.1:8040"))
parser.add_argument("--catalog", default="http://127.0.0.1:8031")
parser.add_argument("--execution", default="http://127.0.0.1:8032")
parser.add_argument("--files", default="http://127.0.0.1:8041")
parser.add_argument("--video-alpha", default="http://127.0.0.1:8050")
parser.add_argument("--data", default="http://127.0.0.1:8030")
args = parser.parse_args()

app = WorkflowService(
    workflow_decision=ServiceClient("workflow-decision", args.workflow_decision), catalog=ServiceClient("catalog", args.catalog),
    execution=ServiceClient("execution", args.execution), files=ServiceClient("files", args.files),
    video_alpha=ServiceClient("video-alpha", args.video_alpha),
    data=ServiceClient("data", args.data),
)
serve(app, args.host, args.port)
