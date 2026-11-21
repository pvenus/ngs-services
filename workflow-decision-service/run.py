import argparse
from wsgiref.simple_server import make_server
from workflow_decision_service.service import WorkflowDecisionService
from workflow_decision_service.http_api import WorkflowDecisionApi
from workflow_decision_service.feature_selection import FeatureSelectionService
from workflow_decision_service.catalog import CatalogClient
from workflow_decision_service.canonical_scene_source import CanonicalSceneSource

parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "::1"])
parser.add_argument("--port", type=int, default=8040)
parser.add_argument("--registry-path")
parser.add_argument("--catalog-url", default="http://127.0.0.1:8031")
parser.add_argument("--data-service-url", default="http://127.0.0.1:8030")
parser.add_argument("--one-stop-direction-streaming", action="store_true",
                    help="select OneStop plan version 4 for new selections only")
parser.add_argument("--one-stop-direction-target", help="limit the opt-in plan to one exact pilot NPC ID")
args = parser.parse_args()
app = WorkflowDecisionApi(WorkflowDecisionService(args.registry_path, feature_selector=FeatureSelectionService(CatalogClient(args.catalog_url), scene_source=CanonicalSceneSource(args.data_service_url)),
    one_stop_direction_streaming=args.one_stop_direction_streaming,
    one_stop_direction_target=args.one_stop_direction_target), port=args.port)
with make_server(args.host, args.port, app) as server:
    print(f"workflow-decision-service listening on http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
