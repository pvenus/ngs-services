"""Prevent Filex I/O from extending a Workflow SQLite snapshot."""
from contextvars import ContextVar

workflow_snapshot_open = ContextVar('workflow_snapshot_open', default=False)

def require_closed_snapshot():
    if workflow_snapshot_open.get():
        raise ValueError('FILEX_CALL_WITH_WORKFLOW_SNAPSHOT')
