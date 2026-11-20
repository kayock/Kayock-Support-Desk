import shutil

from app.config import DOCUMENTS_DIR
from app.db import db_cursor
from app.services import vector_store
from app.services.vehicle import VEHICLE_FIELDS


def list_devices() -> list[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM devices ORDER BY created_at DESC")
        devices = [dict(row) for row in cur.fetchall()]
        for d in devices:
            cur.execute("SELECT COUNT(*) AS c FROM documents WHERE device_id = ?", (d["id"],))
            d["document_count"] = cur.fetchone()["c"]
    return devices


def get_device(device_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
        row = cur.fetchone()
        if row is None:
            return None
        device = dict(row)
        cur.execute(
            "SELECT * FROM documents WHERE device_id = ? ORDER BY created_at DESC",
            (device_id,),
        )
        device["documents"] = [dict(r) for r in cur.fetchall()]
    return device


def create_device(name: str, manufacturer: str = "", model_number: str = "",
                   device_type: str = "", notes: str = "", vehicle_fields: dict | None = None) -> int:
    vf = _clean_vehicle_fields(vehicle_fields)
    columns = ["name", "manufacturer", "model_number", "device_type", "notes", *VEHICLE_FIELDS]
    values = [name.strip(), manufacturer.strip(), model_number.strip(), device_type.strip(), notes.strip(),
              *[vf[key] for key in VEHICLE_FIELDS]]
    placeholders = ", ".join("?" for _ in columns)
    with db_cursor() as cur:
        cur.execute(
            f"INSERT INTO devices ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        return cur.lastrowid


def update_device(device_id: int, name: str, manufacturer: str = "", model_number: str = "",
                   device_type: str = "", notes: str = "", vehicle_fields: dict | None = None) -> None:
    vf = _clean_vehicle_fields(vehicle_fields)
    columns = ["name", "manufacturer", "model_number", "device_type", "notes", *VEHICLE_FIELDS]
    values = [name.strip(), manufacturer.strip(), model_number.strip(), device_type.strip(), notes.strip(),
              *[vf[key] for key in VEHICLE_FIELDS]]
    set_clause = ", ".join(f"{col} = ?" for col in columns)
    with db_cursor() as cur:
        cur.execute(f"UPDATE devices SET {set_clause} WHERE id = ?", (*values, device_id))


def update_answer_level(device_id: int, answer_level: str) -> None:
    """Updates only the answer-detail preference (Beginner/Experienced).
    Touches nothing about documents/embeddings -- never triggers a
    reindex, since it has no bearing on how anything was indexed."""
    with db_cursor() as cur:
        cur.execute("UPDATE devices SET answer_level = ? WHERE id = ?", (answer_level, device_id))


def _clean_vehicle_fields(vehicle_fields: dict | None) -> dict:
    vehicle_fields = vehicle_fields or {}
    return {key: (vehicle_fields.get(key) or "").strip() for key in VEHICLE_FIELDS}


def delete_device(device_id: int) -> None:
    device = get_device(device_id)
    if device is None:
        return
    for doc in device["documents"]:
        _delete_stored_file(doc["stored_filename"])
    vector_store.delete_device(device_id)
    with db_cursor() as cur:
        cur.execute("DELETE FROM devices WHERE id = ?", (device_id,))


def add_document_record(device_id: int, original_filename: str, stored_filename: str) -> int:
    with db_cursor() as cur:
        cur.execute(
            """INSERT INTO documents (device_id, original_filename, stored_filename, status)
               VALUES (?, ?, ?, 'pending')""",
            (device_id, original_filename, stored_filename),
        )
        return cur.lastrowid


def get_document(document_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM documents WHERE id = ?", (document_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def mark_document_error(document_id: int, error: str) -> None:
    with db_cursor() as cur:
        cur.execute(
            "UPDATE documents SET status = 'error', error = ? WHERE id = ?",
            (error, document_id),
        )


def delete_document(document_id: int) -> None:
    doc = get_document(document_id)
    if doc is None:
        return
    vector_store.delete_document(document_id)
    _delete_stored_file(doc["stored_filename"])
    with db_cursor() as cur:
        cur.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def _delete_stored_file(stored_filename: str) -> None:
    path = DOCUMENTS_DIR / stored_filename
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
