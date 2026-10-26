"""Tamper-evident audit log: JSONL, each record hash-chained to the previous one."""
import hashlib
import json
import threading
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path, run_id, echo=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")
        self.run_id = run_id
        self.echo = echo
        self._prev = "0" * 64
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, event, **data):
        with self._lock:
            self._seq += 1
            rec = {"seq": self._seq, "ts": round(time.time(), 4), "run": self.run_id, "event": event, "data": data}
            body = json.dumps(rec, sort_keys=True, default=str)
            rec["prev"] = self._prev
            rec["hash"] = hashlib.sha256((self._prev + body).encode()).hexdigest()
            self._prev = rec["hash"]
            with self.path.open("a") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        if self.echo:
            self.echo(rec)
        return rec

    @staticmethod
    def verify(path):
        """Return (ok, message). Detects edits, deletions and re-ordering."""
        prev = "0" * 64
        for n, line in enumerate(Path(path).read_text().splitlines(), 1):
            rec = json.loads(line)
            claimed, rec_prev = rec.pop("hash"), rec.pop("prev")
            body = json.dumps(rec, sort_keys=True, default=str)
            if rec_prev != prev or hashlib.sha256((prev + body).encode()).hexdigest() != claimed:
                return False, f"chain broken at record {n}"
            prev = claimed
        return True, "chain intact"

    def events(self):
        return [json.loads(l) for l in self.path.read_text().splitlines()]
