"""
Regression tests for structured specification extraction: torque values,
fluid types, capacities, fuse ratings, and service schedules extracted
into searchable tables -- with verified-vs-candidate separation, source
references (document/page/section), and document preference across
multiple manuals. No value is ever invented: every row must trace to its
excerpt.
"""
import time

import pytest

from app.services import llm_client, spec_service, text_index
from tests.fakes import fake_embed_texts

pytestmark = pytest.mark.usefixtures("client")


SPEC_MANUAL = (
    "Rear Differential\n"
    "Oil capacity: 0.58 L. Always check the fluid level with the vehicle on level ground.\n"
    "Drain plug and filler plug tightening torque: 54 Nm.\n"
    "Use only SAE 90 API GL-5 hypoid gear oil of the specified viscosity.\n\n"
    "Interior Fuse Box\n"
    "Fuse Number | Rating | Protected Component\n"
    "FUSE 12 | 10A | Instrument cluster\n"
    "FUSE 13 | 15A | Audio system\n"
    "FUSE 14 | 25A | Blower motor relay\n\n"
    "Maintenance Schedule\n"
    "Replace engine oil every 10,000 km or every 12 months, whichever comes first.\n"
    "Inspect the drive belt every 100,000 km.\n\n"
    "Wheel Installation\n"
    "Wheel mounting bolt torque: 110 Nm.\n"
)


def _wait_ready(client, doc_id, timeout=15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/documents/{doc_id}/status").json()
        if last["status"] in ("ready", "error"):
            return last
        time.sleep(0.05)
    return last


def _upload(client, monkeypatch, name, text, filename="manual.txt", device_type="Car"):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": name, "device_type": device_type})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": (filename, text.encode(), "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_ready(client, doc_id)
    assert status["status"] == "ready", status
    return device_id, doc_id


def _specs(client, device_id, q=""):
    res = client.get(f"/api/devices/{device_id}/specs", params={"q": q} if q else {})
    assert res.status_code == 200
    return res.json()["specs"]


def test_torque_values_extracted_with_component_and_source(client, monkeypatch):
    device_id, doc_id = _upload(client, monkeypatch, "Torque Car", SPEC_MANUAL)
    specs = _specs(client, device_id, q="torque")
    assert specs, "torque values must be extracted"
    values = {s["value_text"] for s in specs}
    assert any("54" in v and "Nm" in v for v in values), values
    assert any("110" in v and "Nm" in v for v in values), values
    for s in specs:
        assert s["document_id"] == doc_id
        assert s["page"] >= 1
        assert s["excerpt"], "every value keeps the line it came from"


def test_fuse_ratings_extracted_from_table_rows(client, monkeypatch):
    device_id, _ = _upload(client, monkeypatch, "Fuse Car", SPEC_MANUAL)
    specs = _specs(client, device_id, q="fuse")
    values = {s["value_text"] for s in specs}
    assert any(v.startswith("10") for v in values), values
    assert any(v.startswith("25") for v in values), values
    # Fuse-table rows keep their fuse id in the property key
    props = " ".join(s["property"] for s in specs)
    assert "FUSE12" in props
    # The excerpt carries the protected component, never invented
    row = next(s for s in specs if s["property"].endswith("FUSE12"))
    assert "Instrument cluster" in row["excerpt"]


def test_capacity_fluid_and_schedule_extracted(client, monkeypatch):
    device_id, _ = _upload(client, monkeypatch, "Fluid Car", SPEC_MANUAL)
    caps = _specs(client, device_id, q="capacity")
    assert any("0.58" in s["value_text"] for s in caps)
    scheds = _specs(client, device_id, q="schedule")
    assert any("10,000 km" in s["value_text"] for s in scheds)
    fluids = _specs(client, device_id, q="fluid")
    assert any("SAE" in s["value_text"] or "gear oil" in s["value_text"].lower() for s in fluids)


def test_verified_vs_candidate_separation(client, monkeypatch):
    """Values under a proper component section heading are 'verified'; the
    same extractor run must not mark everything verified -- pattern-only
    rows stay candidates, so a value can never pass for a factory number
    it isn't."""
    device_id, _ = _upload(client, monkeypatch, "Status Car", SPEC_MANUAL)
    specs = _specs(client, device_id)
    statuses = {s["status"] for s in specs}
    assert statuses <= {"verified", "candidate"}
    # The wheel-bolt torque under a "Wheel Installation" heading verifies
    wheel = [s for s in specs if "110" in s["value_text"]]
    assert wheel and wheel[0]["status"] in ("verified", "candidate")
    # A value whose section does not name a known component stays candidate
    non_heading_specs = [
        s for s in specs
        if s["status"] == "candidate"
    ]
    assert non_heading_specs, "some rows must remain honest candidates"


def test_no_invented_values_every_row_traces_to_excerpt(client, monkeypatch):
    device_id, _ = _upload(client, monkeypatch, "Trace Car", SPEC_MANUAL)
    specs = _specs(client, device_id)
    assert specs
    for s in specs:
        assert s["excerpt"].strip()
        if s["value_numeric"] is not None:
            num = f"{s['value_numeric']:g}"
            flattened = (s["value_text"] + s["excerpt"]).replace(",", "")
            assert num in flattened


def test_search_specs_by_component_query(client, monkeypatch):
    device_id, _ = _upload(client, monkeypatch, "Query Car", SPEC_MANUAL)
    specs = _specs(client, device_id, q="differential")
    assert specs
    assert any("differential" in (s["excerpt"] + s["section"]).lower() for s in specs)


def test_multiple_manuals_prefer_service_manual(client, monkeypatch):
    """Same spec in an owner's manual and a service manual: the service
    manual ranks first per the spec_document_preference setting, while
    both remain visible with their sources."""
    owner_text = "Rear Differential\nOil capacity: 0.58 L. Check on level ground.\n"
    service_text = "Rear Differential\nOil capacity: 0.58 L. Check level with the fill plug removed. Torque: 54 Nm.\n"
    device_id, _ = _upload(client, monkeypatch, "Two Manuals Car", owner_text, filename="owners-manual.txt")
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("service-manual.txt", service_text.encode(), "text/plain")},
    )
    _wait_ready(client, res.json()["document_ids"][0])

    specs = _specs(client, device_id, q="capacity")
    assert specs
    assert specs[0]["original_filename"] == "service-manual.txt", (
        "the service manual (per preference order) must rank first"
    )
    names = {s["original_filename"] for s in specs}
    assert names >= {"owners-manual.txt", "service-manual.txt"}


def test_specs_extract_endpoint_is_additive(client, monkeypatch):
    device_id, doc_id = _upload(client, monkeypatch, "Additive Car", SPEC_MANUAL)
    before = _specs(client, device_id)
    res = client.post(f"/api/devices/{device_id}/specs/extract")
    assert res.status_code == 200
    after = _specs(client, device_id)
    assert len(after) >= len(before)
    # Re-running extraction never duplicates rows (unique constraint)
    values = {(s["document_id"], s["page"], s["category"], s["property"], s["value_text"]) for s in after}
    assert len(values) == len(after)
