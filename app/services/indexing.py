"""
Document parsing + indexing pipeline.

Parsing: pypdf (PDF) / plain text read (txt, md).
Chunking: langchain-text-splitters' RecursiveCharacterTextSplitter.
Embedding: app.services.llm_client (OpenAI-compatible, e.g. Ollama).
Storage/retrieval: app.services.vector_store (Chroma).

This module just wires those proven pieces together -- it does not
reimplement parsing, chunking, embeddings, or vector search.
"""
from pathlib import Path
import re

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from pypdf.errors import DependencyError, FileNotDecryptedError, PdfReadError

from app.config import CHUNK_OVERLAP, CHUNK_SIZE
from app.db import db_cursor
from app.services import llm_client, settings_service, text_index, vector_store
from app.services.logging_service import get_logger
from app.services.llm_client import LLMError


log = get_logger("indexing")


class IndexingError(RuntimeError):
    """A clean, user-facing indexing failure -- the message is safe to
    show directly in the UI, unlike a raw exception string."""


PASSWORD_REQUIRED_MESSAGE = (
    "This PDF is password-protected and requires a password to open. "
    "Remove the password (most PDF editors, or a free tool like qpdf, can do this) "
    "and re-upload it."
)


def extract_pages(path: Path) -> list[tuple[int, str]]:
    """Returns a list of (page_number_starting_at_1, text)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            reader = PdfReader(str(path))
        except PdfReadError as exc:
            raise IndexingError(f"This PDF could not be read (it may be corrupted): {exc}") from exc

        if reader.is_encrypted:
            # Many factory/technical PDFs (e.g. vehicle service manuals) are
            # AES-encrypted purely to restrict editing/printing, with no
            # user password required to open and read them. An empty-string
            # decrypt succeeds for those. If it doesn't, a real password is
            # genuinely required and we can't guess it.
            try:
                result = reader.decrypt("")
            except DependencyError as exc:
                # Should not happen with pypdf[crypto] installed, but if it
                # ever does, never leak the raw dependency error to the user.
                raise IndexingError(
                    "This PDF uses encryption Kayock couldn't process. Please try again "
                    "or contact support if this keeps happening."
                ) from exc
            if not result:
                raise IndexingError(PASSWORD_REQUIRED_MESSAGE)

        pages = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except FileNotDecryptedError as exc:
                raise IndexingError(PASSWORD_REQUIRED_MESSAGE) from exc
            except Exception:
                text = ""
            pages.append((i, text))

        # Scanned-PDF OCR hook (optional): if the PDF has no text layer at
        # all, try OCR when the optional pieces are installed. Without them,
        # fall through to the standard clear error message.
        if pages and not any(text.strip() for _, text in pages):
            try:
                from app.services.extensions import ocr

                pages = ocr.ocr_pages(path)
            except RuntimeError as exc:
                # OCR unavailable or failed: raise the honest message
                raise IndexingError(str(exc)) from exc
            except Exception as exc:  # noqa: BLE001 -- never leak a raw stack to the UI
                raise IndexingError(
                    "This PDF has no extractable text (it is likely a scan), and OCR failed unexpectedly."
                ) from exc

        return pages
    elif suffix in (".txt", ".md"):
        text = path.read_text(errors="ignore")
        return [(1, text)]
    else:
        raise IndexingError(f"Unsupported file type: {suffix}")


# ---- heading detection -------------------------------------------------------
# Manual pages open sections with short label-like lines. Recognizing them
# lets every chunk carry its section heading, which improves citations
# ("page 12, 'Rear Differential'"), keyword search, and spec extraction.
# Deliberately conservative -- a false heading is worse than a missed one.

_HEADING_NUMBERED_RE = re.compile(r"^\s*(\d+(\.\d+){0,3})\s+\S.*$")
_TABLE_ROW_RE = re.compile(r"(\S {2,}\S){2,}|\t|\|")


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80:
        return False
    if _HEADING_NUMBERED_RE.match(stripped):
        return True
    letters = [ch for ch in stripped if ch.isalpha()]
    if len(letters) >= 4 and all(ch.isupper() for ch in letters):
        return True  # e.g. "REAR DIFFERENTIAL", "SPECIFICATIONS"
    # Short Title-ish line ending without sentence punctuation -- common
    # sub-heading layout ("Rear Differential", "Specifications", "Caution").
    # Lines containing digits are body text (spec values, table rows), never
    # headings -- otherwise OCR'd/extracted value lines would empty sections.
    if len(stripped) <= 60 and stripped[0].isalpha() and not stripped.endswith((".", ",", ";", ":")):
        if not any(ch.isdigit() for ch in stripped):
            words = stripped.split()
            if 1 <= len(words) <= 6 and not stripped.endswith("-"):
                return True
    return False


def _split_table_aware(text: str, splitter: RecursiveCharacterTextSplitter) -> list[str]:
    """Splits one section's text into chunks, keeping table rows intact.

    A line looks like a table row when it has several multi-space column
    gaps, a tab, or a pipe separator. Contiguous table lines form a table
    block which is split only at row boundaries (rows are packed into
    chunks under CHUNK_SIZE), never mid-row -- cutting a fuse table mid-row
    is exactly how fuse numbers get detached from their ratings."""
    lines = text.splitlines()
    blocks: list[tuple[bool, list[str]]] = []  # (is_table, lines)
    for line in lines:
        is_row = bool(_TABLE_ROW_RE.search(line))
        if blocks and blocks[-1][0] == is_row:
            blocks[-1][1].append(line)
        else:
            blocks.append([is_row, [line]])

    pieces: list[str] = []
    table_buf: list[str] = []

    def flush_table() -> None:
        nonlocal table_buf
        if not table_buf:
            return
        current: list[str] = []
        size = 0
        for row in table_buf:
            row_len = len(row) + 1
            if current and size + row_len > CHUNK_SIZE:
                pieces.append("\n".join(current))
                current, size = [], 0
            current.append(row)
            size += row_len
        if current:
            pieces.append("\n".join(current))
        table_buf = []

    for is_table, block_lines in blocks:
        if is_table:
            table_buf.extend(block_lines)
        else:
            flush_table()
            prose = "\n".join(block_lines).strip()
            if prose:
                pieces.extend(splitter.split_text(prose))
    flush_table()
    return [p.strip() for p in pieces if p.strip()]


def _page_sections(page_text: str) -> list[tuple[str, str]]:
    """Splits a page's text into (section_heading, text) runs. The page
    starts under an empty heading; each detected heading starts a new run
    whose title is the *accumulated path* (e.g. 'REAR DIFFERENTIAL >
    Torque specifications' when a heading follows another on one page).
    Headings are only ever short label lines -- anything else stays body
    text, so prose pages keep an empty section like before."""
    runs: list[tuple[str, list[str]]] = []
    current_path: list[str] = []
    for line in page_text.splitlines():
        if line.strip() and _looks_like_heading(line):
            current_path = [line.strip()]
            runs.append((current_path, []))
        else:
            if runs:
                runs[-1][1].append(line)
            else:
                runs.append((current_path, [line]))
    # Merge runs under the same accumulated heading path
    merged: list[tuple[str, str]] = []
    current_path: list[str] = []
    for path, lines in runs:
        if path:
            if current_path:
                current_path = [current_path[0], path[0]]
            else:
                current_path = path
        text = "\n".join(lines).strip()
        if not text and not path:
            continue
        merged.append((" > ".join(current_path) if current_path else "", text))
    return [m for m in merged if m[1]]


def chunk_pages(pages: list[tuple[int, str]], filename: str) -> list[dict]:
    """Chunks page text with heading/section and table-row awareness.

    Every chunk keeps its page, filename, and the section heading path it
    belongs to ('' when the page had no recognizable heading), so citations
    and structured extraction can attribute a value to 'page 12, section
    Rear Differential', not just a page number."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    )
    chunks = []
    for page_num, text in pages:
        text = text.strip()
        if not text:
            continue
        for section, section_text in _page_sections(text):
            for piece in _split_table_aware(section_text, splitter):
                piece = piece.strip()
                if piece:
                    # Prefix the section heading path onto the chunk text
                    # (standard contextual-chunk practice): embeddings,
                    # keyword search, and the component/property
                    # verification layers all benefit from the heading
                    # being part of the chunk, and citations can show it.
                    full_text = f"{section}\n{piece}" if section else piece
                    chunks.append(
                        {"text": full_text, "page": page_num, "filename": filename, "section": section}
                    )
    return chunks


def index_document(
    document_id: int,
    device_id: int,
    file_path: Path,
    original_filename: str,
    progress_cb=None,
    cancel_cb=None,
    prior_status: str = "",
) -> None:
    """Parses, chunks, embeds, and stores a document. Updates the documents
    row's status/page_count/chunk_count/error as it goes. Also used for
    re-indexing: any previously-stored vectors AND text-index rows for this
    document are cleared first, so this is safe to call again on the same
    document (e.g. after the embedding model changes).

    progress_cb(done, total) and cancel_cb() (raise to cancel) are
    supplied by the job queue when this runs as a tracked job; both are
    optional so direct calls keep working."""
    with db_cursor() as cur:
        cur.execute(
            "UPDATE documents SET status = 'indexing', error = '' WHERE id = ?",
            (document_id,),
        )
    # Clear out any vectors from a previous run (first-time index: no-op).
    vector_store.delete_document(document_id)
    text_index.delete_document(document_id)

    try:
        if cancel_cb is not None:
            cancel_cb()
        pages = extract_pages(file_path)
        non_empty_pages = [p for p in pages if p[1].strip()]
        chunks = chunk_pages(pages, original_filename)

        if not chunks:
            with db_cursor() as cur:
                cur.execute(
                    """UPDATE documents SET status = 'error', page_count = ?,
                       chunk_count = 0,
                       error = 'No extractable text found (the PDF may be scanned/image-based).'
                       WHERE id = ?""",
                    (len(pages), document_id),
                )
            return

        # Embed in batches to avoid overly large single requests, reporting
        # progress and honoring cancellation between batches.
        batch_size = 64
        total_batches = (len(chunks) + batch_size - 1) // batch_size
        all_embeddings: list[list[float]] = []
        for i in range(0, len(chunks), batch_size):
            if cancel_cb is not None:
                cancel_cb()
            batch = chunks[i : i + batch_size]
            embeddings = llm_client.embed_texts([c["text"] for c in batch])
            all_embeddings.extend(embeddings)
            if progress_cb is not None:
                progress_cb(len(all_embeddings), len(chunks))

        vector_store.add_chunks(document_id, device_id, chunks, all_embeddings)
        text_index.store_chunks(document_id, device_id, chunks)

        # Structured spec extraction (torque/fluid/capacity/fuse/schedule
        # candidates, with source refs). Best-effort: extraction problems
        # must never fail an otherwise-successful indexing run.
        try:
            from app.services import spec_service

            spec_service.extract_specs_for_document(device_id, document_id)
        except Exception:  # noqa: BLE001
            pass

        # Record which embedding model/server produced these vectors, so a
        # later settings change can detect this index is now stale instead
        # of silently mixing embeddings from different models.
        signature = settings_service.embedding_signature()

        with db_cursor() as cur:
            cur.execute(
                """UPDATE documents SET status = 'ready', page_count = ?,
                   chunk_count = ?, error = '', embedding_signature = ? WHERE id = ?""",
                (len(pages), len(chunks), signature, document_id),
            )
    except IndexingError as exc:
        # A clean, already user-facing message (bad/missing PDF text,
        # password-protected file, unsupported type, etc.) -- show it as-is.
        with db_cursor() as cur:
            cur.execute(
                "UPDATE documents SET status = 'error', error = ? WHERE id = ?",
                (str(exc), document_id),
            )
    except LLMError as exc:
        with db_cursor() as cur:
            cur.execute(
                "UPDATE documents SET status = 'error', error = ? WHERE id = ?",
                (str(exc), document_id),
            )
    except Exception as exc:  # noqa: BLE001
        log.error("indexing document %s failed: %s", document_id, exc)
        with db_cursor() as cur:
            cur.execute(
                "UPDATE documents SET status = 'error', error = ? WHERE id = ?",
                (f"Indexing failed: {exc}", document_id),
            )


def mark_stale_ready_documents() -> int:
    """Called after the embedding settings (base URL and/or model) change.
    Any document that was successfully indexed under a different embedding
    signature is marked 'needs_reindex' -- its old vectors stay in Chroma
    for now (harmless, just unused) but query_service excludes anything
    not in 'ready' status, so a stale document is never silently searched
    with mismatched embeddings. Returns how many documents were marked."""
    current = settings_service.embedding_signature()
    with db_cursor() as cur:
        cur.execute(
            """UPDATE documents SET status = 'needs_reindex'
               WHERE status = 'ready' AND embedding_signature != ?""",
            (current,),
        )
        return cur.rowcount


def documents_needing_reindex(device_id: int) -> list[dict]:
    """Documents worth re-running the index job for: those flagged
    'needs_reindex' (embedding model changed) AND those in 'error' state
    whose stored file still exists -- a transient failure (embedding
    server down during upload, a half-configured first run) should be
    retryable without deleting and re-uploading the manual. Documents
    whose file is missing are excluded (the reindex endpoint reports
    those with a clear message instead)."""
    with db_cursor() as cur:
        cur.execute(
            """SELECT id, device_id, stored_filename, original_filename
               FROM documents
               WHERE device_id = ? AND (status = 'needs_reindex' OR status = 'error')""",
            (device_id,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    from app.config import DOCUMENTS_DIR

    return [r for r in rows if (DOCUMENTS_DIR / r["stored_filename"]).exists()]


def detect_device_info(file_path: Path, original_filename: str) -> dict:
    """Best-effort auto-detection of manufacturer/model/device type from the
    first page or two of text, using the configured chat model. Falls back
    to empty strings (so the UI shows a plain form) if the LLM is
    unreachable or the response can't be parsed."""
    result = {"name": "", "manufacturer": "", "model_number": "", "device_type": ""}
    try:
        pages = extract_pages(file_path)
    except Exception:
        return result

    sample_text = "\n".join(t for _, t in pages[:2])[:4000].strip()
    if not sample_text:
        return result

    import json

    system_prompt = (
        "You extract structured product information from the first page(s) of an "
        "appliance/electronics/vehicle manual. Respond with ONLY a JSON object with "
        "these exact keys: name, manufacturer, model_number, device_type. "
        "'name' should be a short friendly name like 'Bosch Dishwasher'. "
        "If a field is unknown, use an empty string. No other text, no markdown fences."
    )
    try:
        raw = llm_client.chat(
            system_prompt,
            f"Filename: {original_filename}\n\nDocument text:\n{sample_text}",
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        data = json.loads(raw)
        for key in result:
            if isinstance(data.get(key), str):
                result[key] = data[key].strip()
    except Exception:
        # LLM unreachable, or returned non-JSON -- fall back cleanly to blank form
        pass
    return result
