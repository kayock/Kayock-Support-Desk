#!/usr/bin/env python3
"""Seeds demo data into a Kayock data directory (for UI visual checks):
a vehicle device with a multi-section manual, a chat session with two
turns, and a diagnostic session. The app must NOT be running when this
runs (it writes the SQLite DB directly, using the app's own services)."""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kayock_ui_check")
os.environ["KAYOCK_DATA_DIR"] = str(data_dir)

from app.db import init_db  # noqa: E402
from app.services import (  # noqa: E402
    chat_service,
    device_service,
    indexing,
    job_queue,
    llm_client,
    settings_service,
)
from app.services import diagnostic_service  # noqa: E402

init_db()
settings_service.ensure_defaults()

# Point at the fake model server started alongside (start_ui_check runs it
# via scripts/smoke_test-style handler; here we simply register the URL so
# seeds can embed + "answer").
os.environ["KAYOCK_FAKE_MODEL_PORT"] = "8791"


# Use the exact same embedding vocabulary as scripts/fake_model_server.py,
# so demo data and the demo model server produce compatible vector
# dimensions for Chroma.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_model_server import VOCAB  # noqa: E402


def fake_embed(texts):
    import math

    out = []
    for text in texts:
        lower = text.lower()
        v = [lower.count(w) for w in VOCAB]
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / norm for x in v])
    return out


llm_client.embed_texts = fake_embed


def fake_chat(system_prompt, user_prompt, temperature=0.1, messages=None):
    if "fill plug" in user_prompt.lower() and "Question: What about" in user_prompt:
        return "The fill plug torque is 39 Nm."
    return "The rear differential drain plug torque is 54 Nm."


llm_client.chat = fake_chat

device_id = device_service.create_device(
    "2017 Hyundai Tucson", "Hyundai", "TL 2.0 GDI", "Car",
    "Daily driver. Roof rack installed.",
    {"vehicle_year": "2017", "vehicle_make": "Hyundai", "vehicle_model": "Tucson",
     "vehicle_trim": "Limited", "vehicle_engine": "2.0L GDI", "vehicle_drivetrain": "AWD", "vin": ""},
)
device_service.update_answer_level(device_id, "experienced")

from fpdf import FPDF  # noqa: E402

pdf = FPDF()
pdf.add_page()
pdf.set_font("Helvetica", size=11)
for line in [
    "DTC P0302",
    "Cylinder 2 Misfire Detected.",
    "Check ignition coil resistance on cylinder 2 before replacing parts.",
    "Spark plug gap: 1.0 mm.",
]:
    pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
pdf.add_page()
pdf.set_font("Helvetica", size=11)
for line in [
    "Rear Differential",
    "Oil capacity: 0.58 L.",
    "Drain plug tightening torque: 54 Nm.",
    "Fill plug tightening torque: 39 Nm.",
    "Use only SAE 90 API GL-5 hypoid gear oil.",
]:
    pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
pdf.add_page()
pdf.set_font("Helvetica", size=11)
for line in [
    "Interior Fuse Box",
    "FUSE 12 | 10A | Instrument cluster",
    "FUSE 15 | 15A | Rear differential control module",
    "FUSE 18 | 25A | Blower motor relay",
]:
    pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
pdf.add_page()
pdf.set_font("Helvetica", size=11)
for line in [
    "Maintenance Schedule",
    "Replace engine oil every 10,000 km or every 12 months.",
    "Inspect the transfer case oil every 40,000 km.",
]:
    pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")

docs_dir = data_dir / "documents"
docs_dir.mkdir(parents=True, exist_ok=True)
manual_path = docs_dir / "tucson-service-manual.pdf"
pdf.output(str(manual_path))

doc_id = device_service.add_document_record(device_id, "tucson-service-manual.pdf", manual_path.name)
job_queue.enqueue(
    "index_document",
    {"document_id": doc_id, "device_id": device_id, "file_path": str(manual_path),
     "original_filename": "tucson-service-manual.pdf", "prior_status": "pending"},
    total=1,
)
job_queue.process_pending_now(timeout=30)
job_queue.wait_until_idle(timeout=10)

sid = chat_service.create_session(device_id, "Rear differential service")
device = device_service.get_device(device_id)
settings_service.update({
    "llm_base_url": "http://127.0.0.1:8791/v1", "llm_model": "fake",
    "embedding_base_url": "http://127.0.0.1:8791/v1", "embedding_model": "fake-embed",
    "ai_mode": "external", "ai_setup_complete": "1",
})
chat_service.ask_in_session(device, sid, "What is the rear differential drain plug torque?")
chat_service.ask_in_session(device, sid, "What about the fill plug?")

did = diagnostic_service.create_session(device_id, "P0302 misfire under load", "Rough idle, flashing check engine light")
diagnostic_service.add_step(did, "Swapped coil 2 with coil 4", result="fail", notes="Misfire followed the coil")
diagnostic_service.add_step(did, "Measured coil 2 primary resistance", reading="0.8 ohms", result="ruled_out")

# A second, non-vehicle device so the dashboard shows a mixed library.
appliance_id = device_service.create_device(
    "Dishwasher", "Bosch", "SHPM88Z75N", "Dishwasher",
    "Kitchen unit, installed 2024.",
)
from fpdf import FPDF as _FPDF  # noqa: E402

pdf2 = _FPDF()
pdf2.add_page()
pdf2.set_font("Helvetica", size=11)
for line in [
    "Troubleshooting",
    "Error code E24: The drain pump is blocked or the drain hose is kinked.",
    "Clean the drain pump filter and check the hose before requesting service.",
    "Error code E15: Water leak detected in the base of the unit. Contact service.",
]:
    pdf2.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
appliance_doc = docs_dir / "dishwasher-manual.pdf"
pdf2.output(str(appliance_doc))
appliance_doc_id = device_service.add_document_record(appliance_id, "dishwasher-manual.pdf", appliance_doc.name)
job_queue.enqueue(
    "index_document",
    {"document_id": appliance_doc_id, "device_id": appliance_id, "file_path": str(appliance_doc),
     "original_filename": "dishwasher-manual.pdf", "prior_status": "pending"},
    total=1,
)
job_queue.process_pending_now(timeout=30)
job_queue.wait_until_idle(timeout=10)

(direct := data_dir / ".seeded").write_text("1")
print(f"Seeded {data_dir}: device={device_id} doc={doc_id} chat={sid} diag={did}")
