#!/usr/bin/env python3
"""Publish an owner-supplied PCM WAV through the protected local PDS."""
import argparse
import base64
import hashlib
import json
import os
import re
import stat
import urllib.error
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirect refused')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav', type=Path)
    parser.add_argument('--title', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8030)
    parser.add_argument('--source-id')
    parser.add_argument('--source-sha256')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or bool(args.source_id) != bool(args.source_sha256):
        parser.error('Valid local port and either both or neither source arguments are required')
    try:
        with os.fdopen(os.open(args.token_file, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as secret:
            st = os.fstat(secret.fileno())
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise ValueError('Token file must be a private owner file')
            token = secret.read(129).decode('ascii').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token):
            raise ValueError('Token file is invalid')
        limit = 24 * 1024 * 1024
        with args.wav.open('rb') as file:
            raw = file.read(limit + 1)
        if not 44 <= len(raw) <= limit:
            raise ValueError('WAV size is outside the accepted range')
        sha = hashlib.sha256(raw).hexdigest()
        source = None
        if args.source_id:
            source = {'sourceOfficialRequestId':args.source_id, 'sourceSha256':args.source_sha256}
        body = dict(schemaVersion='ngs.audio-import.v1', projectId='binari', title=args.title, source=source,
                    artifact=dict(mimeType='audio/wav', bytes=len(raw), sha256=sha,
                                  base64=base64.b64encode(raw).decode('ascii')))
        request = urllib.request.Request(f'http://127.0.0.1:{args.port}/v1/audio/manual-import',
            data=json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode(),
            headers={'Content-Type':'application/json', 'Authorization':'Bearer '+token}, method='POST')
        with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request,timeout=90) as response:
            receipt = json.loads(response.read(4096))
        if receipt.get('ok') is not True or receipt.get('sha256') != sha:
            raise ValueError('Publication could not be verified')
        print(json.dumps(receipt, ensure_ascii=False))
    except urllib.error.HTTPError as error:
        parser.exit(1, 'PDS rejected manual import (HTTP %d).\n' % error.code)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Manual import not confirmed: {error}\n')


if __name__ == '__main__':
    main()
