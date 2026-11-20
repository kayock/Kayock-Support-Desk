"""
Tests for the vehicle-aware parts of query_service: prompt construction
differs between Beginner/Experienced, vehicle context is included for
vehicle devices, non-vehicle devices are completely unaffected, and OBD-II
diagnosis stays scoped to the asking device's own manuals.

The LLM itself is always mocked (see tests/fakes.py and the local fakes
below) -- these tests check that Kayock builds the right prompts and
routes retrieval correctly, which is the right testing boundary given the
actual answer quality depends on a real model we don't have in CI.
"""
import time

from app.services import llm_client, query_service, vehicle
from tests.fakes import fake_embed_texts


def _wait_ready(client, doc_id, timeout=5):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = client.get(f"/api/documents/{doc_id}/status").json()
        if status["status"] in ("ready", "error"):
            break
        time.sleep(0.05)
    return status


def _make_vehicle_device_with_manual(client, monkeypatch, name, manual_text, device_type="Car", **vehicle_fields):
    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    data = {"name": name, "device_type": device_type, **vehicle_fields}
    res = client.post("/api/devices", data=data)
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", manual_text.encode(), "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    status = _wait_ready(client, doc_id)
    assert status["status"] == "ready", status
    return device_id


# ----------------------------------------------------- prompt construction --

def test_beginner_and_experienced_produce_different_system_prompts():
    beginner = query_service._build_system_prompt(is_vehicle=True, answer_level="beginner", diagnostic=False)
    experienced = query_service._build_system_prompt(is_vehicle=True, answer_level="experienced", diagnostic=False)
    assert beginner != experienced
    assert "Beginner" in beginner
    assert "Experienced" in experienced
    assert "plain language" in beginner.lower()
    assert "connector" in experienced.lower() or "torque" in experienced.lower()


def test_diagnostic_mode_adds_structured_headings():
    plain = query_service._build_system_prompt(is_vehicle=True, answer_level="experienced", diagnostic=False)
    diagnostic = query_service._build_system_prompt(is_vehicle=True, answer_level="experienced", diagnostic=True)
    assert "Likely area" in diagnostic
    assert "Factory procedure" in diagnostic
    assert "Likely area" not in plain


def test_non_vehicle_prompt_has_no_vehicle_specific_rules():
    appliance_prompt = query_service._build_system_prompt(is_vehicle=False, answer_level="experienced", diagnostic=False)
    assert "backyard mechanic" not in appliance_prompt.lower()
    assert "Beginner" not in appliance_prompt
    assert "Experienced" not in appliance_prompt
    # Base refusal behavior must still be present, unchanged.
    assert query_service.NOT_FOUND_PHRASE in appliance_prompt


def test_vehicle_prompt_includes_applicability_caution():
    vehicle_prompt = query_service._build_system_prompt(is_vehicle=True, answer_level="experienced", diagnostic=False)
    assert "multiple engine, trim, or drivetrain" in vehicle_prompt.lower() or "cannot tell which applies" in vehicle_prompt.lower()


def test_vehicle_prompt_includes_general_guidance_separation_rule():
    vehicle_prompt = query_service._build_system_prompt(is_vehicle=True, answer_level="experienced", diagnostic=False)
    assert "general diagnostic guidance" in vehicle_prompt.lower()
    assert "general automotive knowledge" in vehicle_prompt.lower()


def test_vehicle_prompt_includes_safety_instruction():
    vehicle_prompt = query_service._build_system_prompt(is_vehicle=True, answer_level="experienced", diagnostic=False)
    assert "airbag" in vehicle_prompt.lower() or "srs" in vehicle_prompt.lower()


# --------------------------------------------------------- vehicle context --

def test_vehicle_context_included_in_user_prompt(client, monkeypatch):
    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["system"] = system_prompt
        captured["user"] = user_prompt
        return "Some answer."

    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "Vehicle Context Test Car",
        "Blower motor check procedure: verify fuse 12 before replacing the motor.",
        vehicle_year="2017", vehicle_make="Hyundai", vehicle_model="Tucson", vehicle_engine="2.0L GDI",
    )
    monkeypatch.setattr(llm_client, "chat", fake_chat)

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "blower motor not working"})
    assert res.status_code == 200
    assert "2017 Hyundai Tucson" in captured["user"]
    assert "2.0L GDI" in captured["user"]


def test_appliance_device_gets_no_vehicle_context_in_user_prompt(client, monkeypatch, sample_txt_bytes):
    captured = {}

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        captured["user"] = user_prompt
        return "Some answer."

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    res = client.post("/api/devices", data={"name": "Appliance Context Test", "device_type": "Dishwasher"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    _wait_ready(client, doc_id)

    monkeypatch.setattr(llm_client, "chat", fake_chat)
    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "what does E24 mean"})
    assert res.status_code == 200
    assert "Vehicle:" not in captured["user"]


# ------------------------------------------------------------- OBD diagnose --

