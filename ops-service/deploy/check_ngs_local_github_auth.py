"""Read-only Mac diagnostic for the exact GitHub Actions artifact authorization."""
import json
import sys
import tarfile
import zipfile

from github_actions_keychain import KeychainGitHubEvidence
from prepare_ngs_local_once import EXPECTED_WAIT
from prepare_ngs_release import need, verified_main_candidate


def check(github):
    # Avoid implying artifact access from main metadata alone or from old readiness.
    return {"target": "ngs-local", "state": "artifact_read_verified",
            "sha": verified_main_candidate(github)["head"]}


if __name__ == "__main__":
    try:
        need(len(sys.argv) == 1, "NO_NGS_PREP_ARGUMENTS")
        print(json.dumps(check(KeychainGitHubEvidence()), separators=(",", ":")), flush=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, tarfile.TarError, zipfile.BadZipFile, RecursionError) as error:
        code = str(error)
        print(json.dumps({"target": "ngs-local", "state": "artifact_read_unverified",
                          "code": code if code in EXPECTED_WAIT else "NGS_LOCAL_PREP_REFUSED"},
                         separators=(",", ":")), flush=True)
        sys.exit(1)
