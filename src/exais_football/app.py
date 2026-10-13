"""Same-origin, loopback application for a single local film library."""

from __future__ import annotations

import logging
import mimetypes
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException

from . import __version__
from .config import InstanceLock, Settings
from .integrations import football_cv, kinovea
from .jobs import ExportJobs
from .media import MediaError, media_capabilities
from .models import (
    AnalysisReview,
    ClipPatch,
    EmptyRequest,
    GameCreate,
    NoteCreate,
    Phase,
    PlayerCreate,
    PlaylistCreate,
    PlaylistItems,
    RestoreRequest,
    ReviewStatus,
)
from .service import Catalog, CatalogError, file_sha256
from .taxonomy_routes import router as taxonomy_router

STATIC = Path(__file__).parent / "static"
KVA_LIMIT = 2 * 1024**2
logger = logging.getLogger(__name__)


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def _copy_review_file(source: Path, destination: Path, expected_sha256: str) -> bool:
    """Publish complete verified bytes only if the native working file is absent."""
    temporary = destination.with_name(f"{uuid4()}.tmp")
    try:
        shutil.copyfile(source, temporary)
        if file_sha256(temporary) != expected_sha256:
            raise CatalogError(
                "review_copy_verification_failed",
                "The review copy did not match its preserved source; native launch was stopped",
                409,
            )
        try:
            # Link only the separate staged copy, never the preserved source. Creating
            # this directory entry is atomic and refuses to replace an existing file.
            os.link(temporary, destination)
        except FileExistsError:
            return False
        return True
    except OSError as error:
        raise CatalogError(
            "review_copy_failed",
            "Could not create a native review file. Check the data folder's write access "
            "and support for hard links",
            503,
        ) from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove a staged native review copy", exc_info=True)


