"""Policy guardrails for security, compliance and change control.

severity: 'critical' -> immediate safe-stop | 'block' -> change rejected (agent may retry) | 'warn' -> recorded,
must be acknowledged at release.   Approval reasons: change needs a named human before it is committed.
"""
import re
from dataclasses import dataclass

DEP_ALLOWLIST = {"fastapi", "uvicorn", "httpx", "pytest", "pydantic"}
PROTECTED = ("requirements.txt", ".github/", "Dockerfile", "LICENSE")
MAX_FILES, MAX_LINES = 15, 900

SECRETS = [
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]
HARDCODED = re.compile(r"""(?i)\b(password|passwd|secret|api[_-]?key|token)\s*=\s*["'][^"']{6,}["']""")
DANGEROUS = re.compile(r"\b(eval|exec)\(|os\.system\(|pickle\.loads?\(|shell\s*=\s*True|yaml\.load\(")
DESTRUCTIVE_SQL = re.compile(r"(?i)\bUPDATE\s+\w+\s+SET\b|\bDROP\s+(TABLE|COLUMN)\b|\bTRUNCATE\b")
RAW_IP_PERSISTED = re.compile(r"_storage\.record_click\(.*,\s*ip\)\s*$", re.M)


@dataclass
class Finding:
    rule: str
    severity: str
    path: str
    message: str

    def __str__(self):
        return f"[{self.severity.upper()}] {self.rule} {self.path}: {self.message}"


@dataclass
class PolicyReport:
    findings: list
    approval_reasons: list

    @property
    def critical(self):
        return [f for f in self.findings if f.severity == "critical"]

    @property
    def blocking(self):
        return [f for f in self.findings if f.severity == "block"]


def _added_lines(old, new):
    old_lines = set((old or "").splitlines())
    return [l for l in new.splitlines() if l not in old_lines]


class Policy:
    def check_change(self, files: dict[str, str], read_existing) -> PolicyReport:
        findings, reasons = [], []
        total_added = 0
        for path, new in files.items():
            old = read_existing(path)
            added = _added_lines(old, new)
            total_added += len(added)
            is_test = path.startswith("tests/")
            added_text = "\n".join(added)
            for name, rx in SECRETS:
                if rx.search(added_text):
                    findings.append(Finding("P-SEC-001", "critical", path, f"{name} committed in source"))
            if not is_test and HARDCODED.search(added_text):
                findings.append(Finding("P-SEC-001", "block", path, "hard-coded credential-like assignment"))
            if path.endswith(".py") and not is_test and DANGEROUS.search(added_text):
                findings.append(Finding("P-SEC-002", "block", path, "dangerous call (eval/exec/os.system/pickle/shell=True)"))
            if path.endswith("requirements.txt"):
                for dep in filter(None, (l.split("==")[0].strip().lower() for l in added)):
                    if dep not in DEP_ALLOWLIST:
                        findings.append(Finding("P-CHG-002", "block", path, f"dependency '{dep}' is not on the allowlist"))
            if path.endswith(".py") and not is_test:
                if RAW_IP_PERSISTED.search(added_text):
                    findings.append(Finding("P-COMP-001", "warn", path, "raw client IP (personal data) is persisted"))
                if DESTRUCTIVE_SQL.search(added_text):
                    reasons.append(f"{path}: destructive/irreversible SQL in change")
            if path.startswith(PROTECTED) or path in PROTECTED:
                reasons.append(f"{path}: protected path (change control)")
        if len(files) > MAX_FILES or total_added > MAX_LINES:
            reasons.append(f"blast radius {len(files)} files / {total_added} lines exceeds {MAX_FILES}/{MAX_LINES}")
        return PolicyReport(findings, reasons)

    def scan_tree(self, files: dict[str, str]) -> list:
        """Whole-tree scan used by the security review task (not just the diff)."""
        return self.check_change(files, lambda _p: "").findings
