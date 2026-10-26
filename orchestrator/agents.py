"""SDLC agents. Each is a pure proposal function: (ctx, task, attempt) -> Result. They never touch the workspace directly.

The generation steps here are deterministic (template + anchored-patch based) so runs are reproducible and testable
offline; the analysis steps (AST impact graph, pytest, OpenAPI extraction) operate on the real workspace. An LLM-backed
agent can be swapped in behind the same signature — the orchestration, policy and gates around it do not change.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .core import Result, Task

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "templates"))
import patches as P  # noqa: E402

V1 = ROOT / "templates" / "v1"


class PatchError(Exception):
    pass


# ============================================================================ helpers
def load_template(prefixes):
    out = {}
    for p in sorted(V1.rglob("*")):
        rel = str(p.relative_to(V1))
        if p.is_file() and "__pycache__" not in rel and any(rel.startswith(x) for x in prefixes):
            out[rel] = p.read_text()
    return out


def apply_ops(ctx, ops, base=None):
    """Apply anchored ops over workspace content. Exact-once anchors; anything else is a hard failure."""
    files = dict(base or {})

    def cur(path):
        if path in files:
            return files[path]
        text = ctx.ws.read(path)
        if text is None:
            raise PatchError(f"{path} does not exist")
        return text

    for op in ops:
        kind, path = op[0], op[1]
        if kind == "create":
            files[path] = op[2]
        elif kind == "append":
            files[path] = cur(path) + op[2]
        elif kind == "replace":
            text, n = cur(path), cur(path).count(op[2])
            if n != 1:
                raise PatchError(f"anchor in {path} found {n}x (expected 1): {op[2][:70]!r}")
            files[path] = text.replace(op[2], op[3])
    return files


def run_pytest(ctx, timeout=180, isolated=False):
    """isolated=True tests a private snapshot, so concurrent commits by other tasks cannot perturb the result."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory() as tmp:
        cwd = ctx.ws.root
        if isolated:
            cwd = Path(tmp) / "snap"
            shutil.copytree(ctx.ws.root, cwd, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore"],
                           cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    out = p.stdout + p.stderr
    tail = [l for l in out.strip().splitlines() if l.strip()][-1] if out.strip() else "no output"
    failed = [l[7:].strip() for l in out.splitlines() if l.startswith("FAILED ")]
    return {"passed": p.returncode == 0, "summary": tail, "failed": failed, "returncode": p.returncode}


def tests_green(ctx, result):
    rep = result.artifacts.get("test_report") or result.artifacts.get("baseline_report") or {}
    return rep.get("passed", False), rep.get("summary", "no test report")


def requirement_present(ctx):
    return bool(ctx.requirement and ctx.requirement.strip()), "requirement text present"


# ============================================================================ requirements
VAGUE = [
    ("speed", r"\b(fast|faster|quick|quicker|speed|snappy|performant)\b", r"\d+\s*ms",
     "What latency target (e.g. p95 redirect time) and traffic profile define 'faster'?",
     "p95 redirect < 20 ms on a read-heavy workload", True),
    ("privacy", r"\b(private|privacy|confidential|anonymous)\b", r"$^",
     "What must be private: visitor data (IPs), who can see analytics, or link targets? Is existing data in scope?",
     "visitor IPs are not stored raw", True),
    ("scale", r"\b(scalable|scale)\b", r"\d+\s*(rps|qps|req)", "What sustained throughput must it handle?",
     "single node, ~500 rps", False),
    ("reliability", r"\b(reliable|reliability|robust)\b", r"\d+\s*(%|nines)", "What availability / error budget applies?",
     "health endpoint, bounded retries, idempotent delete, input validation", False),
]
FEATURES = {
    "core": r"shorten|short url|url shortener|redirect",
    "alias": r"alias|custom",
    "analytics": r"analytic|stats|click",
    "reliability": r"reliab|health|retry|robust",
    "expiry": r"expir|ttl|time[- ]to[- ]live",
    "ratelimit": r"rate[- ]?limit|throttl|abuse",
}
STORIES = {
    "core": ("Shorten a URL and redirect to it", ["POST /shorten returns 201 with code+short_url", "GET /{code} returns 302 to the target",
                                                  "non-http(s)/internal/oversized URLs are rejected with 422"]),
    "alias": ("Choose a custom alias", ["valid alias is honoured", "duplicate alias returns 409", "reserved/invalid alias returns 422"]),
    "analytics": ("See click analytics for a link", ["GET /stats/{code} returns total clicks, last access, clicks per day"]),
    "reliability": ("Operate the service safely", ["GET /healthz reports liveness", "code collisions are retried a bounded number of times",
                                                   "DELETE /links/{code} removes link and its analytics"]),
    "expiry": ("Links can expire", ["ttl_seconds optional on create", "expired link returns 410", "existing links/DBs keep working (migration is additive)"]),
    "ratelimit": ("Protect link creation from abuse", ["per-client token bucket on POST /shorten", "429 with Retry-After when exhausted",
                                                       "redirects are never rate limited"]),
    "cache": ("Redirects are fast", ["hot links served from an in-process LRU cache", "delete invalidates the cache", "stale reads are TTL-bounded"]),
    "privacy_ip": ("Visitor IPs are not stored raw", ["clicks store a keyed hash, never the raw IP"]),
    "admin_stats": ("Analytics restricted to admins", ["/stats requires X-Admin-Token; fails closed when unset"]),
    "scrub_legacy_ip": ("Existing raw IPs are removed", ["migration nulls previously stored raw IPs (irreversible)"]),
}


def normalize(text, answers=None):
    """Interpret intent -> structured, testable problem statement. `answers` resolves blocking ambiguities."""
    low, answers = text.lower(), answers or {}
    features = [f for f, rx in FEATURES.items() if re.search(rx, low)]
    ambiguities, assumptions = [], []
    for aid, rx, specific, question, assumed, blocking in VAGUE:
        if re.search(rx, low) and not re.search(specific, low):
            amb = {"id": aid, "question": question, "assumed": assumed, "blocking": blocking, "resolution": answers.get(aid)}
            ambiguities.append(amb)
            if not amb["resolution"]:
                assumptions.append(f"{aid}: assumed '{assumed}'" + (" (BLOCKING — needs human answer)" if blocking else ""))
    ids = {a["id"] for a in ambiguities}
    if "speed" in ids:          # mechanism assumed (cache) until a human refines it
        features.append("cache")
    if "privacy" in ids:        # minimum interpretation (no raw IPs); extra scope only if a human asks for it
        features.append("privacy_ip")
        ans = (answers.get("privacy") or "").lower()
        if re.search(r"admin|token|who can|access", ans):
            features.append("admin_stats")
        if re.search(r"scrub|existing|legacy|purge|already", ans):
            features.append("scrub_legacy_ip")
    if "reliability" in ids:
        features.append("reliability")
    features = list(dict.fromkeys(features))
    open_blocking = [a["id"] for a in ambiguities if a["blocking"] and not a["resolution"]]
    return {"raw": text, "features": features,
            "stories": [{"id": f"US-{i}", "feature": f, "text": STORIES[f][0], "acceptance": STORIES[f][1]}
                        for i, f in enumerate([f for f in features if f in STORIES], 1)],
            "ambiguities": ambiguities, "assumptions": assumptions, "open_blocking": open_blocking,
            "status": "draft" if open_blocking else "resolved"}


def requirements_agent(ctx, task, attempt):
    req = normalize(ctx.requirement)
    r = Result(artifacts={"requirements": req})
    r.decisions.append(("normalized requirement",
                        f"features={req['features']}; {len(req['ambiguities'])} ambiguity(ies), blocking={req['open_blocking']}"))
    return r


def clarify_agent(ctx, task, attempt):
    req = ctx.get("requirements")
    if not req["open_blocking"]:
        return Result(notes="nothing to clarify")
    qs = [a for a in req["ambiguities"] if a["blocking"] and not a["resolution"]]
    dec = ctx.clarify(task.id, qs)
    missing = [q["id"] for q in qs if q["id"] not in dec.answers]
    if missing:
        raise RuntimeError(f"no human answer for blocking ambiguity: {missing}")
    final = normalize(ctx.requirement, dec.answers)
    return Result(artifacts={"requirements": final},
                  decisions=[("resolved ambiguities with human input", f"features now {final['features']}")], notes="clarified")


# ============================================================================ design
ADR = {
    "core": ("Random 7-char base62 codes with bounded collision retry",
             "62^7 ≈ 3.5e12 codes, non-enumerable, no coordination needed; sequential IDs rejected (guessable, needs a counter)"),
    "alias": ("Alias = [A-Za-z0-9_-]{3,32}, minus reserved route names", "prevents route shadowing (/stats, /healthz) and homoglyph abuse"),
    "analytics": ("One row per click, aggregated on read", "simple and exact; write-per-redirect cost accepted at prototype scale (roadmap: async batching)"),
    "reliability": ("302 (not 301) redirects + strict URL validation",
                    "301 is cached by browsers and would bypass analytics/expiry/delete; validation blocks non-http(s) and internal targets"),
    "expiry": ("Lazy expiry on read (410 Gone), additive column migration", "no sweeper to operate; old DBs upgrade in place without data loss"),
    "ratelimit": ("In-process per-IP token bucket on POST /shorten only", "cheap and dependency-free; per-node and does not trust X-Forwarded-For (documented limit)"),
    "cache": ("In-process LRU with TTL, invalidated on delete", "removes DB read from hot path; TTL bounds staleness if invalidation is missed"),
    "privacy_ip": ("Keyed HMAC of client IP instead of raw IP", "keeps unique-visitor signal without storing personal data"),
    "admin_stats": ("/stats gated by X-Admin-Token, fail-closed", "analytics may reveal behaviour; breaking change to API contract, tests migrated"),
    "scrub_legacy_ip": ("One-time NULL-ing of raw IPs on startup migration", "irreversible → requires explicit human approval"),
}
COMPONENTS = {
    "core": "shortener.app (HTTP) → shortener.service (rules) → shortener.storage (SQLite); validation, codec, errors",
    "analytics": "clicks table + Storage.click_stats", "ratelimit": "shortener.ratelimit.TokenBucketLimiter",
    "cache": "shortener.cache.LRUCache", "privacy_ip": "shortener.privacy.hash_visitor", "expiry": "links.expires_at + validate_ttl",
}


def design_agent(ctx, task, attempt):
    time.sleep(ctx.config.get("design_latency", 0))
    req = ctx.get("requirements")
    feats = [f for f in req["features"] if f in ADR]
    doc = ["# Design", "", f"Status: {req['status']}", "", "## Components"]
    doc += [f"- {c}" for f, c in COMPONENTS.items() if f in req["features"]]
    doc += ["", "## Key decisions"] + [f"{i}. **{ADR[f][0]}** — {ADR[f][1]}" for i, f in enumerate(feats, 1)]
    doc += ["", "## Failure scenarios", "| Scenario | Handling |", "|---|---|",
            "| Code collision | bounded retry (5) then 503 |", "| Malicious target URL | 422 at creation (scheme/host/IP checks) |",
            "| DB unavailable | 5xx; no partial writes (transactions) |"]
    if "expiry" in feats:
        doc.append("| Legacy DB without expires_at | additive migration at startup |")
    if "ratelimit" in feats:
        doc.append("| Abuse burst | 429 + Retry-After; memory-bounded buckets |")
    if "cache" in feats:
        doc.append("| Stale cache after delete | explicit invalidation + TTL bound |")
    r = Result(artifacts={"design": {"features": feats, "decisions": [ADR[f][0] for f in feats]}},
               files={"docs/DESIGN.md": "\n".join(doc) + "\n"})
    r.decisions += [(ADR[f][0], ADR[f][1]) for f in feats]
    return r


API_TABLE = {
    "core": [("POST", "/shorten", [201, 422]), ("GET", "/{code}", [302, 404])],
    "alias": [("POST", "/shorten", [409])], "analytics": [("GET", "/stats/{code}", [200, 404])],
    "reliability": [("GET", "/healthz", [200]), ("DELETE", "/links/{code}", [204, 404])],
}


def api_contract_agent(ctx, task, attempt):
    req, rows = ctx.get("requirements"), []
    for f in req["features"]:
        rows += [{"method": m, "path": p, "statuses": s, "feature": f} for m, p, s in API_TABLE.get(f, [])]
    return Result(artifacts={"api_contract": rows}, decisions=[("API contract derived from stories", f"{len(rows)} operations")])


# ============================================================================ implementation / tests
def _inject_fault(ctx, task, attempt, files, feature=None):
    fault = ctx.config.get("fault")
    if not fault or (ctx.config.get("fault_feature") and ctx.config["fault_feature"] != feature):
        return files
    if fault == "drift":
        raise PatchError("simulated upstream drift: anchor no longer present in target file")
    if fault == "secret" and attempt == 1:
        files["src/shortener/config.py"] = 'AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"\n'
    if fault == "bug" and attempt == 1 and "src/shortener/service.py" in files:
        files["src/shortener/service.py"] = re.sub(r"^(\s*)self\._storage\.record_click\(.*\)$", r"\1pass  # clicks not recorded",
                                                   files["src/shortener/service.py"], flags=re.M)
    return files


def implement_greenfield(ctx, task, attempt):
    req = ctx.get("requirements")
    if "core" not in req["features"]:
        raise RuntimeError(f"no generator for features {req['features']}")
    files = load_template(("src/", "requirements.txt"))
    if ctx.feedback.get(task.id):
        ctx.record(task.id, "repair attempt", f"re-generating using feedback: {ctx.feedback[task.id][:160]}")
    return Result(files=_inject_fault(ctx, task, attempt, files), notes=f"{len(files)} source files from v1 generator")


def tests_greenfield(ctx, task, attempt):
    return Result(files=load_template(("tests/",)), notes="unit + API tests derived from API contract")


def implement_change(ctx, task, attempt, feature, ops, label):
    if ctx.feedback.get(task.id):
        ctx.record(task.id, "repair attempt", f"using feedback: {ctx.feedback[task.id][:160]}")
    files = apply_ops(ctx, ops)
    return Result(files=_inject_fault(ctx, task, attempt, files, feature), notes=f"{label}: {len(files)} files changed")


def tests_change(ctx, task, attempt, new_tests, migration=None):
    """New tests always; legacy-test migration only when failing-test feedback shows legacy tests are affected."""
    files = apply_ops(ctx, new_tests)
    fb = ctx.feedback.get(task.id, "")
    if migration and "tests/test_api.py" in fb:
        files = apply_ops(ctx, migration, files)
        ctx.record(task.id, "migrated legacy tests", "feedback showed tests/test_api.py encodes the old /stats contract; updated deliberately")
    return Result(files=files, notes=f"{len(files)} test files")


# ============================================================================ analysis (brownfield)
def analyse_codebase(ws):
    mods = {}
    for rel in ws.files(".py"):
        if not rel.startswith("src/shortener/"):
            continue
        tree, name = ast.parse(ws.read(rel)), Path(rel).stem
        imports, routes = set(), []
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level == 1:
                imports.update([n.module.split(".")[0]] if n.module else [a.name for a in n.names])
            if isinstance(n, ast.FunctionDef):
                for d in n.decorator_list:
                    if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in ("get", "post", "delete", "put"):
                        routes.append(f"{d.func.attr.upper()} {d.args[0].value}")
        mods[name] = {"path": rel, "imports": sorted(imports - {name}),
                      "defs": [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))], "routes": routes}
    return mods


