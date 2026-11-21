"""Bounded model-free recovery loop for committed knowledge scratch only."""
import threading
from pathlib import Path

class KnowledgeCleanupRuntime:
    def __init__(self, journal_path, interval=30):
        self.path=Path(journal_path); self.interval=interval
        self.stopped=threading.Event(); self.thread=None; self.last_error=None
    def tick(self):
        if not self.path.exists():return []
        from .knowledge_requests import RequestJournal,KnowledgeRequests
        journal=RequestJournal(self.path)
        try:
            result=KnowledgeRequests(journal).retry_cleanup(limit=8)
            self.last_error=None
            return result
        finally:journal.close()
    def status(self):
        return {"running":bool(self.thread and self.thread.is_alive()),"intervalSeconds":self.interval,"lastError":self.last_error}
    def start(self):
        def loop():
            while not self.stopped.wait(self.interval):
                try:self.tick()
                except Exception as error:self.last_error=str(error)[:2000]
        self.thread=threading.Thread(target=loop,name='pds-knowledge-cleanup',daemon=True)
        self.thread.start()
    def close(self):
        self.stopped.set()
        if self.thread:self.thread.join(timeout=5)