class LocalGuard:
    """Deny foreign origins/DNS rebinding and bound even chunked request bodies."""

    def __init__(self, app, max_upload_bytes: int):
        self.app, self.max_upload_bytes = app, max_upload_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope["headers"]
        }
        host = headers.get("host", "")
        try:
            hostname = urlsplit("//" + host).hostname
        except ValueError:
            hostname = None
        origin = headers.get("origin")
        expected_origin = f"{scope.get('scheme', 'http')}://{host}".lower()
        if hostname not in {"localhost", "127.0.0.1", "::1"}:
            return await error_response("invalid_host", "Use the local application address", 403)(
                scope, receive, send
            )
        if (origin and origin.lower() != expected_origin) or headers.get(
            "sec-fetch-site"
        ) == "cross-site":
            return await error_response(
                "foreign_origin", "Requests must come from this application", 403
            )(scope, receive, send)
        path = scope.get("path", "")
        limit = 256 * 1024
        if path == "/api/imports":
            limit = self.max_upload_bytes + 1024**2
        elif path.endswith("/kinovea/annotations"):
            limit = KVA_LIMIT + 64 * 1024
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            return await error_response("invalid_length", "Invalid request length", 400)(
                scope, receive, send
            )
        if content_length < 0 or content_length > limit:
            return await error_response(
                "body_too_large", "This request exceeds the size limit", 413
            )(scope, receive, send)
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise HTTPException(413, "This request exceeds the size limit")
            return message

        async def secure_send(message):
            if message["type"] == "http.response.start":
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (
                        b"content-security-policy",
                        b"default-src 'self'; img-src 'self' blob: data:; "
                        b"media-src 'self' blob:; connect-src 'self'; style-src 'self'; "
                        b"script-src 'self'; object-src 'none'; "
                        b"base-uri 'none'; frame-ancestors 'none'",
                    ),
                    (b"cache-control", b"no-store"),
                ]
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, bounded_receive, secure_send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    catalog = Catalog(settings)
    jobs = ExportJobs(catalog)
    instance_lock = InstanceLock(settings.path("instance.lock"))

    @asynccontextmanager
    async def lifespan(app):
        instance_lock.acquire()
        try:
            jobs.start()
            yield
        finally:
            try:
                await run_in_threadpool(jobs.stop)
            finally:
                instance_lock.release()

    app = FastAPI(
        title="Exais Football",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(taxonomy_router(catalog))
    app.state.catalog, app.state.jobs = catalog, jobs
    app.add_middleware(LocalGuard, max_upload_bytes=settings.max_upload_bytes)

    @app.exception_handler(CatalogError)
    async def catalog_error(request, error):
        return error_response(error.code, error.message, error.status)

    @app.exception_handler(MediaError)
    async def media_error(request, error):
        status = 503 if "unavailable" in error.code or "missing_tool" in error.code else 422
        return error_response(error.code, error.message, status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return error_response(
            "validation_error", "Check the required fields, types and allowed values", 422
        )

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        code = "body_too_large" if error.status_code == 413 else "http_error"
        return error_response(code, str(error.detail), error.status_code)

    @app.get("/api/health")
    def health():
        cv_reason = (
            "Import output from an external football-cv setup; model execution is not bundled"
        )
        return {
            "status": "ok",
            "version": __version__,
            "max_upload_bytes": settings.max_upload_bytes,
            "capabilities": {
                **media_capabilities(),
                "kinovea": kinovea.capability(settings.kinovea_executable),
                "football_cv": {"available": False, "reason": cv_reason, "result_import": True},
            },
        }

    @app.get("/api/stats")
    def stats():
        return catalog.stats()

    @app.get("/api/games")
    def games_list():
        return catalog.list_entities("games")

    @app.post("/api/games", status_code=201)
    def games_create(body: GameCreate):
        return catalog.create_entity("games", body.model_dump())

    @app.get("/api/players")
    def players_list():
        return catalog.list_entities("players")

    @app.post("/api/players", status_code=201)
    def players_create(body: PlayerCreate):
        return catalog.create_entity("players", body.model_dump())

    @app.post("/api/imports", status_code=201)
    async def import_video(
        file: UploadFile = File(...),
        title: str = Form("", max_length=240),
        game_id: UUID | None = Form(None),
    ):
        staged = settings.path(f"staging/{uuid4()}.upload")
        try:
            size = 0
            with staged.open("xb") as destination:
                while chunk := await file.read(1024**2):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise CatalogError("upload_size", "The video exceeds the upload limit", 413)
                    await run_in_threadpool(destination.write, chunk)
            return await run_in_threadpool(
                catalog.ingest,
                staged,
                file.filename or "",
                title,
                str(game_id) if game_id else None,
            )
        finally:
            await file.close()
            staged.unlink(missing_ok=True)

    @app.get("/api/clips")
    def clips_list(
        q: str = Query("", max_length=240),
        game_id: UUID | None = None,
        player_id: UUID | None = None,
        tag: str | None = Query(None, max_length=64),
        phase: Phase | None = None,
        review_status: ReviewStatus | None = None,
        favorite: bool | None = None,
        archived: bool = False,
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ):
        return catalog.list_clips(
            q=q,
            game_id=str(game_id) if game_id else None,
            player_id=str(player_id) if player_id else None,
            tag=tag,
            phase=phase,
            review_status=review_status,
            favorite=favorite,
            archived=archived,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/clips/{clip_id}")
    def clip_get(clip_id: UUID):
        return catalog.get_clip(str(clip_id))

    @app.patch("/api/clips/{clip_id}")
    def clip_patch(clip_id: UUID, body: ClipPatch):
        return catalog.patch_clip(str(clip_id), body)

    @app.api_route("/api/clips/{clip_id}/source", methods=["GET", "HEAD"])
    def source(clip_id: UUID):
        path = catalog.source_path(str(clip_id))
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type)

    @app.get("/api/clips/{clip_id}/thumbnail")
    def thumbnail(clip_id: UUID):
        catalog.get_clip(str(clip_id))
        path = settings.path(f"thumbnails/{clip_id}.jpg")
        if not path.is_file():
            raise CatalogError("preview_missing", "A preview is unavailable for this clip", 404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/clips/{clip_id}/notes")
    def notes_list(clip_id: UUID):
        return catalog.notes(str(clip_id))

    @app.post("/api/clips/{clip_id}/notes", status_code=201)
    def notes_create(clip_id: UUID, body: NoteCreate):
        return catalog.add_note(str(clip_id), body.body, body.at_seconds)

    @app.delete("/api/clips/{clip_id}/notes/{note_id}", status_code=204)
    def notes_delete(clip_id: UUID, note_id: UUID):
        catalog.delete_note(str(clip_id), str(note_id))
        return Response(status_code=204)

    @app.get("/api/clips/{clip_id}/history")
    def history(clip_id: UUID):
        return catalog.history(str(clip_id))

    @app.post("/api/clips/{clip_id}/restore")
    def restore(clip_id: UUID, body: RestoreRequest):
        return catalog.restore(str(clip_id), body.version, body.current_version)

    @app.post("/api/clips/{clip_id}/exports", status_code=202)
    def export_create(clip_id: UUID, body: EmptyRequest):
        return jobs.enqueue(str(clip_id))

    @app.get("/api/jobs")
    def jobs_list():
        return jobs.list()

    @app.get("/api/jobs/{job_id}")
    def job_get(job_id: UUID):
        return jobs.get(str(job_id))

    @app.api_route("/api/jobs/{job_id}/file", methods=["GET", "HEAD"])
    def job_download(job_id: UUID):
        return FileResponse(
            jobs.file(str(job_id)), media_type="video/mp4", filename=f"exais-{job_id}.mp4"
        )

    @app.get("/api/tags")
    def tags():
        return catalog.tags()

    @app.get("/api/playlists")
    def playlists_list():
        return catalog.list_entities("playlists")

    @app.post("/api/playlists", status_code=201)
    def playlist_create(body: PlaylistCreate):
        return catalog.create_entity("playlists", body.model_dump())

    @app.get("/api/playlists/{playlist_id}")
    def playlist_get(playlist_id: UUID):
        return catalog.playlist(str(playlist_id))

    @app.put("/api/playlists/{playlist_id}/clips")
    def playlist_set(playlist_id: UUID, body: PlaylistItems):
        return catalog.set_playlist(str(playlist_id), [str(item) for item in body.clip_ids])

    @app.get("/api/clips/{clip_id}/kinovea")
    def kinovea_status(clip_id: UUID):
        sidecar = catalog.sidecar(str(clip_id))
        return {
            **kinovea.capability(settings.kinovea_executable),
            "has_annotations": sidecar is not None,
            "launch_url": f"/api/clips/{clip_id}/kinovea/launch",
            "annotations_url": f"/api/clips/{clip_id}/kinovea/annotations",
        }

    @app.post("/api/clips/{clip_id}/kinovea/launch")
    def kinovea_launch(clip_id: UUID, body: EmptyRequest):
        available = kinovea.capability(settings.kinovea_executable)
        if not available["available"]:
            raise CatalogError(
                "kinovea_unavailable", available.get("reason", "Kinovea unavailable"), 503
            )
        original = catalog.source_path(str(clip_id))
        workspace = settings.path(f"reviews/{clip_id}/native")
        workspace.mkdir(parents=True, exist_ok=True)
        video = workspace / ("clip" + original.suffix)
        source_hash = catalog.get_clip(str(clip_id))["source_sha256"]
        if file_sha256(original) != source_hash:
            raise CatalogError("source_changed", "The original no longer matches its recorded hash")
        if video.exists():
            if file_sha256(video) != source_hash:
                raise CatalogError("review_copy_changed", "The native review copy has changed", 409)
        else:
            published = _copy_review_file(original, video, source_hash)
            if not published and file_sha256(video) != source_hash:
                raise CatalogError("review_copy_changed", "The native review copy has changed", 409)
        sidecar = catalog.sidecar(str(clip_id))
        annotation = video.with_suffix(".kva")
        # A native reviewer may have unsaved/newer edits; never replace their working sidecar.
        if sidecar and not annotation.exists():
            _copy_review_file(settings.path(sidecar["path"]), annotation, sidecar["sha256"])
        kinovea.launch(settings.kinovea_executable, video)
        return {
            "launched": True,
            "message": "Review the copy in Kinovea; import its saved KVA afterward",
        }

    @app.post("/api/clips/{clip_id}/kinovea/annotations", status_code=201)
    async def annotations_upload(clip_id: UUID, file: UploadFile = File(...)):
        try:
            if Path(file.filename or "").suffix.lower() != ".kva":
                raise CatalogError("invalid_sidecar", "Choose a Kinovea .kva annotation file", 415)
            content = await file.read(KVA_LIMIT + 1)
            if len(content) > KVA_LIMIT:
                raise CatalogError("sidecar_size", "Annotation files must be at most 2 MiB", 413)
            summary = await run_in_threadpool(kinovea.validate_kva, content)
            return await run_in_threadpool(catalog.save_sidecar, str(clip_id), content, summary)
        finally:
            await file.close()

    @app.get("/api/clips/{clip_id}/kinovea/annotations")
    def annotations_download(clip_id: UUID):
        sidecar = catalog.sidecar(str(clip_id))
        if not sidecar or not settings.path(sidecar["path"]).is_file():
            raise CatalogError("annotations_missing", "No annotation file has been imported", 404)
        return FileResponse(
            settings.path(sidecar["path"]), media_type="application/xml", filename=f"{clip_id}.kva"
        )

    @app.get("/api/clips/{clip_id}/analysis")
    def analysis_list(clip_id: UUID):
        return catalog.analyses(str(clip_id))

    @app.post("/api/clips/{clip_id}/analysis", status_code=201)
    def analysis_create(clip_id: UUID, body: dict):
        clip = catalog.get_clip(str(clip_id))
        validated = football_cv.validate_positions(body, clip["source_sha256"])
        return catalog.add_analysis(str(clip_id), validated)

    @app.patch("/api/clips/{clip_id}/analysis/{analysis_id}")
    def analysis_review(clip_id: UUID, analysis_id: UUID, body: AnalysisReview):
        return catalog.review_analysis(str(clip_id), str(analysis_id), body.status)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
