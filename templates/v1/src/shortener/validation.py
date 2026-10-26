import ipaddress
import re
from urllib.parse import urlsplit

MAX_URL_LEN = 2048
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
RESERVED_ALIASES = {"stats", "links", "healthz", "shorten", "docs", "openapi.json", "redoc"}


class ValidationError(ValueError):
    pass


def validate_url(raw):
    """Accept only absolute http(s) URLs that are not obviously internal targets."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError("url must be a non-empty string")
    raw = raw.strip()
    if len(raw) > MAX_URL_LEN:
        raise ValidationError(f"url exceeds {MAX_URL_LEN} characters")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValidationError("only http and https urls are allowed")
    host = parts.hostname
    if not host:
        raise ValidationError("url must include a host")
    if parts.username or parts.password:
        raise ValidationError("credentials in url are not allowed")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValidationError("internal hosts are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip and (ip.is_private or ip.is_loopback or ip.is_link_local
               or ip.is_reserved or ip.is_unspecified or ip.is_multicast):
        raise ValidationError("internal addresses are not allowed")
    return raw


def validate_alias(alias):
    if not isinstance(alias, str) or not ALIAS_RE.match(alias):
        raise ValidationError("alias must be 3-32 chars of letters, digits, '_' or '-'")
    if alias.lower() in RESERVED_ALIASES:
        raise ValidationError("alias is reserved")
    return alias
