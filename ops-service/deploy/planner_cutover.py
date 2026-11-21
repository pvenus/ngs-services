"""Planner selector identity and readiness. Never opens SQLite or sends work."""
from datetime import datetime
from pathlib import Path
import re
import stat
import time

LABEL = 'com.vinus.workflow.planner'
HEARTBEAT = 'workflow-planner'
REVISION = 'NGS_WORKFLOW_PLANNER_REVISION'
ENTRY = 'workflow-planner/cli.mjs'
SHA = re.compile(r'[a-f0-9]{40}')


def planner_identity(selector, home):
    """Validate preserved startup inputs; capture only DB stat identity."""
    args = selector['ProgramArguments']
    database = home / 'Workspaces/Workers/data/workflow-planner/workflow.sqlite'
    source = Path(args[1])
    env = selector.get('EnvironmentVariables', {})
    if (len(args) != 4 or args[2:] != ['--db', str(database)]
            or selector.get('WorkingDirectory') != str(source.parent)
            or not isinstance(env, dict) or not Path(args[0]).is_absolute()
            or source.resolve(strict=True) != source or not source.is_file()
            or database.parent.resolve(strict=True) != database.parent):
        raise ValueError('TARGET_PLANNER_SELECTOR')
    st = database.lstat()
    if (not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600
            or st.st_uid != database.parent.stat().st_uid or st.st_nlink != 1):
        raise ValueError('TARGET_PLANNER_DATABASE_IDENTITY')
    prior_sha = env.get(REVISION)
    if REVISION in env and (not isinstance(prior_sha, str) or not SHA.fullmatch(prior_sha)
            or not source.parent.parent.name.endswith('-' + prior_sha)):
        raise ValueError('TARGET_PLANNER_PRIOR_REVISION')
    return tuple(args), str(source.parent), (st.st_dev, st.st_ino), prior_sha


def loaded_selector(report, arguments, working_directory):
    """Match loaded launchctl program arguments and cwd, not the plist alone."""
    lines = report.splitlines()
    try:
        start = lines.index('\targuments = {') + 1
        end = lines.index('\t}', start)
    except ValueError:
        return False
    return (lines[start:end] == ['\t\t' + arg for arg in arguments]
            and lines.count('\tworking directory = ' + working_directory) == 1)


def fresh_heartbeat(rows, expected_sha, since):
    """Unversioned/mismatched or stale observations are never candidate proof."""
    matches = [r for r in rows if r.get('serverName') == HEARTBEAT]
    if len(matches) != 1:
        return False
    row = matches[0]
    try:
        return (row.get('state') == 'connected' and row.get('commitSha') == expected_sha
                and datetime.fromisoformat(row['lastReceivedAt']) >= since)
    except (KeyError, TypeError, ValueError):
        return False


def wait_planner(*, live, status, expected, since, previous_pid, database_identity,
                 clock=time.monotonic, sleep=time.sleep):
    """Observe exact live selector, unchanged DB, fresh expected revision and stable new PID.

    Legacy rollback (expected SHA absent) verifies restored process identity and
    same DB via two samples; it never claims versioned candidate health.
    """
    args, wd, expected_db, sha = expected
    deadline = clock() + (95 if sha is not None else 10)
    stable = None
    while clock() < deadline:
        try:
            pid, report = live()
            if (pid and pid != previous_pid and loaded_selector(report, args, wd)
                    and database_identity() == expected_db):
                if stable == pid and (sha is None or fresh_heartbeat(status(), sha, since)):
                    if live()[0] == pid:
                        return
                stable = pid
            else:
                stable = None
        except (OSError, KeyError, TypeError, ValueError):
            stable = None
        sleep(1)
    raise ValueError('TARGET_PLANNER_HEALTH_TIMEOUT')
