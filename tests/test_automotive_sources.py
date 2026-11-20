"""
Tests for app.services.automotive_sources: VIN validation/normalization,
VIN decode (NHTSA vPIC), recalls (NHTSA Recalls API), generic DTC lookup
(local dictionary), caching, force-refresh, disabled-source behavior, and
failure handling. All external HTTP is mocked at the single choke point
(_get_json) -- no test here depends on internet availability.
"""
import time

from app.services import automotive_sources as auto
from app.services import settings_service, vehicle

VALID_VIN = "5UXWX7C5XBA123456"  # well-formed (17 chars, no I/O/Q) -- not a real lookup target in mocked tests


# ------------------------------------------------------- VIN validation ----

def test_normalize_vin_uppercases_and_strips():
    assert vehicle.normalize_vin("  5uxwx7c5xba123456  ") == "5UXWX7C5XBA123456"


def test_is_valid_vin_accepts_well_formed_vin():
    assert vehicle.is_valid_vin(VALID_VIN) is True


def test_is_valid_vin_rejects_disallowed_letters():
    # I, O, Q are never valid VIN characters
    assert vehicle.is_valid_vin("5UXWX7C5IBA123456") is False
    assert vehicle.is_valid_vin("5UXWX7C5OBA123456") is False
    assert vehicle.is_valid_vin("5UXWX7C5QBA123456") is False


def test_is_valid_vin_rejects_wrong_length():
    assert vehicle.is_valid_vin("SHORTVIN") is False
    assert vehicle.is_valid_vin(VALID_VIN + "X") is False
    assert vehicle.is_valid_vin("") is False


def test_decode_vin_invalid_format_never_calls_network(monkeypatch):
    calls = []
    monkeypatch.setattr(auto, "_get_json", lambda *a, **k: calls.append(1) or (None, "should not be called"))
    result = auto.decode_vin("not-a-real-vin")
    assert result["ok"] is False
    assert "valid" in result["error"].lower()
    assert calls == []


# ------------------------------------------------------------- VIN decode --

def _fake_vpic_response(**overrides):
    record = {
        "ErrorCode": "0", "ErrorText": "0 - VIN decoded clean.",
        "Make": "HYUNDAI", "Model": "TUCSON", "ModelYear": "2017", "Trim": "SEL",
        "DisplacementL": "2.0", "EngineCylinders": "4", "FuelTypePrimary": "Gasoline",
        "DriveType": "FWD/Front-Wheel Drive", "VehicleType": "MULTIPURPOSE PASSENGER VEHICLE (MPV)",
        "BodyClass": "Sport Utility Vehicle", "PlantCountry": "UNITED STATES (USA)",
    }
    record.update(overrides)
    return {"Count": 1, "Results": [record]}, None


