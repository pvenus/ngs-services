from __future__ import annotations

import argparse
from typing import Optional, Sequence
from wsgiref.simple_server import make_server

from catalog_service.bootstrap import load_minimum_catalog
from catalog_service.http_api import CatalogApi
from catalog_service.repository import HttpCatalogRepository
from catalog_service.service import CatalogService


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8031


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Project Catalog Service HTTP API")
    parser.add_argument("--host", default=DEFAULT_HOST, help="listen host (default: %(default)s)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="listen port (default: %(default)s)")
    parser.add_argument("--data-service-url", required=True, help="base URL of the shared Project Data Service")
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="explicitly write the minimum catalog before serving; disabled by default",
    )
    return parser


def create_app(data_service_url: str, *, bootstrap: bool = False) -> CatalogApi:
    repository = HttpCatalogRepository(data_service_url)
    service = CatalogService(repository)
    if bootstrap:
        load_minimum_catalog(service)
    return CatalogApi(service)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        build_parser().error("--port must be between 1 and 65535")
    app = create_app(args.data_service_url, bootstrap=args.bootstrap)
    with make_server(args.host, args.port, app) as server:
        print(
            "Project Catalog Service listening on http://{}:{} (Data Service: {})".format(
                args.host, args.port, args.data_service_url
            ),
            flush=True,
        )
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
