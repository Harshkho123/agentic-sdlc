import pytest

from shortener.errors import AliasTakenError, NotFoundError
from shortener.service import LinkService
from shortener.storage import Storage
from shortener.validation import ValidationError


@pytest.fixture
def svc():
    return LinkService(Storage(), base_url="http://sho.rt", clock=lambda: 1_700_000_000.0)


def test_shorten_and_resolve(svc):
    out = svc.shorten("https://example.com/a?b=1")
    assert len(out["code"]) == 7 and out["short_url"] == f"http://sho.rt/{out['code']}"
    assert svc.resolve(out["code"]) == "https://example.com/a?b=1"


def test_custom_alias_conflict(svc):
    svc.shorten("https://example.com", alias="my-link")
    with pytest.raises(AliasTakenError):
        svc.shorten("https://example.org", alias="my-link")


@pytest.mark.parametrize("bad", ["ftp://x.com", "javascript:alert(1)", "http://localhost/x",
                                 "http://127.0.0.1/", "http://10.0.0.5/", "https://u:p@x.com", "", "http://" + "a" * 2100])
def test_rejects_unsafe_urls(svc, bad):
    with pytest.raises(ValidationError):
        svc.shorten(bad)


@pytest.mark.parametrize("alias", ["ab", "has space", "stats", "x" * 40])
def test_rejects_bad_alias(svc, alias):
    with pytest.raises(ValidationError):
        svc.shorten("https://example.com", alias=alias)


def test_stats_count_clicks(svc):
    code = svc.shorten("https://example.com")["code"]
    for _ in range(3):
        svc.resolve(code, ip="1.2.3.4")
    s = svc.stats(code)
    assert s["total_clicks"] == 3 and sum(s["clicks_by_day"].values()) == 3


def test_delete_removes_link_and_clicks(svc):
    code = svc.shorten("https://example.com")["code"]
    svc.delete(code)
    with pytest.raises(NotFoundError):
        svc.resolve(code)
    with pytest.raises(NotFoundError):
        svc.delete(code)


def test_collision_retry_is_bounded(monkeypatch, svc):
    from shortener import service
    from shortener.errors import CodeGenerationError
    monkeypatch.setattr(service, "generate_code", lambda n: "fixed00")
    svc.shorten("https://example.com")
    with pytest.raises(CodeGenerationError):
        svc.shorten("https://example.org")
