import argparse
from wsgiref.simple_server import make_server

from laya_service.catalog import CatalogClient
from laya_service.http_api import LayaApi
from laya_service.service import LayaSelectionService
from laya_service.canonical_scene_source import CanonicalSceneSource


parser = argparse.ArgumentParser()
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8040)
parser.add_argument("--catalog-url", default="http://127.0.0.1:8031")
parser.add_argument("--data-service-url", default="http://127.0.0.1:8030")
args = parser.parse_args()
app = LayaApi(LayaSelectionService(CatalogClient(args.catalog_url), scene_source=CanonicalSceneSource(args.data_service_url)))
with make_server(args.host, args.port, app) as server:
    print(f"project-laya-service listening on http://{args.host}:{args.port}")
    server.serve_forever()
