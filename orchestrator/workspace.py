"""Transactional workspace: every commit is snapshotted so it can be rolled back exactly."""
import threading
from pathlib import Path


class WorkspaceError(Exception):
    pass


class Workspace:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._journal: dict[str, list[tuple[str, str | None]]] = {}
        self._lock = threading.Lock()

    def resolve(self, rel):
        p = (self.root / rel).resolve()
        if self.root != p and self.root not in p.parents:
            raise WorkspaceError(f"path escapes workspace: {rel}")
        return p

    def read(self, rel, default=None):
        p = self.resolve(rel)
        return p.read_text() if p.exists() else default

    def files(self, suffix=""):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts and ".pytest_cache" not in p.parts
                      and p.name.endswith(suffix))

    def seed(self, files: dict[str, str]):
        """Initial (baseline) content; not journaled, i.e. not subject to rollback."""
        for rel, text in files.items():
            p = self.resolve(rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def commit(self, task_id, files: dict[str, str]):
        with self._lock:
            journal = self._journal.setdefault(task_id, [])
            for rel in files:
                self.resolve(rel)  # validate everything before touching disk
            for rel, text in files.items():
                p = self.resolve(rel)
                journal.append((rel, p.read_text() if p.exists() else None))
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
        return sorted(files)

    def rollback(self, task_id):
        with self._lock:
            entries = self._journal.pop(task_id, [])
            for rel, prev in reversed(entries):
                p = self.resolve(rel)
                if prev is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_text(prev)
        return sorted({rel for rel, _ in entries})
