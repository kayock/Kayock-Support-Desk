"""
Tests for vehicle profile fields (year/make/model/trim/engine/drivetrain),
OBD-II code normalization, and the Beginner/Experienced answer-level
preference -- all plain CRUD/normalization behavior, no LLM involved.
"""
from app.services import vehicle


# ------------------------------------------------------- OBD normalization --

def test_normalize_single_lowercase_code():
    assert vehicle.normalize_obd_codes("p0302") == ["P0302"]


def test_normalize_multiple_codes_comma_separated():
    assert vehicle.normalize_obd_codes("P0300, P0302, P0420") == ["P0300", "P0302", "P0420"]


def test_normalize_multiple_codes_space_separated():
    assert vehicle.normalize_obd_codes("p0300 p0302 p0420") == ["P0300", "P0302", "P0420"]


def test_normalize_mixed_separators_and_case():
    assert vehicle.normalize_obd_codes("p0300,P0302  p0420") == ["P0300", "P0302", "P0420"]


def test_normalize_deduplicates_preserving_order():
    assert vehicle.normalize_obd_codes("P0302, p0302, P0300") == ["P0302", "P0300"]


def test_normalize_empty_input():
    assert vehicle.normalize_obd_codes("") == []
    assert vehicle.normalize_obd_codes("   ") == []


def test_extract_obd_codes_from_free_text_question():
    codes = vehicle.extract_obd_codes_from_text("why does my car keep throwing P0302 and p0300?")
    assert codes == ["P0302", "P0300"]


def test_extract_obd_codes_from_text_with_no_codes():
    assert vehicle.extract_obd_codes_from_text("the blower motor isn't working") == []


# ---------------------------------------------------------- is_vehicle_device

def test_is_vehicle_device_matches_common_types():
    for t in ("Car", "car", "Vehicle", "Truck", "SUV", "Pickup Truck", "Motorcycle"):
        assert vehicle.is_vehicle_device(t) is True


def test_is_vehicle_device_rejects_appliances():
    for t in ("Dishwasher", "Router", "Television", "", "Washing Machine"):
        assert vehicle.is_vehicle_device(t) is False


# --------------------------------------------------------- vehicle profile --

def test_vehicle_profile_line_full():
    device = {
        "vehicle_year": "2017", "vehicle_make": "Hyundai", "vehicle_model": "Tucson",
        "vehicle_trim": "SEL", "vehicle_engine": "2.0L GDI", "vehicle_drivetrain": "AWD",
    }
    line = vehicle.vehicle_profile_line(device)
    assert "2017 Hyundai Tucson SEL" in line
    assert "Engine: 2.0L GDI" in line
    assert "Drivetrain: AWD" in line


def test_vehicle_profile_line_empty_when_no_fields_set():
    device = {"vehicle_year": "", "vehicle_make": "", "vehicle_model": "", "vehicle_trim": "", "vehicle_engine": "", "vehicle_drivetrain": ""}
    assert vehicle.vehicle_profile_line(device) == ""


def test_vehicle_profile_line_partial_fields():
    device = {"vehicle_year": "", "vehicle_make": "", "vehicle_model": "",
              "vehicle_trim": "", "vehicle_engine": "2.0L GDI", "vehicle_drivetrain": ""}
    line = vehicle.vehicle_profile_line(device)
    assert line == "Engine: 2.0L GDI"


def test_build_vehicle_context_includes_profile_and_applicability_instruction():
    device = {"vehicle_year": "2017", "vehicle_make": "Hyundai", "vehicle_model": "Tucson",
              "vehicle_trim": "", "vehicle_engine": "2.0L GDI", "vehicle_drivetrain": "AWD"}
    ctx = vehicle.build_vehicle_context(device)
    assert "2017 Hyundai Tucson" in ctx
    assert "different engine" in ctx.lower() or "different powertrain" in ctx.lower()


def test_build_vehicle_context_empty_when_no_profile():
    device = {"vehicle_year": "", "vehicle_make": "", "vehicle_model": "", "vehicle_trim": "", "vehicle_engine": "", "vehicle_drivetrain": ""}
    assert vehicle.build_vehicle_context(device) == ""