import re


def _fake_diagnostic_chat(system_prompt, user_prompt, temperature=0.1):
    """Distinguishes which code is actually being asked about (via the
    'Diagnostic trouble code X.' phrasing diagnose_codes constructs) from
    what happens to be in the retrieved excerpt -- a real LLM would notice
    an excerpt about P0302 doesn't answer a question about P9999, even
    though both strings technically appear somewhere in the prompt."""
    match = re.search(r"Diagnostic trouble code (\S+?)\.", user_prompt)
    asked_code = match.group(1) if match else None
    if asked_code == "P0302" and "Cylinder 2 misfire" in user_prompt:
        return "## Likely area\nIgnition system, cylinder 2.\n## Factory procedure\nCheck coil and plug."
    return query_service.NOT_FOUND_PHRASE


def test_diagnose_endpoint_returns_structured_result_per_code(client, monkeypatch):
    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "OBD Test Car",
        "P0302: Cylinder 2 misfire detected. Check ignition coil and spark plug for cylinder 2.",
    )
    monkeypatch.setattr(llm_client, "chat", _fake_diagnostic_chat)

    res = client.post(f"/api/devices/{device_id}/diagnose", json={"codes": "p0302"})
    assert res.status_code == 200
    data = res.json()
    assert len(data["results"]) == 1
    result = data["results"][0]
    assert result["code"] == "P0302"
    assert "Likely area" in result["answer"]
    assert result["not_found"] is False


def test_diagnose_endpoint_accepts_multiple_codes(client, monkeypatch):
    device_id = _make_vehicle_device_with_manual(
        client, monkeypatch, "OBD Multi Test Car",
        "P0302: Cylinder 2 misfire detected. Check ignition coil and spark plug for cylinder 2.",
    )
    monkeypatch.setattr(llm_client, "chat", _fake_diagnostic_chat)

    res = client.post(f"/api/devices/{device_id}/diagnose", json={"codes": "P0302, P9999"})
    assert res.status_code == 200
    data = res.json()
    codes = [r["code"] for r in data["results"]]
    assert codes == ["P0302", "P9999"]
    # The code the manual doesn't cover should honestly say so, not invent a procedure.
    p9999_result = next(r for r in data["results"] if r["code"] == "P9999")
    assert p9999_result["not_found"] is True


def test_diagnose_endpoint_requires_at_least_one_code(client):
    res = client.post("/api/devices", data={"name": "OBD Empty Test Car", "device_type": "Car"})
    device_id = res.json()["id"]
    res = client.post(f"/api/devices/{device_id}/diagnose", json={"codes": ""})
    assert res.status_code == 400


def test_diagnose_stays_scoped_to_the_current_vehicle(client, monkeypatch):
    """Two vehicles, each with a manual that would answer P0302 differently
    -- diagnosing on vehicle A must never pull vehicle B's content."""
    device_a = _make_vehicle_device_with_manual(
        client, monkeypatch, "Scoped Test Car A",
        "P0302: Cylinder 2 misfire detected on the 2.0L engine. Check ignition coil A.",
    )
    device_b = _make_vehicle_device_with_manual(
        client, monkeypatch, "Scoped Test Car B",
        "P0302: Cylinder 2 misfire detected on the 3.5L V6 engine. Check ignition coil B.",
    )

    def fake_chat(system_prompt, user_prompt, temperature=0.1):
        return user_prompt  # echo back so we can inspect exactly what was retrieved

    monkeypatch.setattr(llm_client, "chat", fake_chat)

    res = client.post(f"/api/devices/{device_a}/diagnose", json={"codes": "P0302"})
    answer_a = res.json()["results"][0]["answer"]
    assert "coil A" in answer_a
    assert "coil B" not in answer_a

    res = client.post(f"/api/devices/{device_b}/diagnose", json={"codes": "P0302"})
    answer_b = res.json()["results"][0]["answer"]
    assert "coil B" in answer_b
    assert "coil A" not in answer_b


# ------------------------------------------------- existing behavior intact --

def test_existing_appliance_ask_flow_unchanged(client, monkeypatch, sample_txt_bytes):
    """The full existing appliance Q&A flow (device dict now passed instead
    of device_id) must behave identically to before this pass."""
    from tests.fakes import fake_chat_e24

    monkeypatch.setattr(llm_client, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_client, "chat", fake_chat_e24)

    res = client.post("/api/devices", data={"name": "Regression Test Dishwasher"})
    device_id = res.json()["id"]
    res = client.post(
        f"/api/devices/{device_id}/documents",
        files={"files": ("manual.txt", sample_txt_bytes, "text/plain")},
    )
    doc_id = res.json()["document_ids"][0]
    _wait_ready(client, doc_id)

    res = client.post(f"/api/devices/{device_id}/ask", json={"question": "What does error E24 mean?"})
    data = res.json()
    assert data["not_found"] is False
    assert "drain pump" in data["answer"].lower()
