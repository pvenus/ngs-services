import io
import json
import subprocess
import unittest
from email.message import Message
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import github_actions_keychain as gh
from prepare_ngs_release import Refused

PAT = "github_pat_"+"SYNTHETIC_"+"X"*40
ROUTE = gh.PREFIX+"actions/artifacts/42/zip"
LOCATION = "https://productionresultssa12.blob.core.windows.net/artifacts/exact.zip?sig=fake"


class Response(io.BytesIO):
    def __init__(self, status, body=b"", headers=()):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in headers:
            self.headers[name] = value

    def getcode(self):
        return self.status


class Tests(unittest.TestCase):
    def make(self, *response):
        opener, lookup = Mock(), Mock(return_value=PAT)
        opener.open.side_effect = list(response)
        return gh.KeychainGitHubEvidence(token=lookup, opener=opener), opener, lookup

    def test_exact_api_only_and_blob_get_has_no_github_authorization_or_referer(self):
        headers = Message()
        headers["Location"] = LOCATION
        redirect = HTTPError("https://api.github.com/"+ROUTE,302,"Found",headers,io.BytesIO(b"DO NOT READ"))
        evidence, opener, lookup = self.make(redirect, Response(200,b"exact zip"))
        self.assertEqual(evidence.read(ROUTE, limit=9),b"exact zip")
        a,b = (call.args[0] for call in opener.open.call_args_list)
        self.assertEqual((a.full_url,a.method),("https://api.github.com/"+ROUTE,"GET"))
        self.assertEqual(a.get_header("Authorization"),"Bearer "+PAT)
        self.assertEqual((b.full_url,b.method),(LOCATION,"GET"))
        self.assertEqual({k.lower() for k in b.headers},{"accept","user-agent"})
        lookup.assert_called_once_with()

    def test_public_ref_and_bad_routes_never_retrieve_secret(self):
        evidence, opener, lookup = self.make(Response(200,b'{"ref":"refs/heads/main"}'))
        self.assertEqual(evidence.metadata(gh.MAIN_ROUTE),{"ref":"refs/heads/main"})
        self.assertNotIn("Authorization",opener.open.call_args.args[0].headers)
        attempts=["repos/attacker/other/actions/artifacts/42/zip", gh.PREFIX+"actions/artifacts/42/zip?extra=1",
                  gh.PREFIX+"actions/artifacts/042/zip", gh.PREFIX+"actions/artifacts/42/%7Aip",
                  gh.PREFIX+"actions/runs/42/artifacts?name=other&per_page=2",
                  gh.MAIN_ROUTE+"\r\nAuthorization: broken"]
        for route in attempts:
            with self.assertRaisesRegex(Refused,"GITHUB_ROUTE_DENIED"): evidence.read(route)
        lookup.assert_not_called()
        self.assertEqual(opener.open.call_count,1)

    def test_reject_untrusted_redirect_shapes_before_second_request(self):
        for target in ("http://productionresultssa12.blob.core.windows.net/path?sig=fake",
                       "https://evil.blob.core.windows.net/path?sig=x",
                       "https://productionresultssa12.blob.core.windows.net.evil.example/path?sig=x",
                       "https://productionresultssa12.blob.core.windows.net@evil.example/path?sig=x",
                       "https://user@productionresultssa12.blob.core.windows.net/path?sig=x",
                       "https://productionresultssa12.blob.core.windows.net:443/path?sig=x",
                       "https://127.0.0.1/path?sig=x", "//productionresultssa12.blob.core.windows.net/path?sig=x",
                       LOCATION+"%0a",LOCATION+"#local",LOCATION.replace("https://","https://")+".example#",
                       "https://results-receiver.actions.githubusercontent.com/path"):
            evidence, opener, _ = self.make(Response(302,headers=[("Location",target)]))
            with self.subTest(target=target),self.assertRaisesRegex(Refused,"GITHUB_ARTIFACT_REDIRECT_DENIED"):
                evidence.read(ROUTE)
            self.assertEqual(opener.open.call_count,1)

    def test_real_redirect_handler_refuses_followup_and_duplicate_headers(self):
        req = Mock()
        self.assertIsNone(gh.NoRedirect().redirect_request(req,None,302,"found",{},LOCATION))
        evidence, opener, _ = self.make(Response(302,headers=[("Location",LOCATION),("Location",LOCATION)]))
        with self.assertRaisesRegex(Refused,"GITHUB_RESPONSE_HEADER_INVALID"): evidence.read(ROUTE)
        self.assertEqual(opener.open.call_count,1)
        evidence, opener, _ = self.make(Response(302,headers=[("Location",LOCATION)]),
                                        Response(302,headers=[("Location","https://evil.example")]))
        with self.assertRaisesRegex(Refused,"GITHUB_HTTP_RESPONSE_INVALID"): evidence.read(ROUTE)
        self.assertEqual(opener.open.call_count,2)

    def test_http_auth_and_limit_errors_have_no_remote_body_url_or_token(self):
        for reply,code in [(Response(403,b"DO NOT SHOW "+PAT.encode()),"GITHUB_ACTIONS_READ_DENIED"),
                           (Response(410,b"expired"),"GITHUB_ARTIFACT_EXPIRED"),
                           (URLError(LOCATION),"GITHUB_TRANSPORT_UNAVAILABLE")]:
            before,_,_=self.make(reply)
            with self.assertRaisesRegex(Refused,"^"+code+"$"): before.read(ROUTE)
        evidence,_,_=self.make(Response(302,headers=[("Location",LOCATION)]),Response(200,b"TOO LARGE"))
        with self.assertRaisesRegex(Refused,"GITHUB_RESPONSE_TOO_LARGE"): evidence.read(ROUTE,limit=3)

    def test_fixed_keychain_lookup_and_only_known_auth_status(self):
        with patch.object(gh.subprocess,"run",return_value=subprocess.CompletedProcess([],0,(PAT+"\n").encode(),b"")) as run:
            self.assertEqual(gh.keychain_token(),PAT)
        argv=run.call_args.args[0]
        self.assertEqual(argv[:7],["/usr/bin/security","find-generic-password","-a","pvenus/ngs-platform",
                                   "-s","com.vinus.ops.ngs-local.github-actions-read","-w"])
        self.assertTrue(argv[7].endswith("/Library/Keychains/login.keychain-db"))
        self.assertNotIn(PAT,str(run.call_args))
        self.assertNotIn("GH_TOKEN",run.call_args.kwargs["env"])
        self.assertIs(run.call_args.kwargs["stdin"],subprocess.DEVNULL)
        cases=[(1,b"The specified item could not be found in the keychain.",b"", "GITHUB_KEYCHAIN_ITEM_MISSING"),
               (36,b"additional private text",b"", "GITHUB_KEYCHAIN_LOCKED_OR_DENIED"),
               (2,b"leak not allowed",b"", "GITHUB_KEYCHAIN_UNAVAILABLE"),
               (0,b"",b"ghp_not_fine_grained", "GITHUB_KEYCHAIN_ITEM_INVALID")]
        for rc,err,out,code in cases:
            with patch.object(gh.subprocess,"run",return_value=subprocess.CompletedProcess([],rc,out,err)):
                with self.assertRaisesRegex(Refused,"^"+code+"$"): gh.keychain_token()
        with patch.object(gh.subprocess,"run",side_effect=subprocess.TimeoutExpired(["private"],8,output=PAT)):
            with self.assertRaisesRegex(Refused,"^GITHUB_KEYCHAIN_LOCKED_OR_DENIED$"): gh.keychain_token()

    def test_only_provisioned_loopback_proxy_is_accepted(self):
        self.assertEqual(gh.loopback_https_proxy({"https_proxy":"https://[::1]:9000"}),{"https":"https://[::1]:9000"})
        for proxy in ("http://someone@127.0.0.1:9","http://127.0.0.1:1/path","http://127.0.0.1:9?q=1",
                      "http://127.0.0.1:99999","http://external.example:22"):
            self.assertEqual(gh.loopback_https_proxy({"https_proxy":proxy}),{})