# --------------------------------------------------------------- API layer --

def test_vehicle_fields_persist_through_create_and_get(client):
    res = client.post(
        "/api/devices",
        data={
            "name": "2017 Hyundai Tucson", "device_type": "Car",
            "vehicle_year": "2017", "vehicle_make": "Hyundai", "vehicle_model": "Tucson",
            "vehicle_trim": "SEL", "vehicle_engine": "2.0L GDI", "vehicle_drivetrain": "AWD",
        },
    )
    device_id = res.json()["id"]

    res = client.get(f"/devices/{device_id}")
    assert res.status_code == 200
    assert "2017 Hyundai Tucson" in res.text
    assert "2.0L GDI" in res.text
    assert "AWD" in res.text


def test_vehicle_fields_persist_through_update(client):
    res = client.post("/api/devices", data={"name": "My Truck", "device_type": "Truck"})
    device_id = res.json()["id"]

    res = client.put(
        f"/api/devices/{device_id}",
        json={
            "name": "My Truck", "device_type": "Truck",
            "vehicle_year": "2020", "vehicle_make": "Ford", "vehicle_model": "F-150",
            "vehicle_engine": "5.0L V8", "vehicle_drivetrain": "4WD",
        },
    )
    assert res.status_code == 200

    res = client.get(f"/devices/{device_id}")
    assert "5.0L V8" in res.text
    assert "4WD" in res.text


def test_vehicle_fields_optional_no_vin_required(client):
    """Creating a vehicle device with zero vehicle fields (including no
    VIN) must work fine -- they're all optional."""
    res = client.post("/api/devices", data={"name": "Some Car", "device_type": "Car"})
    assert res.status_code == 200
    device_id = res.json()["id"]
    res = client.get(f"/devices/{device_id}")
    assert res.status_code == 200
    from app.services import device_service
    device = device_service.get_device(device_id)
    assert device["vin"] == ""


def test_non_vehicle_device_unaffected_by_vehicle_fields(client):
    """A non-vehicle device can still technically have vehicle_* columns
    set (they're just unused columns), but is_vehicle_device must stay
    false based on device_type, and the vehicle UI must not show."""
    res = client.post("/api/devices", data={"name": "Kitchen Dishwasher", "device_type": "Dishwasher"})
    device_id = res.json()["id"]
    res = client.get(f"/devices/{device_id}")
    assert "OBD-II Code" not in res.text
    assert "Quick questions" not in res.text


# ------------------------------------------------------------- answer level --

def test_answer_level_defaults_to_experienced(client):
    res = client.post("/api/devices", data={"name": "Answer Level Test Car", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.get(f"/devices/{device_id}")
    assert 'level-btn active" data-level="experienced"' in res.text


def test_update_answer_level(client):
    res = client.post("/api/devices", data={"name": "Answer Level Test Car 2", "device_type": "Car"})
    device_id = res.json()["id"]

    res = client.post(f"/api/devices/{device_id}/answer-level", json={"answer_level": "beginner"})
    assert res.status_code == 200

    res = client.get(f"/devices/{device_id}")
    assert 'level-btn active" data-level="beginner"' in res.text


def test_answer_level_rejects_invalid_value(client):
    res = client.post("/api/devices", data={"name": "Answer Level Test Car 3", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(f"/api/devices/{device_id}/answer-level", json={"answer_level": "expert"})
    assert res.status_code == 400


def test_changing_answer_level_does_not_trigger_reindex(client, monkeypatch, sample_txt_bytes):
    import time
    from app.services import llm_client
    from tests.fakes import fake_embed_texts

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    res = client.post("/api/devices", data={"name": "Answer Level No Reindex Car", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]

    deadline = time.time() + 5
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    assert status["status"] == "ready"

    res = client.post(f"/api/devices/{device_id}/answer-level", json={"answer_level": "beginner"})
    assert res.status_code == 200

    status_after = client.get(f"/api/documents/{doc_id}/status").json()
    assert status_after["status"] == "ready"  # unchanged -- not marked needs_reindex
