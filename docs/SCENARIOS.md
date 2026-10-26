# Scenarios

All three run with `python run.py <name>`; full evidence (plan, lineage, metrics, audit) is in `examples/<name>/SUMMARY.md`.

## 1. Greenfield — "Build a URL shortener from scratch with core APIs, analytics and reliability"
**Understanding.** `normalize()` extracts features (core, alias, analytics, reliability), user stories with acceptance criteria and one
*non-blocking* ambiguity ("reliability") resolved by a recorded assumption (health check, bounded retries, idempotent delete, validation).

**Decomposition / orchestration.**
```
requirements ─┬─► design ──────────┐
              └─► api_contract ─┬──► implement ─┬─► validate ─┬─► docs ─► release (human sign-off)
                                └──► tests ─────┘             │
                                     implement ─► security ───┘      (implement ∥ tests, validate ∥ security)
```
**Validation / risk.** 20 generated tests (unit+API, incl. SSRF-style URL rejection, alias conflicts, bounded collision retry);
security review re-scans the tree; policy flags raw-IP storage (WARN) which the release approver must acknowledge; `requirements.txt`
creation triggers change-control approval. Chaos modes: `--fault bug` (tests catch a silently dropped click-recording line → repair
loop → recovery, MTTR recorded), `--fault secret` (critical violation → immediate safe-stop, zero files committed).

## 2. Brownfield — "Add optional link expiry and rate limiting on creation without breaking clients or data"
**Codebase reasoning.** The `impact` task parses the workspace with `ast`: import graph, routes, public defs, test→module imports.
For each change it reports modified/created files, dependents, affected routes and tests, and **which changes must be serialised**
(both edit `app.py` → `impl_ratelimit` waits for `impl_expiry`; the two test-authoring tasks run in parallel with them).
**Safety.** A `baseline` task must be green (on an isolated snapshot) before any change; schema change is additive and tested against
a legacy-schema DB file; anchored patches fail loudly on drift (`--fault drift`: 3 attempts → rollback → safe-stop, baseline untouched).
**Result.** 32 tests green (20 legacy + 12 new), API version 1.1.0, backwards compatible.

## 3. Ambiguous — "Make the shortener faster and more private"
**Ambiguity handling.** `speed` has no latency target; `privacy` could mean IPs, analytics access, or link targets — both are
*blocking* (they change data handling), so the draft requirement goes to a **human checkpoint** (`clarify`).
**Non-linear execution.** `design` drafts in parallel under assumptions. When the human's answers arrive, requirements v2 invalidates
that draft (its in-flight result is discarded and re-run) and widens the scope: admin-only analytics and a scrub of already stored IPs.
**Governance.** The scrub is irreversible SQL → policy forces explicit approval *before* commit. Making `/stats` token-protected breaks
the old contract, so 2 legacy tests fail; `validate` routes the failure to `tests_cache_privacy` (not to implementation), which migrates
the legacy tests deliberately (visible in the lineage), after rollback of its previous commit. The P-COMP-001 warning disappears.
