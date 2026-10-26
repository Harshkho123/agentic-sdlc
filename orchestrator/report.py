"""Final engineering summary generated from run state (plan, rationale, artifacts, validation, metrics, risks)."""
from .audit import AuditLog


def _tbl(head, rows):
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]


def write_summary(engine, meta, path):
    ctx, g, m = engine.ctx, engine.graph, engine.metrics.summary(len(engine.graph.tasks))
    req = ctx.get("requirements") or {}
    ok, chain = AuditLog.verify(ctx.audit.path)
    L = [f"# Final Engineering Summary — {meta['title']}", "",
         f"**Outcome:** `{engine.state.value}`" + (f" — {engine.stop_reason}" if engine.stop_reason else ""), "",
         f"> Requirement: *{ctx.requirement}*", "", "## 1. Requirement understanding",
         f"- Normalized features: `{', '.join(req.get('features', []))}` (status: **{req.get('status')}**, requirement v{ctx.version('requirements')})"]
    for s in req.get("stories", []):
        L.append(f"- **{s['id']}** {s['text']} — " + "; ".join(s["acceptance"]))
    for a in req.get("ambiguities", []):
        L.append(f"- Ambiguity `{a['id']}`: {a['question']} → " + (f"**resolved by human:** {a['resolution']}" if a["resolution"] else f"assumed: {a['assumed']}"))
    L += ["", "## 2. Plan and orchestration", "```mermaid", g.mermaid(), "```", "", f"Parallel layers (no dependency path inside a layer): `{g.layers()}`", ""]
    L += _tbl(["task", "stage", "depends on", "status", "attempts"], [(t.id, t.stage, ", ".join(t.deps) or "—", t.status.value, t.attempts) for t in g.tasks.values()])
    L += ["", "## 3. Rationale and decision lineage", ""]
    L += _tbl(["#", "task", "decision", "rationale", "inputs", "actor"],
              [(d.seq, d.task, d.decision, d.rationale[:240], d.inputs or "", d.actor) for d in ctx.decisions])
    L += ["", "## 4. Artifacts", ""]
    L += _tbl(["artifact", "version", "hash", "producer", "built from"], [(a.name, a.version, a.hash, a.producer, a.inputs or "—") for vs in ctx.artifacts.values() for a in vs])
    L += ["", "Workspace files:", ""] + [f"- `{f}`" for f in ctx.ws.files()]
    L += ["", "## 5. Validation and risk control", f"- Tests: `{(ctx.get('test_report') or {}).get('summary', 'n/a')}`",
          f"- Security review: `{(ctx.get('security_report') or {}).get('findings', 'n/a')}`", "", "Policy findings seen during the run:", ""]
    L += _tbl(["rule", "severity", "path", "message"], [(f.rule, f.severity, f.path, f.message) for f in ctx.findings]) if ctx.findings else ["- none"]
    L += ["", "Human approvals:", ""]
    L += _tbl(["task", "approved", "approver", "why approval was required", "reason"], [(a["task"], a["approved"], a["approver"], "; ".join(a["reasons"]), a["reason"]) for a in ctx.approvals]) if ctx.approvals else ["- none required"]
    L += ["", "## 6. Reliability metrics", ""] + _tbl(["metric", "value"], list(m.items()))
    L += ["", "## 7. Risks and trade-offs", ""] + _tbl(["risk / trade-off", "mitigation / decision"], meta["risks"])
    L += ["", "## 8. Assumptions", ""] + [f"- {a}" for a in meta["assumptions"] + req.get("assumptions", [])]
    L += ["", "## 9. Limitations", ""] + [f"- {a}" for a in meta["limitations"]]
    L += ["", "## 10. Audit", f"- `audit.jsonl`: {len(ctx.audit.events())} hash-chained records — **{chain}**"]
    path.write_text("\n".join(L) + "\n")
    return ok
