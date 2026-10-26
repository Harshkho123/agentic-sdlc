import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .errors import AliasTakenError, CodeGenerationError, NotFoundError
from .service import LinkService
from .storage import Storage
from .validation import ValidationError


class ShortenRequest(BaseModel):
    url: str
    custom_alias: str | None = None


class ShortenResponse(BaseModel):
    code: str
    short_url: str
    url: str


def create_app(service=None):
    svc = service or LinkService(
        Storage(os.getenv("SHORTENER_DB", ":memory:")),
        base_url=os.getenv("BASE_URL", "http://localhost:8000"),
    )
    app = FastAPI(title="URL Shortener", version="1.0.0")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/shorten", status_code=201, response_model=ShortenResponse)
    def shorten(req: ShortenRequest):
        try:
            return svc.shorten(req.url, req.custom_alias)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except AliasTakenError:
            raise HTTPException(status_code=409, detail="alias already in use")
        except CodeGenerationError:
            raise HTTPException(status_code=503, detail="temporarily unable to allocate a code")

    @app.get("/stats/{code}")
    def stats(code: str):
        try:
            return svc.stats(code)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @app.delete("/links/{code}", status_code=204)
    def delete_link(code: str):
        try:
            svc.delete(code)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")

    @app.get("/{code}")
    def redirect(code: str, request: Request):
        ip = request.client.host if request.client else None
        try:
            target = svc.resolve(code, ip)
        except NotFoundError:
            raise HTTPException(status_code=404, detail="not found")
        return RedirectResponse(url=target, status_code=302)

    return app


app = create_app()
