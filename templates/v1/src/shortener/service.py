import time

from .codec import generate_code
from .errors import AliasTakenError, CodeGenerationError, NotFoundError
from .validation import validate_alias, validate_url


class LinkService:
    def __init__(self, storage, base_url="http://localhost:8000", clock=time.time, code_len=7):
        self._storage = storage
        self._base_url = base_url.rstrip("/")
        self._clock = clock
        self._code_len = code_len

    def shorten(self, url, alias=None):
        url = validate_url(url)
        now = self._clock()
        if alias:
            alias = validate_alias(alias)
            if not self._storage.insert_link(alias, url, now):
                raise AliasTakenError(alias)
            code = alias
        else:
            for _ in range(5):  # bounded retry on (rare) collisions
                code = generate_code(self._code_len)
                if self._storage.insert_link(code, url, now):
                    break
            else:
                raise CodeGenerationError("could not allocate a unique code")
        return {"code": code, "short_url": f"{self._base_url}/{code}", "url": url}

    def resolve(self, code, ip=None):
        link = self._storage.get_link(code)
        if link is None:
            raise NotFoundError(code)
        self._storage.record_click(code, self._clock(), ip)
        return link["url"]

    def stats(self, code):
        link = self._storage.get_link(code)
        if link is None:
            raise NotFoundError(code)
        return {"code": code, "url": link["url"], "created_at": link["created_at"], **self._storage.click_stats(code)}

    def delete(self, code):
        if not self._storage.delete_link(code):
            raise NotFoundError(code)
