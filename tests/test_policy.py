from orchestrator.policy import Policy

P = Policy()
none = lambda p: ""


def rules(files):
    r = P.check_change(files, none)
    return {f.rule for f in r.findings}, r


def test_secret_is_critical():
    _, r = rules({"src/a.py": "k='AKIAIOSFODNN7EXAMPLE'"})
    assert r.critical


def test_hardcoded_credential_blocked_in_src_but_not_tests():
    assert rules({"src/a.py": 'password = "hunter22!"'})[1].blocking
    assert not rules({"tests/t.py": 'password = "hunter22!"'})[1].blocking


def test_dangerous_calls_blocked():
    for bad in ("eval(x)", "os.system('ls')", "subprocess.run(c, shell=True)", "pickle.loads(b)"):
        assert rules({"src/a.py": bad})[1].blocking, bad


def test_destructive_sql_needs_approval_only_when_newly_added():
    r = P.check_change({"src/s.py": "conn.execute('UPDATE t SET a=NULL')"}, none)
    assert r.approval_reasons
    same = "conn.execute('UPDATE t SET a=NULL')"
    assert not P.check_change({"src/s.py": same}, lambda p: same).approval_reasons


def test_blast_radius_requires_approval():
    assert P.check_change({f"src/f{i}.py": "x=1" for i in range(16)}, none).approval_reasons


def test_raw_ip_persistence_is_a_warning_hashed_is_not():
    assert rules({"src/s.py": "self._storage.record_click(code, now, ip)"})[0] == {"P-COMP-001"}
    assert not rules({"src/s.py": "self._storage.record_click(code, now, hash_visitor(ip))"})[0]
    assert not rules({"src/s.py": "def record_click(self, code, ts, ip):"})[0]
