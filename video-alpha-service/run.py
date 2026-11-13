import argparse
import threading
from http.server import ThreadingHTTPServer

from video_alpha.adapters import DataJobRepository, FileServiceClient, VideoAlphaProcessor
from video_alpha.http_server import Handler
from video_alpha.service import VideoAlphaService

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--data-service-url", required=True); parser.add_argument("--file-service-url", required=True)
    args = parser.parse_args(); server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.active_jobs = set(); server.active_lock = threading.Lock()
    server.app = VideoAlphaService(DataJobRepository(args.data_service_url), FileServiceClient(args.file_service_url), VideoAlphaProcessor())
    print("video-alpha listening on http://%s:%d" % (args.host, args.port), flush=True); server.serve_forever()

if __name__ == "__main__": main()
