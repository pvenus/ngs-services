import argparse
import os
from pathlib import Path

from file_service.data_client import HttpDataClient
from file_service.http_server import serve
from file_service.service import FileService


parser = argparse.ArgumentParser()
parser.add_argument("--storage-root", type=Path, required=True)
parser.add_argument("--data-service", default="http://127.0.0.1:8030")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8041)
args = parser.parse_args()
secret = os.environ.get("PROJECTBS_FILE_TOKEN_SECRET", "").encode()
if len(secret) < 32: raise SystemExit("PROJECTBS_FILE_TOKEN_SECRET must contain at least 32 bytes")
serve(FileService(args.storage_root, HttpDataClient(args.data_service), secret), args.host, args.port)