def test_decode_vin_success_normalizes_fields(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_vin_lookup_enabled": "1"})
    monkeypatch.setattr(auto, "_get_json", lambda url, timeout=6: _fake_vpic_response())

    result = auto.decode_vin(VALID_VIN)
    assert result["ok"] is True
    assert result["source"] == "NHTSA vPIC"
    suggested = result["suggested_fields"]
    assert suggested["vehicle_year"] == "2017"
    assert suggested["vehicle_make"] == "HYUNDAI"
    assert suggested["vehicle_model"] == "TUCSON"
    assert suggested["vehicle_trim"] == "SEL"
    assert "2.0L" in suggested["vehicle_engine"]
    assert suggested["vehicle_drivetrain"] == "FWD/Front-Wheel Drive"


def test_decode_vin_no_usable_data_is_a_clean_failure(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_vin_lookup_enabled": "1"})
    monkeypatch.setattr(auto, "_get_json", lambda url, timeout=6: ({"Count": 1, "Results": [{"ErrorCode": "6"}]}, None))
    result = auto.decode_vin("1G1ZT53806F109149")  # distinct VIN, never cached by another test
    assert result["ok"] is False
    assert "did not return usable" in result["error"].lower()


def test_decode_vin_network_failure_is_plain_english(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_vin_lookup_enabled": "1"})
    monkeypatch.setattr(auto, "_get_json", lambda url, timeout=6: (None, "Could not reach the online source (no internet connection?)."))
    result = auto.decode_vin("JH4KA7561PC008269")  # distinct VIN, never cached by another test
    assert result["ok"] is False
    assert "could not reach" in result["error"].lower()
    assert "traceback" not in result["error"].lower()
    assert "exception" not in result["error"].lower()


def test_decode_vin_disabled_never_calls_network(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "0"})
    calls = []
    monkeypatch.setattr(auto, "_get_json", lambda *a, **k: calls.append(1) or (None, "unused"))
    result = auto.decode_vin(VALID_VIN)
    assert result["ok"] is False
    assert "turned off" in result["error"].lower()
    assert calls == []
    settings_service.update({"online_sources_enabled": "1"})  # restore for later tests


def test_decode_vin_cache_used_on_second_call(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_vin_lookup_enabled": "1"})
    calls = []

    def fake_get_json(url, timeout=6):
        calls.append(url)
        return _fake_vpic_response()

    monkeypatch.setattr(auto, "_get_json", fake_get_json)

    vin = "1FA6P8TD5M5100002"
    first = auto.decode_vin(vin)
    second = auto.decode_vin(vin)
    assert len(calls) == 1  # second call served from cache
    assert first["ok"] is True
    assert second["ok"] is True
    assert second["cache_hit"] is True


def test_decode_vin_force_refresh_bypasses_cache(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_vin_lookup_enabled": "1"})
    calls = []

    def fake_get_json(url, timeout=6):
        calls.append(url)
        return _fake_vpic_response()

    monkeypatch.setattr(auto, "_get_json", fake_get_json)

    vin = "1FA6P8TD5M5100003"
    auto.decode_vin(vin)
    auto.decode_vin(vin, force_refresh=True)
    assert len(calls) == 2


# ---------------------------------------------------------------- recalls --

def test_get_recalls_success(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_recalls_enabled": "1"})

    def fake_get_json(url, timeout=6):
        return {
            "Count": 1,
            "results": [
                {
                    "NHTSACampaignNumber": "19V182000", "Component": "AIR BAGS", "Summary": "Test summary.",
                    "Consequence": "Test consequence.", "Remedy": "Test remedy.", "Manufacturer": "Test Mfr",
                }
            ],
        }, None

    monkeypatch.setattr(auto, "_get_json", fake_get_json)
    result = auto.get_recalls("Hyundai", "Tucson", "2017")
    assert result["ok"] is True
    assert result["vin_specific"] is False
    assert len(result["recalls"]) == 1
    assert result["recalls"][0]["campaign_number"] == "19V182000"


def test_get_recalls_no_recalls_found_uses_correct_wording(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_recalls_enabled": "1"})
    monkeypatch.setattr(auto, "_get_json", lambda url, timeout=6: ({"Count": 0, "results": []}, None))
    result = auto.get_recalls("Hyundai", "Tucson", "2099")
    assert result["ok"] is True
    assert result["recalls"] == []


def test_get_recalls_timeout_is_plain_english(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_recalls_enabled": "1"})
    monkeypatch.setattr(auto, "_get_json", lambda url, timeout=6: (None, "The online source took too long to respond."))
    result = auto.get_recalls("Volvo", "XC90", "2013")  # distinct Y/M/M, never cached by another test
    assert result["ok"] is False
    assert "too long" in result["error"].lower()


def test_get_recalls_missing_vehicle_info(client):
    result = auto.get_recalls("", "", "")
    assert result["ok"] is False
    assert "year, make, and model" in result["error"].lower()


def test_get_recalls_disabled(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_recalls_enabled": "0"})
    calls = []
    monkeypatch.setattr(auto, "_get_json", lambda *a, **k: calls.append(1) or (None, "unused"))
    result = auto.get_recalls("Hyundai", "Tucson", "2017")
    assert result["ok"] is False
    assert calls == []
    settings_service.update({"online_recalls_enabled": "1"})


def test_recalls_cache_used_then_force_refresh(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_recalls_enabled": "1"})
    calls = []

    def fake_get_json(url, timeout=6):
        calls.append(url)
        return {"Count": 0, "results": []}, None

    monkeypatch.setattr(auto, "_get_json", fake_get_json)
    auto.get_recalls("Toyota", "Corolla", "2020")
    auto.get_recalls("Toyota", "Corolla", "2020")
    assert len(calls) == 1
    auto.get_recalls("Toyota", "Corolla", "2020", force_refresh=True)
    assert len(calls) == 2


# ------------------------------------------------------------- DTC lookup --

def test_lookup_dtc_known_code():
    result = auto.lookup_dtc("p0302")
    assert result["ok"] is True
    assert result["code"] == "P0302"
    assert "misfire" in result["definition"].lower()
    assert result["category"] == "Powertrain"


def test_lookup_dtc_unknown_code():
    result = auto.lookup_dtc("P9999")
    assert result["ok"] is False
    assert result["definition"] is None
    # Still categorized by family even if the specific code isn't listed.
    assert result["category"] == "Powertrain"


def test_lookup_dtc_never_touches_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("lookup_dtc must never make a network call")

    monkeypatch.setattr(auto, "_get_json", boom)
    auto.lookup_dtc("P0302")  # must not raise


# --------------------------------------------------- endpoint-level tests --

def test_decode_vin_endpoint_requires_saved_vin(client):
    res = client.post("/api/devices", data={"name": "No VIN Car", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(f"/api/devices/{device_id}/decode-vin", json={})
    assert res.status_code == 400


def test_decode_vin_endpoint_does_not_silently_overwrite_profile(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_vin_lookup_enabled": "1"})
    res = client.post(
        "/api/devices",
        data={
            "name": "Conflict Test Car", "device_type": "Car", "vin": VALID_VIN,
            "vehicle_year": "2015", "vehicle_make": "Honda", "vehicle_model": "Civic",
        },
    )
    device_id = res.json()["id"]
    before = client.get(f"/devices/{device_id}").text

    monkeypatch.setattr(auto, "_get_json", lambda url, timeout=6: _fake_vpic_response())  # decodes to Hyundai Tucson 2017
    res = client.post(f"/api/devices/{device_id}/decode-vin", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    conflict_fields = {c["field"] for c in data["conflicts"]}
    assert "vehicle_make" in conflict_fields
    assert "vehicle_model" in conflict_fields

    after = client.get(f"/devices/{device_id}").text
    assert before == after
    assert "Honda" in after
    assert "Hyundai" not in after


def test_recalls_endpoint_returns_correct_no_recall_message(client, monkeypatch):
    settings_service.update({"online_sources_enabled": "1", "online_recalls_enabled": "1"})
    res = client.post(
        "/api/devices",
        data={"name": "No Recall Car", "device_type": "Car", "vehicle_year": "2099", "vehicle_make": "Nomake", "vehicle_model": "Nomodel"},
    )
    device_id = res.json()["id"]
    monkeypatch.setattr(auto, "_get_json", lambda url, timeout=6: ({"Count": 0, "results": []}, None))
    res = client.post(f"/api/devices/{device_id}/recalls", json={})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["recalls"] == []


def test_diagnose_includes_separately_labeled_dtc_lookup(client, monkeypatch):
    from app.services import llm_client, query_service
    from tests.fakes import fake_embed_texts

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        return "## Likely area\nIgnition.\n## Factory procedure\nCheck the coil."

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    res = client.post("/api/devices", data={"name": "DTC Label Test Car", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", b"P0302: Cylinder 2 Misfire. Check the coil.", "text/plain")},
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

    res = client.post(f"/api/devices/{device_id}/diagnose", json={"codes": "P0302"})
    result = res.json()["results"][0]
    assert result["dtc_lookup"]["definition"] == "Cylinder 2 Misfire Detected"
    assert result["dtc_lookup"]["source"] != "manual"
    assert result["answer"] != result["dtc_lookup"]["definition"]


def test_manual_answering_unaffected_by_online_sources_disabled(client, monkeypatch, sample_txt_bytes):
    """Turning online automotive sources off must not touch normal
    manual-backed Q&A in any way -- confirms full offline usefulness."""
    from app.services import llm_client
    from tests.fakes import fake_chat_e24, fake_embed_texts

    settings_service.update({"online_sources_enabled": "0"})
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_client, "chat", fake_chat_e24)

    res = client.post("/api/devices", data={"name": "Offline Answering Test Device"})
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

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What does error E24 mean?"})
    data = res.json()
    assert data["not_found"] is False
    assert "drain pump" in data["answer"].lower()
    settings_service.update({"online_sources_enabled": "1"})


def test_non_vehicle_device_page_has_no_online_automotive_ui(client):
    res = client.post("/api/devices", data={"name": "Regression Appliance", "device_type": "Router"})
    device_id = res.json()["id"]
    res = client.get(f"/devices/{device_id}")
    # The functional Online Vehicle Information section (Decode VIN / Check
    # Recalls buttons, OBD box, quick questions) must not render at all for
    # a non-vehicle device -- checked by id/heading, not just substring,
    # since a collapsed "Vehicle details (optional)" edit field is
    # harmlessly present on every device type (same as Year/Make/etc.).
    assert 'id="decodeVinBtn"' not in res.text
    assert 'id="checkRecallsBtn"' not in res.text
    assert "Online Vehicle Information" not in res.text
    assert "OBD-II Code" not in res.text
    assert "Quick questions" not in res.text


def test_uploaded_document_content_never_sent_to_external_lookup(client, monkeypatch, sample_txt_bytes):
    """decode_vin/get_recalls must only ever be called with a VIN or
    year/make/model -- never manual text. This test captures every URL
    Kayock would have sent externally and asserts none of it contains the
    manual's distinctive content."""
    from app.services import llm_client
    from tests.fakes import fake_embed_texts

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    settings_service.update({"online_sources_enabled": "1", "online_vin_lookup_enabled": "1", "online_recalls_enabled": "1"})

    captured_urls = []

    def fake_get_json(url, timeout=6):
        captured_urls.append(url)
        return _fake_vpic_response()

    monkeypatch.setattr(auto, "_get_json", fake_get_json)

    res = client.post(
        "/api/devices",
        data={
            "name": "Privacy Test Car", "device_type": "Car", "vin": VALID_VIN,
            "vehicle_year": "2017", "vehicle_make": "Hyundai", "vehicle_model": "Tucson",
        },
    )
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", b"Secret drain pump filter cleaning procedure page 42", "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    deadline = time.time() + 5
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)

    client.post(f"/api/devices/{device_id}/decode-vin", json={})
    client.post(f"/api/devices/{device_id}/recalls", json={})

    for url in captured_urls:
        assert "drain" not in url.lower()
        assert "filter" not in url.lower()
        assert "manual" not in url.lower()
        assert "42" not in url
