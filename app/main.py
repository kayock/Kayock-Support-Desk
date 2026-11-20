import io
import shutil
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, DATA_DIR, DOCUMENTS_DIR, ensure_data_dirs
from app.db import db_cursor, init_db
from app.services import (
    ai_manager,
    automotive_sources,
    chat_service,
    device_service,
    diagnostic_service,
    diagnostics,
    file_picker,
    indexing,
    job_queue,
    network_info,
    query_service,
    settings_service,
    spec_service,
    text_index,
    vehicle,
)

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB per file, generous for manuals

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_data_dirs()
    init_db()
    settings_service.ensure_defaults()
    # Recover jobs abandoned by a previous crashed/killed run, then start
    # the persistent background worker (indexing/reindexing now flow
    # through the queue so they're tracked, retried, and cancellable).
    job_queue.recover_stale_running_jobs()
    job_queue.ensure_worker_started()
    # One-time additive migration: installations indexed before the text
    # index existed get their chunk texts copied out of Chroma (no
    # re-upload, no re-embedding) so keyword search covers the whole
    # existing library.
    try:
        text_index.backfill_from_vector_store()
    except Exception:  # noqa: BLE001 -- a backfill hiccup must never block startup
        pass
    ai_manager.ensure_idle_watcher_started()
    yield
    # Stop only the AI processes Kayock itself started this session.
    # Synchronous (not the background-thread stop_ai()) so shutdown
    # actually waits for it instead of racing interpreter exit.
    ai_manager.stop_ai_sync()


app = FastAPI(title="Kayock Support Desk", lifespan=lifespan)

APP_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


# ---------------------------------------------------------------- pages ----

@app.get("/")
def home(request: Request):
    devices = device_service.list_devices()
    setup_needed = settings_service.get("ai_setup_complete") != "1"
    return templates.TemplateResponse(
        request, "index.html", {"devices": devices, "setup_needed": setup_needed}
    )


@app.get("/devices/new")
def new_device_page(request: Request):
    return templates.TemplateResponse(request, "add_device.html", {})


@app.get("/devices/{device_id}")
def device_page(request: Request, device_id: int):
    device = device_service.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    is_vehicle = vehicle.is_vehicle_device(device.get("device_type", ""))
    vehicle_profile = vehicle.vehicle_profile_line(device) if is_vehicle else ""
    online_sources = {
        "enabled": automotive_sources.online_sources_enabled(),
        "vin_lookup_enabled": automotive_sources.vin_lookup_enabled(),
        "recalls_enabled": automotive_sources.recalls_enabled(),
    }
    recalls_last_checked = None
    if is_vehicle:
        recalls_last_checked = automotive_sources.recalls_last_checked(
            device.get("vehicle_make", ""), device.get("vehicle_model", ""), device.get("vehicle_year", "")
        )
    return templates.TemplateResponse(
        request,
        "device.html",
        {
            "device": device,
            "is_vehicle": is_vehicle,
            "vehicle_profile": vehicle_profile,
            "online_sources": online_sources,
            "recalls_last_checked": recalls_last_checked,
        },
    )


@app.get("/settings")
def settings_page(request: Request):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "settings": settings_service.get_all(),
            "lan_url": network_info.get_lan_url(),
            "hostname_url": network_info.get_hostname_url(),
        },
    )


@app.get("/ai")
def ai_page(request: Request):
    return templates.TemplateResponse(request, "ai_status.html", {"settings": settings_service.get_all()})


@app.get("/ai/setup")
def ai_setup_page(request: Request):
    return templates.TemplateResponse(request, "ai_setup.html", {"settings": settings_service.get_all()})


@app.get("/documents/{document_id}/view")
def document_viewer_page(request: Request, document_id: int):
    """Locally served manual viewer (bundled pdf.js -- no CDN). Citations
    in answers link here with ?page=N&q=<excerpt> for click-to-page
    navigation and best-effort text highlighting. The manual file itself
    stays on this machine, served only through /api/documents/{id}/file."""
    doc = device_service.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return templates.TemplateResponse(
        request,
        "viewer.html",
        {
            "document_id": document_id,
            "filename": doc["original_filename"],
            "q": request.query_params.get("q", ""),
            "page": request.query_params.get("page", "1"),
        },
    )


# --------------------------------------------------------------- helpers ----

def _validate_upload(upload: UploadFile) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    return suffix