def impact_agent_factory(change_sets):
    """change_sets: ordered [(change_id, required_features, src_ops, test_ops, migration_ops_or_None)]"""

    def agent(ctx, task, attempt):
        mods = analyse_codebase(ctx.ws)
        by_path = {m["path"]: n for n, m in mods.items()}
        test_imports = {}
        for rel in ctx.ws.files(".py"):
            if rel.startswith("tests/"):
                test_imports[rel] = sorted(set(re.findall(r"from shortener\.(\w+)", ctx.ws.read(rel))))
        req, impact, tasks, join = ctx.get("requirements"), {}, [], {"validate": [], "security": []}
        touched_by, prev_for_file = {}, {}
        for feature, requires, src_ops, test_ops, migration in change_sets:
            if not requires <= set(req["features"]):
                continue
            files = sorted({op[1] for op in src_ops if op[1] in by_path})
            new_files = sorted({op[1] for op in src_ops if op[0] == "create"})
            touched = {by_path[f] for f in files}
            dependents = sorted({n for n, m in mods.items() if touched & set(m["imports"])} - touched)
            routes = sorted({r for n in touched | set(dependents) for r in mods[n]["routes"]})
            deps = ["design", "baseline"]
            for f in files:  # file overlap => serialise (safe), disjoint => parallel
                if f in prev_for_file and prev_for_file[f] not in deps:
                    deps.append(prev_for_file[f])
                prev_for_file[f] = f"impl_{feature}"
            impact[feature] = {"modified": files, "created": new_files, "dependents": dependents, "routes": routes,
                               "tests_affected": sorted(t for t, ms in test_imports.items() if touched & set(ms)),
                               "serialised_after": [d for d in deps if d.startswith("impl_")]}
            tasks.append(Task(f"impl_{feature}", "implementation", _bind_impl(feature, src_ops), deps, ["requirements", "design"],
                              rollback_scope=[], description=f"implement {feature}", max_retries=2))
            tasks.append(Task(f"tests_{feature}", "testing", _bind_tests(test_ops, migration), ["design"], ["requirements", "design"],
                              description=f"tests for {feature}"))
            join["validate"] += [f"impl_{feature}", f"tests_{feature}"]
            join["security"].append(f"impl_{feature}")
        if not tasks:
            raise RuntimeError(f"no approved change set covers features {req['features']}; refusing to improvise")
        r = Result(new_tasks=tasks, join=join, artifacts={"impact": {"modules": {n: m["imports"] for n, m in mods.items()},
                                                                      "features": impact}})
        for f, i in impact.items():
            r.decisions.append((f"impact of '{f}'", f"modifies {i['modified']}, creates {i['created']}, dependents {i['dependents']}, "
                                f"serialised after {i['serialised_after'] or 'nothing (parallel-safe)'}"))
        return r

    return agent


