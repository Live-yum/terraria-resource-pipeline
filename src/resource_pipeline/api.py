"""Loopback-only administration demo, with explicit review before publication."""
from __future__ import annotations

from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .fixtures import demo_archive
from .intake import UploadBodyLimitMiddleware
from .pipeline import Pipeline
from .consumer_control import ConsumerReleaseControl
from .preflight import RawInputPreflight
from .textures import configured_texture_extractor
from .real_producer import RawEvidenceProducer
from .security import ArchiveLimits, PipelineError, relative_path


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reviewDigest: str
    confirmed: bool


def create_app(root: Path | None = None, synchronous: bool = False, consumer_demo: bool = False) -> FastAPI:
    state_root = root or Path(os.environ.get("RESOURCE_PIPELINE_STATE", ".runtime"))
    pipeline = Pipeline(state_root)
    consumer_control = ConsumerReleaseControl(pipeline, publisher_id="synthetic-local-demo", synthetic_demo=consumer_demo)
    raw_preflight = RawInputPreflight(pipeline, texture_extractor=configured_texture_extractor(), semantic_producer=RawEvidenceProducer())
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="resource-extract")

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="资源更新与审核演示", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.consumer_control = consumer_control
    app.state.raw_preflight = raw_preflight
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    app.add_middleware(UploadBodyLimitMiddleware, archive_bytes=ArchiveLimits().archive_bytes)

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

    def compact_raw(value):
        if value.get("kind") != "raw-input-preflight":
            return value
        for source in value["sources"].values():
            inventory = source.get("inventory")
            if inventory is not None:
                source["inventory"] = {"expandedBytes": inventory["expandedBytes"], "fileCount": len(inventory["files"])}
        for texture in value.get("textures", {}).values():
            for key in ("images", "skipped"):
                rows = texture.pop(key, None)
                if rows is not None:
                    texture[key + "Count"] = len(rows)
        for family in value.get("producerEvidence", {}).get("familyCoverage", {}).values():
            rows = family.pop("idsWithoutDirectImage", None)
            if rows is not None:
                family["idsWithoutDirectImageCount"] = len(rows)
        return value

    @app.get("/api/jobs")
    def jobs():
        return [compact_raw(value) for value in pipeline.list()]

    @app.get("/api/jobs/{identity}")
    def job(identity: str, compact: bool = False):
        try:
            value = pipeline.get(identity)
            return compact_raw(value) if compact else value
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.post("/api/jobs", status_code=202)
    def upload(file: UploadFile = File()):
        # The synchronous endpoint runs in FastAPI's threadpool. Copy from the
        # spooled request file while its lifetime is valid; never join a whole
        # game archive in memory or hand a closed UploadFile to the worker.
        if file.size is not None and file.size > ArchiveLimits().archive_bytes:
            raise HTTPException(413, "上传包超过体积限制")
        created = pipeline.submit_file(file.file, file.filename or "upload.zip")
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

    @app.post("/api/consumer-demo/{revision}/preview")
    def consumer_preview(revision: int):
        return consumer_control.preview_demo(revision)

    @app.post("/api/consumer-reviews/{identity}/publish")
    def consumer_publish(identity: str, review: Review):
        return consumer_control.publish(identity, review.reviewDigest, review.confirmed)

    @app.post("/api/consumer-rollbacks/{approval_sequence}/preview")
    def consumer_rollback_preview(approval_sequence: int):
        return consumer_control.preview_rollback(approval_sequence)

    @app.post("/api/consumer-reviews/{identity}/rollback")
    def consumer_rollback(identity: str, review: Review):
        return consumer_control.rollback(identity, review.reviewDigest, review.confirmed)

    @app.get("/api/consumer-current")
    def consumer_current():
        return consumer_control.current()

    @app.post("/api/raw-jobs", status_code=202)
    def raw_upload(server_file: UploadFile | None = File(default=None),
                   client_file: UploadFile | None = File(default=None),
                   declared_version: str | None = Form(default=None)):
        uploads = [upload for upload in (server_file, client_file) if upload is not None]
        if sum(upload.size or 0 for upload in uploads) > raw_preflight.limits.archive_bytes:
            raise HTTPException(413, "两个输入 ZIP 的合计体积超过上传限制")
        def source(upload):
            return (upload.file, upload.filename or "upload.zip") if upload is not None else None
        created = raw_preflight.submit(source(server_file), source(client_file), declared_version)
        if synchronous:
            return raw_preflight.process(created["id"])
        executor.submit(raw_preflight.process, created["id"])
        return created

    @app.post("/api/raw-jobs/{identity}/cancel")
    def raw_cancel(identity: str):
        try:
            return raw_preflight.request_cancel(identity)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.post("/api/raw-jobs/{identity}/retry", status_code=202)
    def raw_retry(identity: str):
        try:
            created = raw_preflight.retry(identity)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None
        if synchronous:
            return raw_preflight.process(created["id"])
        executor.submit(raw_preflight.process, created["id"])
        return created

    @app.get("/api/raw-jobs/{identity}/review")
    def raw_review(identity: str):
        try:
            value = pipeline.get(identity)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None
        if value.get("kind") != "raw-input-preflight":
            raise HTTPException(404, "不是原始双包任务")
        value = compact_raw(value)
        ready = value.get("extractionComplete") is True and value.get("state") == "READY_FOR_REVIEW"
        return {"id": identity, "state": value["state"], "reviewable": ready,
                "reviewDigest": value.get("reviewDigest") if ready else None,
                "baseRelease": value.get("baseRelease"), "release": value.get("release"),
                "diff": value.get("diff"), "coverage": value.get("coverage"),
                "blockers": value.get("blockers", []),
                "familyCoverage": value.get("producerEvidence", {}).get("familyCoverage", {})}

    @app.post("/api/raw-jobs/{identity}/publish")
    def raw_publish(identity: str, review: Review):
        checked = raw_review(identity)
        if not checked["reviewable"] or not review.confirmed or review.reviewDigest != checked["reviewDigest"]:
            raise HTTPException(409, "真实语义尚未完整或审核已变化，未发布任何资源")
        # Real publication identity/provider must be configured independently.
        raise HTTPException(409, "真实 CDN 发布身份尚未配置，未发布任何资源")

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
        if not asset_path.startswith(("objects/", "releases/", "channels/", "consumer/", "release-sets/")):
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