def _save_upload(upload: UploadFile, suffix: str) -> tuple[str, Path]:
    stored_filename = f"{uuid.uuid4().hex}{suffix}"
    dest = DOCUMENTS_DIR / stored_filename
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="File too large (limit 100MB).")
            out.write(chunk)
    return stored_filename, dest


# ------------------------------------------------------------- detect API ----

@app.post("/api/detect")
async def api_detect(file: UploadFile = File(...)):
    """Uploads a single manual to a temp location and returns best-guess
    device metadata for the confirm-or-edit form. The temp file is cleaned
    up immediately after -- the real upload happens again on device
    creation, since the browser still holds the File object."""
    suffix = _validate_upload(file)
    tmp_path = DATA_DIR / f"_detect_{uuid.uuid4().hex}{suffix}"
    try:
        with tmp_path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        info = indexing.detect_device_info(tmp_path, file.filename or "manual")
    finally:
        tmp_path.unlink(missing_ok=True)
    return JSONResponse(info)


# ------------------------------------------------------------ devices API ----

@app.post("/api/devices")
async def api_create_device(
    background_tasks: BackgroundTasks,
    name: str = Form(...),
    manufacturer: str = Form(""),
    model_number: str = Form(""),
    device_type: str = Form(""),
    notes: str = Form(""),
    vehicle_year: str = Form(""),
    vehicle_make: str = Form(""),
    vehicle_model: str = Form(""),
    vehicle_trim: str = Form(""),
    vehicle_engine: str = Form(""),
    vehicle_drivetrain: str = Form(""),
    vin: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Device name is required.")
    vehicle_fields = {
        "vehicle_year": vehicle_year, "vehicle_make": vehicle_make, "vehicle_model": vehicle_model,
        "vehicle_trim": vehicle_trim, "vehicle_engine": vehicle_engine, "vehicle_drivetrain": vehicle_drivetrain,
        "vin": vehicle.normalize_vin(vin),
    }
    device_id = device_service.create_device(name, manufacturer, model_number, device_type, notes, vehicle_fields)
    _handle_uploads(device_id, files, background_tasks)
    return JSONResponse({"id": device_id})


@app.put("/api/devices/{device_id}")
async def api_update_device(device_id: int, request: Request):
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Device name is required.")
    vehicle_fields = {key: body.get(key, "") for key in vehicle.VEHICLE_FIELDS}
    if "vin" in vehicle_fields:
        vehicle_fields["vin"] = vehicle.normalize_vin(vehicle_fields["vin"])
    device_service.update_device(
        device_id,
        name,
        body.get("manufacturer", ""),
        body.get("model_number", ""),
        body.get("device_type", ""),
        body.get("notes", ""),
        vehicle_fields,
    )
    return JSONResponse({"ok": True})


@app.post("/api/devices/{device_id}/answer-level")
async def api_update_answer_level(device_id: int, request: Request):
    """Updates just the Beginner/Experienced preference. Deliberately a
    separate, lightweight endpoint from the full device edit -- this is
    meant to be flipped often, and never touches documents/embeddings,
    so it never triggers a reindex."""
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json()
    level = (body.get("answer_level") or "").strip().lower()
    if level not in vehicle.ANSWER_LEVELS:
        raise HTTPException(status_code=400, detail=f"answer_level must be one of {vehicle.ANSWER_LEVELS}")
    device_service.update_answer_level(device_id, level)
    return JSONResponse({"ok": True})


@app.delete("/api/devices/{device_id}")
def api_delete_device(device_id: int):
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    device_service.delete_device(device_id)
    return JSONResponse({"ok": True})


@app.post("/api/devices/{device_id}/documents")
async def api_add_documents(
    device_id: int,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
):
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    doc_ids = _handle_uploads(device_id, files, background_tasks)
    return JSONResponse({"document_ids": doc_ids})


def _handle_uploads(device_id: int, files: list[UploadFile], background_tasks: BackgroundTasks) -> list[int]:
    doc_ids = []
    for upload in files:
        if not upload.filename:
            continue
        suffix = _validate_upload(upload)
        stored_filename, dest_path = _save_upload(upload, suffix)
        doc_id = device_service.add_document_record(device_id, upload.filename, stored_filename)
        doc_ids.append(doc_id)
        # Indexing flows through the persistent job queue (progress,
        # retries, cancellation, restart-safe) instead of an untracked
        # background task. Endpoint behavior is unchanged.
        job_queue.enqueue(
            "index_document",
            {
                "document_id": doc_id,
                "device_id": device_id,
                "file_path": str(dest_path),
                "original_filename": upload.filename,
                "prior_status": "pending",
            },
            total=1,
        )
    return doc_ids


@app.get("/api/documents/{document_id}/status")
def api_document_status(document_id: int):
    doc = device_service.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    payload = {
        "status": doc["status"],
        "page_count": doc["page_count"],
        "chunk_count": doc["chunk_count"],
        "error": doc["error"],
    }
    # Include the tracking job's progress when one is queued/running for
    # this document, so the UI can show real progress instead of a spinner.
    job = None
    with db_cursor() as cur:
        cur.execute(
            """SELECT id, status, progress, total FROM jobs
               WHERE job_type = 'index_document'
                 AND json_extract(payload_json, '$.document_id') = ?
               ORDER BY id DESC LIMIT 1""",
            (document_id,),
        )
        row = cur.fetchone()
        if row:
            job = dict(row)
    payload["job"] = job
    return JSONResponse(payload)


@app.get("/api/documents/{document_id}/file")
def api_document_file(document_id: int):
    """Serves the originally uploaded manual file for the built-in PDF
    viewer. Local-only by design: the app itself already has no auth gate
    (home-network deployment, see README), this endpoint adds no new
    exposure beyond what /api/export already provides, and the file never
    leaves the machine running Kayock."""
    doc = device_service.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    path = DOCUMENTS_DIR / doc["stored_filename"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="The manual file is missing from the data directory.")
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain"
    return FileResponse(path, media_type=media_type, filename=doc["original_filename"])


@app.get("/api/documents/{document_id}/pages")
def api_document_pages(document_id: int, page: int = 0):
    """Stored chunk text for one page (or the whole document when page=0),
    used by the PDF viewer for best-effort text highlighting."""
    doc = device_service.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = text_index.get_document_chunks(document_id)
    if page:
        chunks = [c for c in chunks if c["page"] == page]
    return JSONResponse({"chunks": chunks})


@app.get("/api/devices/{device_id}/search")
def api_device_search(device_id: int, q: str = "", limit: int = 20):
    """Exact keyword search across a device's indexed manuals (BM25 when
    available). Complements Ask: precise lookups of trouble codes, fuse
    numbers, torque values, or part names without invoking the model."""
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    results = text_index.search(device_id, q, max(1, min(limit, 100)))
    return JSONResponse({"results": results})


@app.delete("/api/documents/{document_id}")
def api_delete_document(document_id: int):
    if device_service.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    device_service.delete_document(document_id)
    return JSONResponse({"ok": True})


@app.post("/api/devices/{device_id}/reindex")
def api_reindex_device(device_id: int, background_tasks: BackgroundTasks):
    """Rebuilds the Chroma index for any of this device's manuals that
    were flagged 'needs_reindex' (typically because the embedding model
    or server changed), re-reading from the originally saved file --
    no re-upload required. Runs through the persistent job queue."""
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")

    stale_docs = indexing.documents_needing_reindex(device_id)
    doc_ids = []
    for doc in stale_docs:
        file_path = DOCUMENTS_DIR / doc["stored_filename"]
        if not file_path.exists():
            with_error = (
                "Original file is missing from the data directory and can't be reindexed. "
                "Remove this manual and upload it again."
            )
            device_service.mark_document_error(doc["id"], with_error)
            continue
        doc_ids.append(doc["id"])
        job_queue.enqueue(
            "index_document",
            {
                "document_id": doc["id"],
                "device_id": device_id,
                "file_path": str(file_path),
                "original_filename": doc["original_filename"],
                "prior_status": "needs_reindex",
            },
            total=1,
        )
    return JSONResponse({"document_ids": doc_ids})


# --------------------------------------------------------------- ask API ----

@app.post("/api/devices/{device_id}/ask")
async def api_ask(device_id: int, request: Request):
    device = device_service.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")
    result = query_service.answer_question(device, question)
    return JSONResponse(result)


@app.post("/api/devices/{device_id}/diagnose")
async def api_diagnose(device_id: int, request: Request):
    """OBD-II code diagnosis: one structured, manual-scoped answer per
    code. Manual code entry only -- no scanner integration."""
    device = device_service.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json()
    raw_codes = body.get("codes", "")
    codes = vehicle.normalize_obd_codes(raw_codes)
    if not codes:
        raise HTTPException(status_code=400, detail="Enter at least one OBD-II code.")
    results = query_service.diagnose_codes(device, codes)
    return JSONResponse({"results": results})


# ------------------------------------------------- online automotive API ----

@app.post("/api/devices/{device_id}/decode-vin")
async def api_decode_vin(device_id: int, request: Request):
    """Decodes the device's saved VIN via NHTSA vPIC. Read-only -- never
    writes to the device profile itself; the frontend shows the result
    (and any conflicts with existing fields) and applies chosen values
    via the normal PUT /api/devices/{id} endpoint, so nothing is ever
    silently overwritten."""
    device = device_service.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json() if await request.body() else {}
    vin = (body.get("vin") or device.get("vin") or "").strip()
    if not vin:
        raise HTTPException(status_code=400, detail="This device has no VIN saved yet.")
    force_refresh = bool(body.get("force_refresh"))
    result = automotive_sources.decode_vin(vin, force_refresh=force_refresh)

    if result.get("ok"):
        current = {key: device.get(key, "") for key in vehicle.VEHICLE_FIELDS if key != "vin"}
        suggested = result.get("suggested_fields", {})
        conflicts = []
        for field, suggested_value in suggested.items():
            current_value = current.get(field, "")
            if current_value and current_value.strip() != suggested_value.strip():
                conflicts.append({"field": field, "current": current_value, "suggested": suggested_value})
        result["current_fields"] = current
        result["conflicts"] = conflicts

    return JSONResponse(result)


@app.post("/api/devices/{device_id}/recalls")
async def api_recalls(device_id: int, request: Request):
    """Checks NHTSA recalls for the device's year/make/model. Cached (see
    automotive_sources); pass {"force_refresh": true} to bypass the cache
    and check again."""
    device = device_service.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json() if await request.body() else {}
    force_refresh = bool(body.get("force_refresh"))
    result = automotive_sources.get_recalls(
        device.get("vehicle_make", ""), device.get("vehicle_model", ""),
        device.get("vehicle_year", ""), force_refresh=force_refresh,
    )
    return JSONResponse(result)


@app.get("/api/devices/{device_id}/recalls/last-checked")
def api_recalls_last_checked(device_id: int):
    device = device_service.get_device(device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    last_checked = automotive_sources.recalls_last_checked(
        device.get("vehicle_make", ""), device.get("vehicle_model", ""), device.get("vehicle_year", "")
    )
    return JSONResponse({"last_checked": last_checked})


# ---------------------------------------------------------- settings API ----

@app.post("/api/settings")
async def api_update_settings(request: Request):
    body = await request.json()
    settings_service.update(body)
    # If the embedding server/model changed, any manuals indexed under the
    # old settings are now stale -- mark them so they're excluded from
    # search until reindexed, rather than silently mixing embeddings from
    # two different models.
    marked = indexing.mark_stale_ready_documents()
    return JSONResponse({"ok": True, "documents_marked_needs_reindex": marked})


# ---------------------------------------------------------------- AI API ----

@app.get("/api/ai/status")
def api_ai_status():
    return JSONResponse(ai_manager.get_status())


@app.post("/api/ai/start")
def api_ai_start():
    ai_manager.start_ai()
    return JSONResponse(ai_manager.get_status())


@app.post("/api/ai/stop")
def api_ai_stop():
    ai_manager.stop_ai()
    return JSONResponse(ai_manager.get_status())


@app.post("/api/ai/validate-path")
async def api_ai_validate_path(request: Request):
    body = await request.json()
    raw_path = (body.get("path") or "").strip()
    if not raw_path:
        return JSONResponse({"exists": False, "is_file": False, "size_bytes": None})
    p = Path(raw_path)
    exists = p.exists()
    return JSONResponse(
        {
            "exists": exists,
            "is_file": exists and p.is_file(),
            "size_bytes": p.stat().st_size if exists and p.is_file() else None,
        }
    )


@app.post("/api/ai/pick-file")
async def api_ai_pick_file(request: Request):
    """Opens a native file-selection dialog on the Kayock host machine and
    returns the chosen absolute path. Never uploads/copies the file
    itself. Degrades to {"available": false} if no dialog could be shown
    (e.g. headless server) -- the setup page falls back to its text
    field in that case."""
    body = await request.json()
    kind = body.get("kind", "")
    if kind not in file_picker.FILE_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown picker kind: {kind}")
    result = file_picker.open_file_dialog(kind)
    return JSONResponse(result)


@app.post("/api/ai/setup")
async def api_ai_setup(request: Request, background_tasks: BackgroundTasks):
    """Saves the managed-AI paths chosen in the first-run wizard (or from
    the AI page's 'change model' links) and, if requested, kicks off
    Start AI right away."""
    body = await request.json()
    values = {
        "ai_mode": "managed",
        "ai_setup_complete": "1",
        "chat_model_path": (body.get("chat_model_path") or "").strip(),
    }
    if body.get("llama_server_path") is not None:
        values["llama_server_path"] = body.get("llama_server_path", "").strip()
    if body.get("ollama_path") is not None:
        values["ollama_path"] = body.get("ollama_path", "").strip()
    settings_service.update(values)

    if body.get("start", True):
        ai_manager.start_ai()

    return JSONResponse({"ok": True})


@app.post("/api/ai/skip-setup")
def api_ai_skip_setup():
    """Lets someone dismiss the first-run wizard without picking models --
    they can still use Advanced/External settings or come back later."""
    settings_service.update({"ai_setup_complete": "1"})
    return JSONResponse({"ok": True})


@app.post("/api/ai/mode")
async def api_ai_mode(request: Request):
    body = await request.json()
    mode = body.get("mode")
    if mode not in ("managed", "external"):
        raise HTTPException(status_code=400, detail="mode must be 'managed' or 'external'")
    settings_service.update({"ai_mode": mode})
    return JSONResponse({"ok": True})


# -------------------------------------------------------- chat sessions API ----

@app.post("/api/devices/{device_id}/chat/sessions")
async def api_create_chat_session(device_id: int, request: Request):
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json() if await request.body() else {}
    session_id = chat_service.create_session(device_id, body.get("title") or "")
    return JSONResponse({"id": session_id})


@app.get("/api/devices/{device_id}/chat/sessions")
def api_list_chat_sessions(device_id: int):
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return JSONResponse({"sessions": chat_service.list_sessions(device_id)})


@app.get("/api/chat/sessions/{session_id}")
def api_get_chat_session(session_id: int):
    session = chat_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(session)


@app.patch("/api/chat/sessions/{session_id}")
async def api_rename_chat_session(session_id: int, request: Request):
    body = await request.json()
    if chat_service.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    chat_service.rename_session(session_id, body.get("title") or "")
    return JSONResponse({"ok": True})


@app.delete("/api/chat/sessions/{session_id}")
def api_delete_chat_session(session_id: int):
    if chat_service.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    chat_service.delete_session(session_id)
    return JSONResponse({"ok": True})


@app.post("/api/chat/sessions/{session_id}/ask")
async def api_chat_ask(session_id: int, request: Request):
    """Asks inside a persisted chat session: conversation history gives
    the model follow-up meaning, while retrieval + verification run fresh
    on this turn's evidence exactly as in the plain ask flow."""
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question is required.")
    session = chat_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    device = device_service.get_device(session["device_id"])
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    try:
        result = chat_service.ask_in_session(device, session_id, question)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return JSONResponse(result)


# ------------------------------------------------- diagnostic sessions API ----

@app.post("/api/devices/{device_id}/diag/sessions")
async def api_create_diag_session(device_id: int, request: Request):
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    body = await request.json() if await request.body() else {}
    session_id = diagnostic_service.create_session(device_id, body.get("title") or "", body.get("symptoms") or "")
    return JSONResponse({"id": session_id})


@app.get("/api/devices/{device_id}/diag/sessions")
def api_list_diag_sessions(device_id: int):
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return JSONResponse({"sessions": diagnostic_service.list_sessions(device_id)})


@app.get("/api/diag/sessions/{session_id}")
def api_get_diag_session(session_id: int):
    session = diagnostic_service.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return JSONResponse(session)


@app.patch("/api/diag/sessions/{session_id}")
async def api_update_diag_session(session_id: int, request: Request):
    if diagnostic_service.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    body = await request.json()
    try:
        diagnostic_service.update_session(
            session_id,
            title=body.get("title"),
            symptoms=body.get("symptoms"),
            status=body.get("status"),
            outcome=body.get("outcome"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse({"ok": True})


@app.delete("/api/diag/sessions/{session_id}")
def api_delete_diag_session(session_id: int):
    if diagnostic_service.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    diagnostic_service.delete_session(session_id)
    return JSONResponse({"ok": True})


@app.post("/api/diag/sessions/{session_id}/steps")
async def api_add_diag_step(session_id: int, request: Request):
    if diagnostic_service.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    body = await request.json()
    description = (body.get("description") or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="Step description is required.")
    try:
        step_id = diagnostic_service.add_step(
            session_id, description, body.get("reading") or "", body.get("result") or "", body.get("notes") or ""
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse({"id": step_id})


@app.patch("/api/diag/steps/{step_id}")
async def api_update_diag_step(step_id: int, request: Request):
    body = await request.json()
    try:
        diagnostic_service.update_step(
            step_id,
            description=body.get("description"),
            reading=body.get("reading"),
            result=body.get("result"),
            notes=body.get("notes"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return JSONResponse({"ok": True})


@app.delete("/api/diag/steps/{step_id}")
def api_delete_diag_step(step_id: int):
    diagnostic_service.delete_step(step_id)
    return JSONResponse({"ok": True})


# ------------------------------------------------------- specs API ----

@app.get("/api/devices/{device_id}/specs")
def api_device_specs(device_id: int, q: str = "", limit: int = 100):
    """Searchable structured specifications (torque, fluids, capacities,
    fuses, schedules) extracted from this device's manuals. Every row
    carries document/page/section references; 'verified' rows cleared the
    component+property verification layers, 'candidate' rows are pattern
    matches only -- the two are never blended."""
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    results = spec_service.search_specs(device_id, q, max(1, min(limit, 500)))
    return JSONResponse({"specs": results})


@app.post("/api/devices/{device_id}/specs/extract")
def api_device_specs_extract(device_id: int):
    """Re-runs structured extraction over every ready manual of the device
    (e.g. after a chunking/extraction upgrade). Additive -- it never
    deletes existing values, only adds new ones."""
    if device_service.get_device(device_id) is None:
        raise HTTPException(status_code=404, detail="Device not found")
    rows = spec_service.extract_specs_for_device(device_id)
    return JSONResponse({"added": rows})


# ------------------------------------------------- diagnostics / jobs API ----

@app.get("/api/diagnostics")
def api_diagnostics():
    """Local system diagnostics snapshot (Settings page). Read-only --
    reports Python/venv, data-dir writability, GUI/picker availability,
    llama-server/Ollama discovery, GPU presence (informational only), log
    files, and AI status. Never installs or changes anything."""
    return JSONResponse(diagnostics.collect_diagnostics())


@app.get("/api/metrics")
def api_metrics():
    """Aggregate retrieval/verification counters (Settings diagnostics
    panel): questions answered, keyword-hit usage, embedding fallbacks,
    and average retrieval latency."""
    from app.db import get_metrics

    metrics = get_metrics()
    answered = metrics.get("questions_answered", 0) or 0
    latency_sum = metrics.get("retrieval_latency_ms_sum", 0) or 0
    return JSONResponse(
        {
            "metrics": metrics,
            "avg_retrieval_ms": round(latency_sum / answered, 1) if answered else None,
        }
    )


@app.get("/api/jobs")
def api_jobs(limit: int = 50):
    """Recent background jobs (indexing/reindexing) with status, progress
    and any error -- the Settings page's job history panel."""
    return JSONResponse({"jobs": job_queue.list_jobs(limit)})


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: int):
    job = job_queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] not in ("queued", "running"):
        return JSONResponse({"ok": False, "detail": "Job already finished."})
    ok = job_queue.request_cancel(job_id)
    return JSONResponse({"ok": ok})


# ---------------------------------------------------------- network / QR ----

@app.get("/api/network")
def api_network():
    return JSONResponse(
        {"lan_url": network_info.get_lan_url(), "hostname_url": network_info.get_hostname_url()}
    )


@app.get("/api/qrcode.png")
def api_qrcode():
    png_bytes = network_info.generate_qr_png(network_info.get_lan_url())
    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")


# ------------------------------------------------------------- data export ----

@app.get("/api/export")
def api_export():
    """Zips up the entire data directory (devices DB, manuals, vector
    index) for a simple manual backup."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in DATA_DIR.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(DATA_DIR)))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=kayock-backup.zip"},
    )
