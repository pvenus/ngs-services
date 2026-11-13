import argparse
from http.server import ThreadingHTTPServer

from execution_service.http_server import Handler
from execution_service.repository import HttpWorkflowRepository
from execution_service.service import ExecutionService


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8032)
    parser.add_argument("--data-service", default="http://127.0.0.1:8030")
    args = parser.parse_args()
    Handler.service = ExecutionService(HttpWorkflowRepository(args.data_service))
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"project-execution-service listening on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