def _bind_impl(feature, ops):
    return lambda ctx, task, attempt: implement_change(ctx, task, attempt, feature, ops, feature)


def _bind_tests(test_ops, migration):
    return lambda ctx, task, attempt: tests_change(ctx, task, attempt, test_ops, migration)


# ============================================================================ validation
def baseline_agent(ctx, task, attempt):
    rep = run_pytest(ctx, isolated=True)
    return Result(artifacts={"baseline_report": rep}, notes=rep["summary"])


def validate_agent(ctx, task, attempt):
    rep = run_pytest(ctx)
    if not rep["passed"]:
        raise RuntimeError(f"pytest: {rep['summary']}; " + "; ".join(rep["failed"][:6]))
    return Result(artifacts={"test_report": rep}, notes=rep["summary"])


def security_agent_factory(policy):
    def agent(ctx, task, attempt):
        tree = {p: ctx.ws.read(p) for p in ctx.ws.files() if p.endswith((".py", ".txt")) and not p.startswith("tests/")}
        findings = policy.scan_tree(tree)
        rep = {"files_scanned": len(tree), "findings": [str(f) for f in findings],
               "blocking": sum(f.severity in ("block", "critical") for f in findings),
               "warnings": sum(f.severity == "warn" for f in findings)}
        if rep["blocking"]:
            raise RuntimeError(f"security review failed: {rep['findings']}")
        return Result(artifacts={"security_report": rep}, notes=f"{rep['files_scanned']} files, {rep['warnings']} warning(s)")

    return agent


