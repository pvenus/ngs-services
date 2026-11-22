"""Fixed read-only GitHub Actions transport for the Mac candidate preparer."""
import http.client
import os
from pathlib import Path
import re
import ssl
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from prepare_ngs_release import OUTER_LIMIT, REPO, Refused, json_load, need

KEYCHAIN_SERVICE = "com.vinus.ops.ngs-local.github-actions-read"
KEYCHAIN_ACCOUNT = "pvenus/ngs-platform"
API = "api.github.com"
PREFIX = "repos/" + REPO + "/"
MAIN_ROUTE = PREFIX + "git/ref/heads/main"
ARTIFACT_ORIGIN = re.compile(r"results-receiver\.actions\.githubusercontent\.com|productionresultssa[0-9]{1,3}\.blob\.core\.windows\.net")
FINE_GRAINED = re.compile(rb"(github_pat_[A-Za-z0-9_]{30,240})\n?")
ID = r"[1-9][0-9]{0,19}"
SHA = r"[a-f0-9]{40}"
ACTIONS = re.compile(
    rf"actions/(?:workflows/(?:prepare-ngs-release\.yml|{ID}/runs\?branch=main&event=push&head_sha={SHA}&per_page=1)"
    rf"|runs/{ID}/artifacts\?name=ngs-local-source-{SHA}-{ID}&per_page=2|artifacts/{ID}(?:/zip)?)"
)
DOWNLOAD = re.compile(rf"actions/artifacts/{ID}/zip")


def keychain_token():
    """Read only the fixed login Keychain item, without an interactive prompt in this process."""
    home = Path.home()
    keychain = home / "Library/Keychains/login.keychain-db"
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-a", KEYCHAIN_ACCOUNT, "-s", KEYCHAIN_SERVICE,
             "-w", str(keychain)],
            env={"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C"},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False, timeout=8,
        )
    except subprocess.TimeoutExpired:
        raise Refused("GITHUB_KEYCHAIN_LOCKED_OR_DENIED") from None
    except (OSError, subprocess.SubprocessError):
        raise Refused("GITHUB_KEYCHAIN_UNAVAILABLE") from None
    if result.returncode:
        # Old security tool builds return a generic exit value. Classify only
        # known fixed POSIX/OSStatus forms or the exact English error suffix.
        detail = result.stderr[:1024].strip().lower()
        if result.returncode in (-25300, (-25300) % 256) or detail.endswith(b"the specified item could not be found in the keychain."):
            raise Refused("GITHUB_KEYCHAIN_ITEM_MISSING")
        if (result.returncode in (-25308, (-25308) % 256, -25293, (-25293) % 256) or
                any(detail.endswith(message) for message in (
                    b"user interaction is not allowed.", b"user interaction is required.",
                    b"authorization/authentication failed."))):
            raise Refused("GITHUB_KEYCHAIN_LOCKED_OR_DENIED")
        raise Refused("GITHUB_KEYCHAIN_UNAVAILABLE")
    match = FINE_GRAINED.fullmatch(result.stdout) if result.returncode == 0 else None
    need(match, "GITHUB_KEYCHAIN_ITEM_INVALID")
    return match[1].decode("ascii")


def loopback_https_proxy(env):
    for name in ("https_proxy", "HTTPS_PROXY"):
        value = env.get(name, "")
        match = re.fullmatch(r"https?://(?:127\.0\.0\.1|\[::1\]):([0-9]{1,5})", value)
        if match and 1 <= int(match[1]) <= 65535:
            return {"https": value}
    return {}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def artifact_location(value):
    need(type(value) is str and 0 < len(value) <= 8192 and value.isascii()
         and not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value)
         and "\\" not in value and "#" not in value and not re.search(r"%0[ad]", value, re.I),
         "GITHUB_ARTIFACT_REDIRECT_DENIED")
    try:
        u = urlsplit(value)
        trusted = u.scheme == "https" and u.netloc == u.hostname and bool(ARTIFACT_ORIGIN.fullmatch(u.hostname or ""))
    except ValueError:
        trusted = False
    need(trusted and u.path.startswith("/") and not u.path.startswith("//") and bool(u.query), "GITHUB_ARTIFACT_REDIRECT_DENIED")
    return value


