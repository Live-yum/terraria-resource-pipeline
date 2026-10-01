"""Loopback-only administration demo, with explicit review before publication."""
from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .fixtures import demo_archive
from .pipeline import Pipeline
from .security import ArchiveLimits, PipelineError, relative_path


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviewDigest: str
    confirmed: bool


def create_app(root: Path | None = None, synchronous: bool = False) -> FastAPI:
    state_root = root or Path(os.environ.get("RESOURCE_PIPELINE_STATE", ".runtime"))
    pipeline = Pipeline(state_root)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="resource-extract")

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="资源更新与审核演示", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "跨站修改请求被拒绝"}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "跨站修改请求被拒绝"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob: data:; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(PipelineError)
    async def known_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/jobs")
    def jobs():
        return pipeline.list()

    @app.get("/api/jobs/{identity}")
    def job(identity: str):
        try:
            return pipeline.get(identity)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.post("/api/jobs", status_code=202)
    async def upload(file: UploadFile = File()):
        # Read the bounded upload before scheduling: UploadFile is closed when
        # the HTTP request ends. No worker ever references a closed request file.
        chunks = []
        total = 0
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > ArchiveLimits().archive_bytes:
                raise HTTPException(413, "上传包超过体积限制")
            chunks.append(chunk)
        created = pipeline.submit(b"".join(chunks), file.filename or "upload.zip")
        if synchronous:
            return pipeline.process(created["id"])
        executor.submit(pipeline.process, created["id"])
        return created

    @app.post("/api/jobs/{identity}/publish")
    def publish(identity: str, review: Review):
        try:
            return pipeline.publish(identity, review.reviewDigest, review.confirmed)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.get("/api/current")
    def current():
        return pipeline.current()

    @app.get("/api/demo/{revision}.zip")
    def fixture(revision: int):
        if revision not in (1, 2):
            raise HTTPException(404)
        return Response(demo_archive(revision), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="synthetic-v{revision}.zip"'})

    @app.get("/cdn/{asset_path:path}")
    def cdn(asset_path: str):
        try:
            relative_path(asset_path)
        except PipelineError:
            raise HTTPException(404) from None
        if not asset_path.startswith(("objects/", "releases/", "channels/")):
            raise HTTPException(404)
        try:
            content = pipeline.published_file(asset_path)
        except FileNotFoundError:
            raise HTTPException(404) from None
        media = "image/png" if asset_path.endswith(".png") else "application/gzip" if asset_path.endswith(".gz") else "application/json"
        return Response(content, media_type=media, headers={"Cache-Control": "no-cache" if asset_path.startswith("channels/") else "public,max-age=31536000,immutable"})

    web_root = Path(__file__).resolve().parents[2] / "web"
    app.mount("/", StaticFiles(directory=web_root, html=True), name="demo-ui")
    return app