# ============================================================================ documentation / release
def _openapi(ctx):
    code = "import json; from shortener.app import app; print(json.dumps(app.openapi()))"
    p = subprocess.run([sys.executable, "-c", code], cwd=ctx.ws.root, capture_output=True, text=True, timeout=60,
                       env={**os.environ, "PYTHONPATH": str(ctx.ws.root / "src"), "PYTHONDONTWRITEBYTECODE": "1"})
    if p.returncode:
        raise RuntimeError("openapi generation failed: " + p.stderr.strip().splitlines()[-1])
    return json.loads(p.stdout)


def docs_agent(ctx, task, attempt):
    spec = _openapi(ctx)
    env = {}
    for rel in ctx.ws.files(".py"):
        if rel.startswith("src/"):
            for name, default in re.findall(r'os\.getenv\("(\w+)"(?:,\s*"([^"]*)")?\)', ctx.ws.read(rel)):
                env[name] = default or "(unset)"
    rows = [f"| {m.upper()} | `{p}` | {op.get('summary', '')} | {', '.join(sorted(op['responses']))} |"
            for p, item in spec["paths"].items() for m, op in item.items()]
    design = ctx.get("design", {"decisions": []})
    api = ["# API reference", f"\nGenerated from the running app's OpenAPI document (v{spec['info']['version']}).\n",
           "| Method | Path | Summary | Responses |", "|---|---|---|---|", *rows]
    readme = ["# URL Shortener", "", "Generated by the agentic SDLC pipeline; see `docs/DESIGN.md` and `docs/API.md`.", "",
              "## Run", "```bash", "pip install -r requirements.txt", "PYTHONPATH=src uvicorn shortener.app:app", "```", "",
              "## Test", "```bash", "pytest", "```", "", "## Configuration", "| Variable | Default |", "|---|---|",
              *[f"| `{k}` | `{v}` |" for k, v in sorted(env.items())], "", "## Design decisions",
              *[f"- {d}" for d in design["decisions"]]]
    files = {"README.md": "\n".join(readme) + "\n", "docs/API.md": "\n".join(api) + "\n",
             "docs/openapi.json": json.dumps(spec, indent=2) + "\n"}
    return Result(files=files, artifacts={"docs": {"api_version": spec["info"]["version"], "endpoints": len(rows)}},
                  notes=f"{len(rows)} operations documented")