def one_header(headers, name):
    values = headers.get_all(name, [])
    need(len(values) <= 1, "GITHUB_RESPONSE_HEADER_INVALID")
    return values[0] if values else None


class KeychainGitHubEvidence:
    """Exact read-only routes; manual artifact redirect sends only noncredential headers."""

    def __init__(self, *, token=keychain_token, opener=None):
        self._get_token = token
        self._pat = None
        if opener is None:
            # Explicit client context avoids TLS key logging from SSLKEYLOGFILE.
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_default_certs()
            opener = build_opener(ProxyHandler(loopback_https_proxy(os.environ)), HTTPSHandler(context=context), NoRedirect())
        self._opener = opener

    def _request(self, url, *, headers, status, limit, timeout):
        req = Request(url, method="GET", headers=headers)
        try:
            try:
                response = self._opener.open(req, timeout=timeout)
            except HTTPError as error:
                response = error
            with response:
                got = response.getcode()
                if got != status:
                    if got in (401, 403) and "Authorization" in headers:
                        raise Refused("GITHUB_ACTIONS_READ_DENIED")
                    if got == 410 and status == 302:
                        raise Refused("GITHUB_ARTIFACT_EXPIRED")
                    raise Refused("GITHUB_HTTP_RESPONSE_INVALID")
                if status == 302:
                    return one_header(response.headers, "Location")
                length = one_header(response.headers, "Content-Length")
                need(length is None or (re.fullmatch(r"[0-9]{1,12}", length) and int(length) <= limit),
                     "GITHUB_RESPONSE_TOO_LARGE")
                data = response.read(limit + 1)
                need(len(data) <= limit, "GITHUB_RESPONSE_TOO_LARGE")
                return data
        except (OSError, URLError, http.client.HTTPException, ValueError) as error:
            if type(error) is Refused:
                raise
            # Do not include upstream bodies, the signed URL or Request repr.
            raise Refused("GITHUB_TRANSPORT_UNAVAILABLE") from None

    def read(self, route, limit=1024 * 1024):
        need(type(route) is str and route.startswith(PREFIX) and type(limit) is int and 0 < limit <= OUTER_LIMIT,
             "GITHUB_ROUTE_DENIED")
        path = route[len(PREFIX):]
        main = route == MAIN_ROUTE
        need(main or ACTIONS.fullmatch(path), "GITHUB_ROUTE_DENIED")
        request_headers = {"Accept": "application/vnd.github+json", "User-Agent": "ngs-ops-local-candidate",
                           "X-GitHub-Api-Version": "2022-11-28"}
        if not main:
            if self._pat is None:
                token = self._get_token()
                need(isinstance(token, str) and token.isascii() and FINE_GRAINED.fullmatch(token.encode()) and "\n" not in token,
                     "GITHUB_KEYCHAIN_ITEM_INVALID")
                self._pat = token
            request_headers["Authorization"] = "Bearer " + self._pat
        artifact = DOWNLOAD.fullmatch(path)
        response = self._request("https://" + API + "/" + route, headers=request_headers,
                                 status=302 if artifact else 200, limit=limit, timeout=12)
        if artifact:
            # The GitHub API supplies a short-lived signed URL. Do not forward
            # Authorization, cookies, API version or referrer, or follow a second hop.
            return self._request(artifact_location(response), headers={"Accept": "application/octet-stream",
                                 "User-Agent": "ngs-ops-local-candidate"}, status=200, limit=limit, timeout=30)
        return response

    def metadata(self, route):
        try:
            return json_load(self.read(route))
        except (UnicodeError, ValueError, RecursionError) as error:
            if type(error) is Refused:
                raise
            raise Refused("GITHUB_METADATA_INVALID") from None