def release_agent(ctx, task, attempt):
    tr, sec = ctx.get("test_report"), ctx.get("security_report")
    open_warnings = [f for f in (sec or {}).get("findings", []) if f.startswith("[WARN]")]
    checklist = {"tests_green": bool(tr and tr["passed"]), "security_review_clean": bool(sec and not sec["blocking"]),
                 "docs_present": ctx.ws.read("README.md") is not None and ctx.ws.read("docs/API.md") is not None,
                 "all_required_approvals_recorded": all(a["approved"] for a in ctx.approvals)}
    if not all(checklist.values()):
        raise RuntimeError(f"release readiness failed: {[k for k, v in checklist.items() if not v]}")
    notes = ["# Release notes", "", "## Readiness checklist", *[f"- [x] {k}" for k in checklist], "",
             "## Open warnings (acknowledged by approver)", *([f"- {w}" for w in open_warnings] or ["- none"]), "",
             "## Rollback plan", "- Redeploy previous version; schema changes are additive/forward-only.",
             "- Irreversible data migrations (if any) are listed in the audit log with their approver."]
    return Result(files={"RELEASE_NOTES.md": "\n".join(notes) + "\n"}, artifacts={"release": {"checklist": checklist, "open_warnings": open_warnings}},
                  notes="release readiness verified")
